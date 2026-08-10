# Legacy Pipe Create workbook — `Pipeline Creation Quarter Product V20.xlsm`

**Location:** `data/legacy/Pipeline Creation Quarter Product V20.xlsm` (27.9 MB, 24 sheets)
**Status:** Superseded by [`../models/pipe-create.md`](../models/pipe-create.md).
**Role:** reconciliation baseline and historical reference — **not** a source of truth.

The Excel predecessor of the Pipe Create model. It is kept because it is the only
artifact that shows how these numbers were produced *before* the Python pipeline,
which makes it the natural place to reconcile against and the most likely place to
resolve root `CLAUDE.md` invariant 10.

---

## How to treat it

1. **The Python model wins.** Where this workbook and
   [`../models/pipe-create.md`](../models/pipe-create.md) disagree, the Python
   model is current. Surface the discrepancy; do not reconcile silently.
2. **Never cite a cell as fact** without stating it came from the legacy
   workbook and that its formulas are unaudited.
3. **Load lazily.** 27.9 MB. Read one sheet with
   `pd.read_excel(path, sheet_name=..., engine='openpyxl')` — never the whole
   workbook.
4. It is macro-enabled (`.xlsm`). Macros are **not** to be executed.

---

## Sheet map — verified 2026-08-10

Names, dimensions, and visibility were read directly. **Contents and formulas
have not been audited** — every "purpose" below is inferred from the sheet name
and is therefore unverified.

| Sheet | Rows x Cols | Likely purpose (UNVERIFIED) |
|---|---:|---|
| `Pipeline Waterfall OLD` | 1785 x 49 | Superseded waterfall |
| `Pipeline Waterfall (Quarterly)` | 1785 x 49 | Quarterly pipeline waterfall |
| `Pivot Summary (Quarterly)` | 17 x 16 | Summary pivot |
| `Historic Floor` | 194 x 13 | Historical baseline / floor |
| `Productivity` | 49 x 16 | **Capacity model — see open question 2** |
| `Opps` | 34892 x 25 | Opportunity-level detail |
| `Executive Summary` | 28 x 31 | Exec view |
| `Pipe Create Summary` | 104 x 17 | **Primary reconciliation target** |
| `Opp Count Summary` | 103 x 14 | **Bears on invariant 10** |
| `Geo Source Summary` | 202 x 26 | Pipe create by Geo x Source |
| `Quarter Matrix` | 36 x 10 | Quarter definitions / calendar |
| `Geo Opp Count` | 213 x 23 | Opp counts by Geo |
| `Head Count` | 22 x 11 | Relates to `data/Headcount.xlsx` |
| `Mapping` | 21 x 3 | Dimension mapping |
| `Sheet2` | 22 x 11 | Untitled working sheet |
| `Create VS Close` | 3541 x 5 | Create-date vs close-date comparison |
| `Pipe Create Chart` | 75 x 22 | Chart source |
| `ASP` | 15 x 13 | **Bears on invariants 2 and 10** |
| `Pipeline Flow` | 37731 x 11 | Flow-level detail |
| `Pipe Create By Split` | 113113 x 20 | Largest sheet — split-level detail |
| `Opp Count` | 50257 x 10 | Opp count detail |
| `Sheet1` | 34895 x 27 | Untitled working sheet |
| `NACV Reassignment` | 34 x 8 | NACV reassignment adjustments |
| `Pipeline Reallocation` | 84 x 19 | Target/pipe reallocation adjustments |

All 24 sheets are `visible`. There are no hidden sheets.

---

## Why this workbook matters — invariant 10

Root `CLAUDE.md` invariant 10 records that the `Opportunities` target unit is
**unresolved**: it may count opp-product-lines rather than distinct opps, and the
target runs ~5.5x actual pace.

Three sheets bear on it directly — `ASP`, `Opp Count Summary`, and
`Pipe Create By Split` (whose name and 113,113 rows suggest split/product-line
grain rather than opp grain, which is *exactly* the hypothesis in question).

**This has not been investigated.** The sheets are named here so the work has a
starting point, not because the answer is known. Until someone reads the formulas
and confirms, invariant 10 stands and its caveat travels with every opp-count and
ASP figure.

---

## Relationship to the current model

| Concern | Legacy workbook | Current model |
|---|---|---|
| Targets | Embedded in sheets | `data/Target_Monthly.csv` — [`../tables/target-monthly.md`](../tables/target-monthly.md) |
| Actuals | `Opps` / `Pipeline Flow` sheets | Snapshot feed, `MIN(snapshot_date)` over the full buffered frame |
| Allocation | Unaudited formulas | Day-weighted, prorated to `days_counted` |
| Grain | Product / split level | `Geo` / `Region` / `Territory` via `BTS_Territory` |
| Output | Excel sheets | `gtm_pipe_create.parquet` + `.json` |
