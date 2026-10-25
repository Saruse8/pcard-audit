# OSU Purchasing Card Audit

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
- Search vendor names
