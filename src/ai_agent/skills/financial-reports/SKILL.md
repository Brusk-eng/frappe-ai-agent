---
name: financial-reports
description: Answer profit, loss, revenue, expense, balance, cash and receivable questions from ERPNext's own financial reports, for a named company and period.
risk: read-only
governs: run_report:Profit and Loss Statement, run_report:Balance Sheet, run_report:Cash Flow, run_report:Trial Balance, run_report:General Ledger, run_report:Gross Profit, run_report:Accounts Receivable, run_report:Accounts Payable
---

<!-- records:
Company:
  - name
  - default_currency
Fiscal Year:
  - name
  - year_start_date
  - year_end_date
-->

# Financial Reports

A financial figure is only right if it comes from the accounting reports, which read the general
ledger. Adding up invoices is not a profit figure: purchases are not the cost of goods sold, and
journal entries, depreciation, taxes and returns are left out.

## Allowed Tools

* `list_documents`: find the companies and fiscal years.
* `run_report`: run the report the question needs.

Never compute a profit, margin, balance or total by summing invoices with `aggregate_documents`.

## Which report

| Question | `report_name` | Row to read |
| --- | --- | --- |
| Profit, loss, net income, revenue, expenses | `Profit and Loss Statement` | `Profit for the year`, `Total Income (Credit)`, `Total Expense (Debit)` |
| Assets, liabilities, equity, net worth | `Balance Sheet` | the total rows for each section |
| Cash in, cash out, cash position | `Cash Flow` | the net change and closing rows |
| Account balances | `Trial Balance` | the account rows |
| Gross profit by invoice or item | `Gross Profit` | the total row |
| Who owes us, overdue invoices | `Accounts Receivable` | one row per outstanding invoice |
| What we owe | `Accounts Payable` | one row per outstanding invoice |
| Every entry behind an account | `General Ledger` | narrow it (see below) |

## Filters

The first five reports take a period one of two ways:

```json
{"company": "<company>", "filter_based_on": "Fiscal Year",
 "from_fiscal_year": "<fiscal year>", "to_fiscal_year": "<fiscal year>", "periodicity": "Yearly"}
```

```json
{"company": "<company>", "filter_based_on": "Date Range",
 "period_start_date": "YYYY-MM-DD", "period_end_date": "YYYY-MM-DD", "periodicity": "Monthly"}
```

`periodicity` is `Monthly`, `Quarterly`, `Half-Yearly` or `Yearly`. Trial Balance takes
`company`, `fiscal_year`, `from_date` and `to_date`. Gross Profit takes `company`, `from_date`,
`to_date` and `group_by` (`Invoice` or `Item Code`). Accounts Receivable and Payable take
`company`, `report_date`, `ageing_based_on` (`Due Date`) and `range` (`30, 60, 90, 120`).

General Ledger answers are long. Always narrow it with `account`, `party_type` and `party`, or
`voucher_no`, besides `company`, `from_date` and `to_date`; otherwise the result is cut short.

## Steps

1. **Company.** List `Company`. If there is more than one and the question does not name one,
   ask which, or run the report for each and show them side by side. Never pick one silently.
2. **Period.** Use the period the user named. Otherwise use the current fiscal year: list
   `Fiscal Year` and take the one whose dates contain today. Say which period you used.
3. **Run the report** with the filters above, and read the figure from the row named in the table.
4. **Empty report.** If it has no rows, say so, and check the company and period before
   concluding there is no activity: the data is often in another company.
5. **Answer in words, then a card.** Always write one or two sentences: the figure, the company,
   the period, the currency, and the report it came from. A KPI or table block may follow, never
   replace the sentence.

## Example

"Report my net profit" on a site with the companies Examplia Pty Ltd and Examplia (Demo):

1. `list_documents` on `Company` shows two companies, so ask which, or run both.
2. `list_documents` on `Fiscal Year` gives the current year.
3. `run_report` with `report_name="Profit and Loss Statement"` and the fiscal-year filters.
4. "Examplia (Demo) made a profit of AUD 109,240 in fiscal year 2026-2027 (income 363,000, expenses
   253,760), from the Profit and Loss Statement. Examplia Pty Ltd has no entries for that year."
