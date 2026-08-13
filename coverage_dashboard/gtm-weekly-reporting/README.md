# GTM Weekly Reporting

Automated pipeline reporting that pulls current fiscal year data from Azure Synapse and builds quarterly summary tables in a Jupyter notebook. Replaces the manual Excel-based GTM Weekly Reporting workbook.

## Tables produced

1. **Quarterly Summary** — Total Pipe, LS Pipe, QTD Booked, Target, LTB, Coverage by Region/Team
2. **Product Breakdown** — Same metrics by Product × Geo (AMS / EMEA / APAC)
3. **Deal Type Breakdown** — Same metrics by Geo × New/Existing

Fiscal year auto-detects from today's date (FY = calendar year, Q1 = Jan–Mar).

## Prerequisites

- Python 3.11 or higher
- Microsoft ODBC Driver 18 for SQL Server (install at OS level — pip cannot install this)
  - Windows: download from https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
  - macOS: `brew install msodbcsql18`
  - Linux: follow Microsoft's apt/yum instructions
- Azure AD account with read access to the Synapse `DedicatedSQLPool` database

## Setup

1. Create and activate a virtual environment:

   **Windows:**

   ```
   python -m venv .venv
   .venv\Scripts\activate
   ```

   **macOS / Linux:**

   ```
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```
   pip install -r requirements.txt
   ```

   **Windows (`MAX_PATH`):** If the repo lives under a **very deep path** (for example long OneDrive or company folders), `pip install` can fail while extracting packages with long nested paths (`jupyterlab-manager`, some `jedi` stubs). Fix one of:

   - Enable [Windows long paths](https://pip.pypa.io/warnings/enable-long-paths) (recommended), **or**
   - Clone or copy the project to a **shorter directory** (for example `C:\work\gtm-weekly-reporting`), **or**
   - Create the virtualenv on a short path (`python -m venv C:\gtmvenv` then activate and `pip install -r …\requirements.txt`), **or** after a successful install into e.g. `C:\gtm-reporting-venv`, link it into the repo: `cmd /c mklink /J .venv C:\gtm-reporting-venv` from the `gtm-weekly-reporting` folder so `.\.venv` works without long-path errors.

3. Create your `.env` file from the template:

   ```
   cp .env.example .env
   ```

   The defaults should work as-is for production Synapse access.

4. Register the Jupyter kernel:

   ```
   python -m ipykernel install --user --name=gtm-weekly --display-name="GTM Weekly"
   ```

5. Launch the notebook:

   ```
   jupyter notebook "notebooks/GTM Weekly Reporting.ipynb"
   ```

## Running the weekly report

After setup, from this project directory (`gtm-weekly-reporting`), run:

```
python scripts/run_weekly_report.py
```

This will:

- Pull current FY data from Synapse
- Build the three quarterly tables (current quarter)
- Compare against the prior snapshot when one exists under `output/snapshots/` (week-over-week Δ columns use **zeros** when no prior snapshot is available yet)
- Write an HTML dashboard to `output/dashboards/`
- Write a **WoW Excel export** (current quarter only, pipe + coverage deltas) to `output/weekly/GTM_Weekly_WoW_FY*_Q*_DATE.xlsx` with sheets **Quarterly**, **Product**, and **Deal Type**
- Save one **raw** snapshot workbook to `output/snapshots/` for the next run’s diff (tabs for Q1–Q4 across `Quarterly`, `Product`, `Deal Type`; no Δ columns stored here)

For backfills:

```
python scripts/run_weekly_report.py --as-of 2026-04-21
```

Optional flags: `--quarter N`, `--no-snapshot`, `--output-dir PATH`. Core logic lives in `src/pipeline.py`; snapshots in `src/snapshots.py`.

## Authentication

The notebook uses `ActiveDirectoryInteractive` auth, which opens a browser popup on first connection. Sign in with your work Microsoft account. The token caches for the session.

## Notebook structure

The notebook is organized into 8 sections:

1. **Setup & Date Config** — auto-detects FY and current quarter
2. **Pull Data from Synapse** — runs the main SQL query
3. **Enrich the Data** — adds Region Family, Region, Geo_View, Is_LS, Deal_Class
4. **Targets** — three hardcoded target dictionaries (team, product, deal type)
5. **Quarterly Summary Table** — primary view
6. **Product Breakdown Table** — by product × geo
7. **Geo × Deal Type Breakdown** — by new vs existing
8. **Reconciliation Check** — confirms all three table totals reconcile

## Updating targets

Targets live in three Python dicts in Section 4 of the notebook (and the same constants in `src/pipeline.py`). To update for a new fiscal year, copy the existing FY26 block and adjust the values from the source workbook (`Targets` and `Geo by Deal Type` tabs).
