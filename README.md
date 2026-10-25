# OSU Purchasing Card Audit

**Live Application:** https://pcard-audit-ka3gxcfzzcshne6lw2hgqy.streamlit.app

## Project Overview

This project is a web-based purchasing card (P-card) audit application developed as part of the **AI in Accounting and Business** course at the University of Southern Denmark (SDU).

The application uses Python, Streamlit, SQLite, and Google Gemini to help auditors explore purchasing card transactions, analyze spending patterns, and identify potential violations of internal purchasing policies.

The dataset contains **489,178 transactions** from 2010–2014, with total recorded spending of approximately **$164.4 million**.

## Application Features

### 1. Ask the Database

This tab allows auditors to interact with the database using natural-language questions.

- Enter a question in plain English.
- Google Gemini generates a corresponding SQL query.
- The application executes the query against the SQLite database.
- Results are displayed in a table or summary.
- Users can review the generated SQL and download results as CSV.

Example questions:

- How many transactions are in the database?
- What was the total spending by year?
- Which vendors received the highest total payments in 2014?

### 2. Prohibited Purchases Dashboard

This dashboard helps auditors identify transactions that may require further investigation under OSU purchasing card policies.

Users can:

- Select one or more years.
- Search transaction descriptions using keywords.
- Search vendor names using keywords.
- Combine Description and Vendor filters.
- Review matching transactions and associated spending.

Keyword matches indicate **potential policy exceptions**, not confirmed violations. Further investigation is required before drawing conclusions.

## Technologies Used

- **Python** – Application development and data processing
- **Streamlit** – Interactive web interface
- **SQLite** – Transaction database
- **Google Gemini API** – Natural-language-to-SQL generation
- **Pandas** – Data analysis and result presentation
- **SQLGlot** – SQL parsing and validation

## Security

The Gemini API key is managed through Streamlit Secrets and is not included in the source code.

The application restricts database queries to read-only operations.

## Running the Application Locally

1. Clone the repository.
2. Install the dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Configure your Gemini API key using Streamlit Secrets.
4. Start the application:

   ```bash
   streamlit run app.py
   ```

The application initializes its local SQLite database from the included `pcards.zip` file.

## Academic Context

Developed for the *Analytics Mindset – P-card* case study as part of the AI in Accounting and Business course at SDU.

The application is intended for educational and audit-analysis purposes. Flagged transactions should not be interpreted as evidence of fraud or misconduct without further examination.
