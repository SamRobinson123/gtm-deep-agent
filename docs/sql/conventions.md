# SQL Conventions

**When to load this file**: Before writing ANY SQL query against any GTM table.  
**Loaded by**: [`README.md`](../README.md) task map — every SQL task starts here.  
**Then load**: The relevant table file(s) from `tables/` for column names and join patterns.  
**Do not load**: [`sql/patterns.md`](patterns.md) unless you need a ready-to-run template — conventions are sufficient for writing new queries.

Universal rules for every query in this GTM system. These rules apply to all tables.

---

## Stage determines outcome

Won/lost status comes from `StageName`, never from dates.

```
StageName = 'Closed Won'   → IsWon = 1, IsClosed = 1
StageName = 'Closed Lost'  → IsWon = 0, IsClosed = 1
StageName = 'Opportunity Rejected' → IsWon = 0, IsClosed = 1, ForecastCategory = Omitted
```

`IsWon` and `IsClosed` are pre-derived flags — fine to use in WHERE and CASE expressions,
but they flow from `StageName`. When filtering for analysis, prefer `StageName` explicitly
so the intent is unambiguous.

**Closed Lost vs Opportunity Rejected**: both have `IsClosed = 1, IsWon = 0`.
Rejected opps were disqualified early and lack structured `Loss_Reason__c` data.
For loss analysis always filter `StageName = 'Closed Lost'` — never just `IsWon = 0`.

---

## Date fields

| Goal | Use |
|------|-----|
| When did the deal close / when is it expected to close? | `CloseDate` |
| When did the opp enter the pipeline? | `CreatedDate` |
| Finance booking date | `Booking_Date__c` |
| Last stage transition | `LastStageChangeDate` |
| Last logged activity | `LastActivityDate` |

`snaplogic_extract_date` and `snap_source_hash` are ETL infrastructure fields.
Never use them in business queries.

`snapshot_date` in `[rep].[trf_opp_daily_snapshot_new]` is **not** an ETL field —
it is the business date the pipeline state was captured and is the primary filter
for all time-series queries against that table.

---

## Financial field hierarchy

Never use `Amount` or `Probability` — they are legacy SFDC fields.

| Metric | Field |
|--------|-------|
| Primary pipeline metric | `Total_ARR__c` |
| Primary booking metric | `NACV__c` |
| Annual contract value | `ACV__c` |
| Total contract value | `TCV__c` |
| Win probability | `Win_Prob` from `scored_opps.parquet` (not `Probability`) |

All financial columns are nullable — always wrap in `ISNULL(col, 0)` in aggregations.

---

## Mandatory filters on every query

```sql
WHERE IsDeleted = 0   -- always: deleted records persist in the table
```

---

## Territory and geo

Never derive Geo, Region, or Product Family with a CASE statement on `Bookings_Team_static__c`.
Always join `[sharepoint].[Map_Booking_Team_Static_live]`:

```sql
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  o.Bookings_Team_static__c = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
```

Use `LEFT JOIN` — opportunities with no active mapping must appear as `'Unassigned'`, not be dropped.
See [`tables/territory-mapping.md`](../tables/territory-mapping.md) for the full column reference.

---

## Currency

`CurrencyIsoCode` varies by deal — USD, EUR, GBP, others.
Normalize to USD before any cross-geo aggregation.
EUR deals are common in EMEA (`Legal_Entity__c = 'Tricentis GmbH'`).

---

## NULL handling in text fields

Some text columns contain artifacts — exclude or treat as NULL:
- `mkto_si__Sales_Insight__c` / `mkto_si__MarketoAnalyzer__c` — contain raw HTML markup
- Fields with `[B@...]` values — Java object hash artifacts from the ETL pipeline

---

## Handoff

After reading this file:
- For column names and join keys → load the relevant `tables/` file
- For ready-to-run query templates → load [`sql/patterns.md`](patterns.md)
- For geo grouping in any query → load [`tables/territory-mapping.md`](../tables/territory-mapping.md)
