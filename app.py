
import re
import sqlite3
import time
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st
import sqlglot
from sqlglot import exp
from google import genai
from google.genai import errors, types


# ============================================================
# CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="OSU Purchasing Card Audit",
    page_icon="📊",
    layout="wide",
)

DATABASE = Path(__file__).parent / "pcards.db"
DATABASE_ZIP = Path(__file__).parent / "pcards.zip"


def ensure_database():
    if DATABASE.is_file():
        return

    if not DATABASE_ZIP.is_file():
        raise FileNotFoundError(
            "Neither pcards.db nor pcards.zip was found."
        )

    with zipfile.ZipFile(DATABASE_ZIP, "r") as archive:
        with archive.open("pcards.db") as source:
            with DATABASE.open("wb") as target:
                import shutil
                shutil.copyfileobj(source, target)


MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.1-flash-lite",
]

MAX_RETRIES_PER_MODEL = 2
MAX_QUERY_SECONDS = 15
MAX_RESULT_ROWS = 1000

UNSUPPORTED_MESSAGE = "Question cannot be answered"

ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "min", "max",
    "round", "abs", "lower", "upper",
    "trim", "ltrim", "rtrim", "length",
    "substr", "substring", "coalesce",
    "ifnull", "nullif", "date", "time",
    "datetime", "strftime", "julianday",
    "cast", "typeof", "total",
}

SCHEMA = """
Table: pcards

Year INTEGER
Month TEXT
FullName TEXT
ID TEXT
AgencyNumber TEXT
AgencyName TEXT
CardholderLastName TEXT
CardholderFirstInitial TEXT
Description TEXT
Amount REAL
Vendor TEXT
TransactionDate TEXT
PostedDate TEXT
MCC TEXT
"""


# ============================================================
# DATABASE
# ============================================================

def open_readonly_database():
    ensure_database()
    if not DATABASE.is_file():
        raise FileNotFoundError(
            "pcards.db was not found beside app.py."
        )

    uri = DATABASE.resolve().as_uri() + "?mode=ro"

    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only = ON")

    return connection


@st.cache_data
def get_database_summary():
    sql = """
        SELECT
            Year,
            COUNT(*) AS Transactions,
            SUM(Amount) AS Total_Spending
        FROM pcards
        GROUP BY Year
        ORDER BY Year
    """

    with open_readonly_database() as connection:
        return pd.read_sql_query(sql, connection)


@st.cache_data
def search_transactions(years, description, vendor):
    conditions = []
    params = []

    if years:
        placeholders = ",".join("?" for _ in years)
        conditions.append(f"Year IN ({placeholders})")
        params.extend(years)

    if description.strip():
        conditions.append(
            "LOWER(COALESCE(Description, '')) LIKE ?"
        )
        params.append(
            f"%{description.strip().lower()}%"
        )

    if vendor.strip():
        conditions.append(
            "LOWER(COALESCE(Vendor, '')) LIKE ?"
        )
        params.append(f"%{vendor.strip().lower()}%")

    where_sql = (
        "WHERE " + " AND ".join(conditions)
        if conditions else ""
    )

    sql = f"""
        SELECT
            Year,
            Month,
            FullName,
            AgencyName,
            Description,
            Amount,
            Vendor,
            TransactionDate,
            MCC
        FROM pcards
        {where_sql}
        ORDER BY Year DESC, TransactionDate DESC
    """

    with open_readonly_database() as connection:
        return pd.read_sql_query(
            sql,
            connection,
            params=params,
        )


# ============================================================
# SQL VALIDATION
# ============================================================

def validate_sql(sql):
    sql = sql.strip().rstrip(";").strip()

    if not sql:
        raise ValueError("AI returned an empty query.")

    statements = sqlglot.parse(sql, read="sqlite")

    if len(statements) != 1:
        raise ValueError(
            "Only one SQL statement is permitted."
        )

    statement = statements[0]

    if not isinstance(statement, exp.Select):
        raise ValueError(
            "Only SELECT statements are permitted."
        )

    if statement.args.get("with_") is not None:
        raise ValueError(
            "Common table expressions are not supported."
        )

    tables = list(statement.find_all(exp.Table))

    if not tables:
        raise ValueError(
            "The query must reference the pcards table."
        )

    for table in tables:
        if (
            table.name.lower() != "pcards"
            or table.db
            or table.catalog
        ):
            raise ValueError(
                "Only the pcards table may be accessed."
            )

    # Prevent subqueries and UNION-style combinations.
    select_nodes = list(statement.find_all(exp.Select))

    if len(select_nodes) != 1:
        raise ValueError(
            "Nested SELECT queries are not supported."
        )

    # Restrict functions to common analytical operations.
    for node in statement.walk():
        if isinstance(node, exp.Func):
            function_name = node.sql_name().lower()

            if isinstance(node, exp.Anonymous):
                function_name = node.name.lower()

            if function_name not in ALLOWED_FUNCTIONS:
                raise ValueError(
                    f"SQL function not allowed: {function_name}"
                )

    return sql


def format_sql(sql):
    try:
        formatted = sqlglot.transpile(
            sql,
            read="sqlite",
            write="sqlite",
            pretty=True,
        )[0]

        return formatted.rstrip(";") + ";"

    except Exception:
        return sql.rstrip(";") + ";"


# ============================================================
# GEMINI
# ============================================================

class AIUnavailableError(Exception):
    pass


def request_gemini(prompt):
    try:
        api_key = st.secrets["GEMINI_API_KEY"]
    except Exception as error:
        raise AIUnavailableError(
            "Gemini API key is missing."
        ) from error

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=20000,
        ),
    )

    for model_name in MODELS:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )

                if response.text and response.text.strip():
                    return response.text, model_name

                break

            except errors.APIError as error:
                status = getattr(error, "code", None)

                if status not in (
                    429, 500, 502, 503, 504
                ):
                    raise AIUnavailableError(
                        "The AI request was rejected. "
                        "Check the API configuration."
                    ) from error

                if attempt == 0:
                    time.sleep(1)

            except (TimeoutError, ConnectionError):
                break

    raise AIUnavailableError(
        "The AI models are temporarily unavailable. "
        "Please try again later."
    )


# ============================================================
# NATURAL LANGUAGE TO SQL
# ============================================================

def generate_sql(question):
    prompt = f"""
You are a SQLite analyst for an Oklahoma State
University purchasing card audit application.

Convert the user question into a single SQLite
SELECT statement.

DATABASE SCHEMA:

{SCHEMA}

RULES:

1. Return only SQL, without Markdown or explanations.
2. Use only the pcards table.
3. Generate one SELECT statement.
4. Do not use WITH, UNION, subqueries, or JOIN.
5. Never modify the database.
6. COUNT(*) counts transactions.
7. SUM(Amount) calculates total spending.
8. AVG(Amount) calculates average transaction amount.
9. Use FullName for employees.
10. Use Vendor for suppliers.
11. Use AgencyName for agencies.
12. Use Year for year filtering.
13. Use clear aliases, such as:
    Total_Transactions
    Total_Spending
    Average_Amount
14. For top-N questions, use ORDER BY and LIMIT.
15. For detailed transaction lists, use LIMIT 100.
16. Use only the provided columns.
17. Do not invent information.
18. If the question cannot be answered using
    these database columns, return exactly:
    UNSUPPORTED_QUESTION
19. Return SQLite-compatible syntax.

USER QUESTION:

{question}
"""

    response, model_used = request_gemini(prompt)

    response = response.strip()

    response = re.sub(
        r"^```(?:sql)?\s*",
        "",
        response,
        flags=re.IGNORECASE,
    )

    response = re.sub(
        r"\s*```$",
        "",
        response,
    ).strip()

    if response == "UNSUPPORTED_QUESTION":
        return None, model_used

    sql = validate_sql(response)

    return sql, model_used


# ============================================================
# EXECUTE AI QUERY
# ============================================================

def execute_ai_query(sql):
    start = time.monotonic()

    def progress_handler():
        return int(
            time.monotonic() - start > MAX_QUERY_SECONDS
        )

    def authorizer(action, arg1, arg2, db_name, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK

        if action == sqlite3.SQLITE_READ:
            if (
                str(arg1).lower() == "pcards"
                and db_name in (None, "main")
            ):
                return sqlite3.SQLITE_OK

            return sqlite3.SQLITE_DENY

        if action == sqlite3.SQLITE_FUNCTION:
            function_name = str(arg2 or "").lower()

            if function_name in ALLOWED_FUNCTIONS:
                return sqlite3.SQLITE_OK

        return sqlite3.SQLITE_DENY

    with open_readonly_database() as connection:
        connection.set_authorizer(authorizer)
        connection.set_progress_handler(
            progress_handler,
            1000,
        )

        try:
            bounded_sql = (
                "SELECT * FROM ("
                + sql.rstrip(";")
                + f") LIMIT {MAX_RESULT_ROWS}"
            )

            return pd.read_sql_query(
                bounded_sql,
                connection,
            )

        finally:
            connection.set_progress_handler(None, 0)
            connection.set_authorizer(None)


# ============================================================
# DISPLAY HELPERS
# ============================================================

def is_money_column(name):
    name = name.lower()

    return any(
        term in name
        for term in (
            "amount", "spending", "cost",
            "price", "expense", "average",
            "total_paid", "sales",
        )
    )


def friendly_name(name):
    return name.replace("_", " ")


def format_value(value, money=False):
    if pd.isna(value):
        return "N/A"

    number = float(value)

    if money:
        return f"${number:,.2f}"

    if number.is_integer():
        return f"{int(number):,}"

    return f"{number:,.2f}"


def display_ai_results(results):
    if results.empty:
        st.info("No matching records were found.")
        return

    if (
        len(results) == 1
        and len(results.columns) == 1
        and pd.api.types.is_numeric_dtype(
            results.iloc[:, 0]
        )
    ):
        column = results.columns[0]

        st.metric(
            friendly_name(column),
            format_value(
                results.iloc[0, 0],
                money=is_money_column(column),
            ),
        )
        return

    display = results.copy()

    for column in display.columns:
        if (
            is_money_column(column)
            and pd.api.types.is_numeric_dtype(
                display[column]
            )
        ):
            display[column] = display[column].map(
                lambda value: (
                    format_value(value, money=True)
                    if pd.notna(value) else ""
                )
            )

    display.columns = [
        friendly_name(column)
        for column in display.columns
    ]

    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# APP HEADER
# ============================================================

st.title("OSU Purchasing Card Audit")

st.write(
    "Explore purchasing card transactions using "
    "natural-language questions or search for "
    "potentially prohibited purchases."
)

ensure_database()

if not DATABASE.is_file():
    st.error(
        "Database not found. Place pcards.db "
        "in the same folder as app.py."
    )
    st.stop()

tab_ai, tab_dashboard = st.tabs([
    "Ask the Database",
    "Prohibited Purchases Dashboard",
])


# ============================================================
# TAB 1: ASK THE DATABASE
# ============================================================

with tab_ai:
    st.subheader("Ask the Database")

    st.write(
        "Ask a question in English. "
        "The application generates SQL and "
        "runs it against the purchasing card database."
    )

    with st.form("ai_question_form"):
        question = st.text_input(
            "Your question",
            placeholder=(
                "Which 10 employees had the highest "
                "total spending in 2014?"
            ),
        )

        submitted = st.form_submit_button(
            "Run Question",
            type="primary",
        )

    if submitted:
        if not question.strip():
            st.warning("Please enter a question.")

        else:
            try:
                with st.spinner(
                    "Generating and running your query..."
                ):
                    sql, model_used = generate_sql(
                        question.strip()
                    )

                    if sql is None:
                        results = None
                    else:
                        results = execute_ai_query(sql)

                st.caption(
                    f"AI model used: {model_used}"
                )

                if sql is None:
                    st.warning(UNSUPPORTED_MESSAGE)

                    st.info(
                        "The available purchasing card "
                        "data does not contain the "
                        "information needed to answer "
                        "this question."
                    )

                else:
                    st.success(
                        "Query completed successfully."
                    )

                    display_ai_results(results)

                    with st.expander(
                        "View Generated SQL"
                    ):
                        st.code(
                            format_sql(sql),
                            language="sql",
                        )

                    csv_data = results.to_csv(
                        index=False
                    ).encode("utf-8")

                    st.download_button(
                        "Download Results as CSV",
                        data=csv_data,
                        file_name="ai_query_results.csv",
                        mime="text/csv",
                    )

            except AIUnavailableError as error:
                st.error(str(error))

            except ValueError as error:
                st.error(
                    "The generated SQL did not pass "
                    "the safety checks."
                )
                st.caption(str(error))

            except sqlite3.Error as error:
                st.error(
                    "The SQL query could not be executed."
                )
                st.caption(str(error))

            except Exception as error:
                st.error(
                    "The question could not be processed."
                )
                st.caption(str(error))


# ============================================================
# TAB 2: PROHIBITED PURCHASES DASHBOARD
# ============================================================

with tab_dashboard:
    st.subheader(
        "Prohibited Purchases Dashboard"
    )

    st.write(
        "Search transactions for potential policy "
        "exceptions. Keyword matches require "
        "further investigation and are not proof "
        "of prohibited purchases."
    )

    st.info("How to use: Select one or more years, enter keywords in the Description and/or Vendor fields, and click Search Transactions. When both fields are used, transactions must match both conditions. Review the results to identify purchases requiring further investigation.")

    try:
        summary = get_database_summary()

        total_transactions = int(
            summary["Transactions"].sum()
        )

        total_spending = float(
            summary["Total_Spending"].sum()
        )

        years_available = sorted(
            summary["Year"]
            .dropna()
            .astype(int)
            .tolist()
        )

        col1, col2 = st.columns(2)

        with col1:
            st.metric(
                "Total Transactions",
                f"{total_transactions:,}",
            )

        with col2:
            st.metric(
                "Total Spending",
                f"${total_spending:,.2f}",
            )

        st.subheader("Transactions by Year")

        yearly_display = summary.copy()

        yearly_display["Total_Spending"] = (
            yearly_display["Total_Spending"].map(
                lambda value: f"${value:,.2f}"
            )
        )

        yearly_display.columns = [
            "Year",
            "Transactions",
            "Total Spending",
        ]

        st.dataframe(
            yearly_display,
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("Search Transactions")

        with st.form("transaction_search_form"):
            selected_years = st.multiselect(
                "Select Years",
                options=years_available,
                default=years_available,
            )

            description_filter = st.text_input(
                "Description Contains",
                placeholder="For example: alcohol",
            )

            vendor_filter = st.text_input(
                "Vendor Contains",
                placeholder="For example: restaurant",
            )

            search_submitted = st.form_submit_button(
                "Search Transactions",
                type="primary",
            )

        if search_submitted:
            if not selected_years:
                st.warning(
                    "Please select at least one year."
                )

            else:
                with st.spinner(
                    "Searching transactions..."
                ):
                    matching = search_transactions(
                        tuple(selected_years),
                        description_filter,
                        vendor_filter,
                    )

                matches = len(matching)

                matched_spending = (
                    matching["Amount"].sum()
                    if matches else 0
                )

                result1, result2 = st.columns(2)

                with result1:
                    st.metric(
                        "Matching Transactions",
                        f"{matches:,}",
                    )

                with result2:
                    st.metric(
                        "Matching Spending",
                        f"${matched_spending:,.2f}",
                    )

                if not matches:
                    st.info(
                        "No matching transactions were found."
                    )

                else:
                    display = matching.copy()

                    display["Amount"] = (
                        display["Amount"].map(
                            lambda value: (
                                f"${value:,.2f}"
                                if pd.notna(value)
                                else ""
                            )
                        )
                    )

                    st.dataframe(
                        display,
                        use_container_width=True,
                        hide_index=True,
                    )

                    csv_data = matching.to_csv(
                        index=False
                    ).encode("utf-8")

                    st.download_button(
                        "Download Matching Transactions",
                        data=csv_data,
                        file_name=(
                            "potential_prohibited_purchases.csv"
                        ),
                        mime="text/csv",
                    )

    except Exception as error:
        st.error(
            "The dashboard could not load the database."
        )
        st.caption(str(error))
