# Daily Snapshot Opportunities — trf_opp_daily_snapshot_new

**When to load**: Historical pipeline state, trend analysis, QoQ movement, coverage curve, stage age features for the model.  
**Requires first**: [`sql/conventions.md`](../sql/conventions.md)  
**Also load**: [`tables/territory-mapping.md`](territory-mapping.md) for geo grouping  
**Not for**: Current opp state → [`tables/opportunity.md`](opportunity.md) · Live product bookings → [`tables/sku-nacv-fact.md`](sku-nacv-fact.md)  
**Key distinction**: `snapshot_date` here is a BUSINESS date (use it). `snaplogic_extract_date` in `opportunity_live` is ETL infrastructure (ignore it).

**Source table**: `[rep].[trf_opp_daily_snapshot_new]`  
**Alias**: `snap`  
**Row grain**: One row per opportunity per `snapshot_date` — this table is intentionally multi-row per opp  
**Primary key**: `Opp_Id` + `snapshot_date`  
**Related**: [`sql/conventions.md`](../sql/conventions.md) · [`tables/opportunity.md`](opportunity.md) (live opp data) · [`tables/territory-mapping.md`](territory-mapping.md) (geo join)

---

## RULE: current state = the LATEST snapshot, always

This table is multi-row per opp (one per `snapshot_date`). Whenever you want an opp's **current /
point-in-time** state (its `Stage_Age`, `Raw_Stage`, `Total_NACV`, etc. "as of now"), you MUST
anchor to the **latest** snapshot — never an arbitrary or stale row, and **never `CAST(GETDATE()
AS DATE)`** (that returns *nothing* on weekends, holidays, or before the day's ETL has landed).

Two correct forms:

```sql
-- (a) latest state PER OPP — preferred for enrichment/age features; keeps every opp
WITH latest_snapshot AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) AS rn
    FROM [rep].[trf_opp_daily_snapshot_new]
)
SELECT ... FROM latest_snapshot WHERE rn = 1

-- (b) the single most-recent snapshot DATE — for a whole-pipeline point-in-time rollup
WHERE snap.snapshot_date = (SELECT MAX(snapshot_date) FROM [rep].[trf_opp_daily_snapshot_new])
```

Prefer **(a)** when different opps may have different last-seen dates (e.g. opps drop out of the
feed after they close) so none are silently lost; use **(b)** for an aggregate "today's pipeline"
cut. The **only** time you filter to a specific/earlier `snapshot_date` is a deliberate historical
or time-series query (trend, coverage curve, QoQ) — that iterates dates on purpose.

---

## When to use this table vs opportunity_live

| Use case | Table |
|----------|-------|
| Current state of open pipeline | `[sfdc_trf].[opportunity_live]` |
| How pipeline looked on a specific past date | `[rep].[trf_opp_daily_snapshot_new]` |
| Quarter-over-quarter pipeline movement | `[rep].[trf_opp_daily_snapshot_new]` |
| How a deal progressed through stages over time | `[rep].[trf_opp_daily_snapshot_new]` |
| Pipeline at start/end of quarter | `[rep].[trf_opp_daily_snapshot_new]` |
| Waterfall / bridge analysis | `[rep].[trf_opp_daily_snapshot_new]` |
| Win rate by cohort (opps created in Q1, closed by Q2) | `[rep].[trf_opp_daily_snapshot_new]` |

> `snapshot_date` in this table is a **business date** — the date the pipeline state was captured.
> This is different from `snaplogic_extract_date` in `opportunity_live` which is an ETL infrastructure field.
> Always filter or group by `snapshot_date` to scope time-series queries.

---

## Core identity and join fields

| Column | Notes |
|--------|-------|
| `Opp_Id` | Opportunity ID — FK → `[sfdc_trf].[opportunity_live].Id` |
| `Account_Id` | Account ID — FK → `[sfdc_trf].[account_live].Id` |
| `Opportunity_OwnerId` | AE owner ID — FK → `[sfdc_trf].[user_live].Id` |
| `Opportunity_Owner` | AE owner name (denormalized) |
| `Bookings_Team_static` | Territory join key → `[sharepoint].[Map_Booking_Team_Static_live].Bookings_Team_Static` |
| `BTS_lower` | Lowercase version of territory (case-insensitive matching) |
| `snaplogic_extract_date` | ETL infrastructure field — do not use in business queries |

---

## Stage and outcome fields

Stage logic follows the same rules as `opportunity_live` — outcome is determined by stage, not dates.

| Column | Values | Notes |
|--------|--------|-------|
| `Raw_Stage` | `Closed Won` · `Closed Lost` · `Closed Deferred` · open stages | Stage as of `snapshot_date` |
| `Stage_Pipe_Category` | `Won` · `Lost` · `Pipe` · `Deferred` | Simplified stage bucket for pipeline reporting |
| `Manager_Forecast_Category` | `Closed Won` · `Closed Lost` · `Pipeline` · etc. | Manager forecast at time of snapshot |
| `Solex_Flag` | `True` / `False` | Whether this is a Solex opportunity |

**Stage_Pipe_Category values:**

| Value | Meaning |
|-------|---------|
| `Won` | `Raw_Stage = 'Closed Won'` |
| `Lost` | `Raw_Stage = 'Closed Lost'` |
| `Deferred` | `Raw_Stage = 'Closed Deferred'` — pushed out, not truly lost |
| `Pipe` | Any open stage |

---

## Financial fields

| Column | Notes |
|--------|-------|
| `Total_NACV` | Net Annual Contract Value at time of snapshot — primary financial metric |
| `NACV_Uplift` | NACV uplift component |
| `Incremental_ACV` | Incremental ACV |
| `IACV_NACV` | IACV from NACV calculation |
| `Cal_IACV` | Calculated IACV |

> All financial columns are nullable — use `ISNULL(col, 0)` in aggregations.
> This table uses `Total_NACV` not `Total_ARR__c` — do not mix with `opportunity_live` financial fields without aliasing clearly.

---

## Date fields

| Column | Notes |
|--------|-------|
| `snapshot_date` | **Business date** — when this pipeline state was captured. Use to scope time-series queries |
| `CloseDate` | Expected or actual close date as of this snapshot |
| `Stage_1_start_date` | Date opportunity entered stage 1 (Discovery) |
| `Stage_2_start_date` | Date entered stage 2 (Qualification) |
| `Stage_3_start_date` | Date entered stage 3 (Technical/Executive Evaluation) |
| `Stage_4_start_date` | Date entered stage 4 (Negotiation) |
| `Stage_5_start_date` | Date entered stage 5 |
| `Next_Steps_Last_Updated_date` | Last next steps update date |
| `maxstagedate` | Date of the latest stage the opp has reached |

---

## Quarter calendar fields

This table pre-computes quarter context for every snapshot row — use these instead of deriving quarter logic in SQL.

| Column | Example | Notes |
|--------|---------|-------|
| `snapshot_date` | `2025-07-10` | The snapshot date |
| `QuarterStartDate` | `2025-07-01` | First day of the quarter containing `snapshot_date` |
| `QuarterEndDate` | `2025-09-30` | Last day of the quarter containing `snapshot_date` |
| `Next2QtrEndDate` | `2027-03-31` | End of 2 quarters out from snapshot |
| `NumberQuarterDay` | `10` | Day number within the quarter (1-92) |
| `QuarterDay` | `Day 10` | Labeled version of day within quarter |
| `QuarterWeek` | `2` | Week number within the quarter |
| `QuarterWeekNew` | `2` | Adjusted week number |
| `IsQuarterWeekStartDate` | `0` / `1` | 1 = this snapshot_date is a Monday (week start) |
| `WeekDay` | `Thursday` | Day of week name |
| `DateLevel` | `D` · `W \| D` · `M \| W \| D` | Which date grouping levels apply to this row |

**DateLevel values** — controls which rollup grain this row should be included in:

| Value | Include in |
|-------|-----------|
| `D` | Daily reports only |
| `W \| D` | Weekly and daily reports |
| `M \| W \| D` | Monthly, weekly, and daily reports |

---

## Close-to-snapshot relationship

| Column | Notes |
|--------|-------|
| `Close_Snap_Qtr_Diff` | Quarters between `CloseDate` and `snapshot_date`. Negative = already closed. 0 = closes this quarter. 1 = closes next quarter |

**Common filter patterns using `Close_Snap_Qtr_Diff`:**

```sql
-- Opps that closed in the same quarter as the snapshot
WHERE Close_Snap_Qtr_Diff = 0

-- Opps expected to close next quarter from the snapshot date
WHERE Close_Snap_Qtr_Diff = 1

-- Already closed at time of snapshot
WHERE Close_Snap_Qtr_Diff < 0
```

---

## Age and velocity fields

| Column | Example | Notes |
|--------|---------|-------|
| `age_in_days_since_s1` | `88` | Days from stage 1 entry to snapshot date |
| `S1_Age` | `90` | Age since stage 1 in days (may differ slightly from above) |
| `NextStep_Age` | `2` | Days since next steps were last updated |
| `Stage_Age` | `78` | Days in current stage as of snapshot date |
| `age_in_days (bucket)` | `0 - 30 Days` | Age bucket for reporting |
| `Opp_Type` | `New Business` · `Expansion` · `Upsell` · `Professional Services` | Deal type (denormalized) |
| `Raw_Opp_Type` | Same as `Opp_Type` before any mapping | Raw source value |
| `opportunity_source` | `Sales Sourced` · `BDR Sourced` · `Partner Sourced` | How opp originated |

**Age bucket values:**
`0 - 30 Days` · `30 - 90 Days` · `90 - 180 Days` · `180 - 360 Days` · `360+ Days`

---

## Territory join

Same join pattern as `opportunity_live` — `Bookings_Team_static` maps to `Map_Booking_Team_Static_live`:

```sql
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  snap.Bookings_Team_static = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
```

Note: some rows have `Bookings_Team_static = NULL` (see `Opp_Id = 0068c000010f3pVAAQ` in sample) — `LEFT JOIN` handles these correctly, they resolve to `'Unassigned'`.

---

## Standard query patterns

### Point-in-time pipeline snapshot

```sql
SELECT
    snap.Opp_Id,
    snap.snapshot_date,
    snap.Raw_Stage,
    snap.Stage_Pipe_Category,
    snap.CloseDate,
    snap.Close_Snap_Qtr_Diff,
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    snap.Opportunity_Owner,
    snap.Opp_Type,
    ISNULL(snap.Total_NACV, 0)           AS Total_NACV
FROM [rep].[trf_opp_daily_snapshot_new] snap
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  snap.Bookings_Team_static = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE snap.snapshot_date = '2025-07-01'   -- scope to a specific date
  AND snap.Stage_Pipe_Category = 'Pipe'   -- open pipeline only
ORDER BY snap.Total_NACV DESC
```

### Quarter-start vs quarter-end pipeline movement

```sql
SELECT
    snap.Stage_Pipe_Category,
    ISNULL(bts.BTS_Geo, 'Unassigned')    AS Geo,
    snap.Opp_Type,
    COUNT(*)                             AS Opp_Count,
    SUM(ISNULL(snap.Total_NACV, 0))      AS Total_NACV
FROM [rep].[trf_opp_daily_snapshot_new] snap
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  snap.Bookings_Team_static = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE snap.snapshot_date = snap.QuarterStartDate  -- first day of each quarter
GROUP BY snap.Stage_Pipe_Category, bts.BTS_Geo, snap.Opp_Type
ORDER BY Total_NACV DESC
```

### Daily pipeline trend within a quarter

Use `DateLevel` to control grain — filter to `'D'` rows only for a daily series, or include `'W | D'` for week-start markers.

```sql
SELECT
    snap.snapshot_date,
    snap.NumberQuarterDay,
    snap.Stage_Pipe_Category,
    ISNULL(bts.BTS_Geo, 'Unassigned')   AS Geo,
    COUNT(*)                            AS Opp_Count,
    SUM(ISNULL(snap.Total_NACV, 0))     AS Total_NACV
FROM [rep].[trf_opp_daily_snapshot_new] snap
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  snap.Bookings_Team_static = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE snap.QuarterStartDate = '2025-07-01'   -- scope to one quarter
  AND snap.DateLevel LIKE '%D%'              -- daily grain rows
GROUP BY snap.snapshot_date, snap.NumberQuarterDay, snap.Stage_Pipe_Category, bts.BTS_Geo
ORDER BY snap.snapshot_date
```

### Deals closing this quarter, pipeline coverage ratio

```sql
SELECT
    ISNULL(bts.BTS_Geo,    'Unassigned') AS Geo,
    ISNULL(bts.BTS_Region, 'Unassigned') AS Region,
    SUM(CASE WHEN snap.Stage_Pipe_Category = 'Won'
             THEN ISNULL(snap.Total_NACV, 0) END)  AS Won_NACV,
    SUM(CASE WHEN snap.Stage_Pipe_Category = 'Pipe'
             THEN ISNULL(snap.Total_NACV, 0) END)  AS Pipe_NACV,
    SUM(CASE WHEN snap.Stage_Pipe_Category = 'Lost'
             THEN ISNULL(snap.Total_NACV, 0) END)  AS Lost_NACV,
    SUM(CASE WHEN snap.Stage_Pipe_Category = 'Deferred'
             THEN ISNULL(snap.Total_NACV, 0) END)  AS Deferred_NACV
FROM [rep].[trf_opp_daily_snapshot_new] snap
LEFT JOIN [sharepoint].[Map_Booking_Team_Static_live] bts
    ON  snap.Bookings_Team_static = bts.Bookings_Team_Static
    AND bts.ActiveTeam = 'Active'
WHERE snap.snapshot_date = (SELECT MAX(snapshot_date) FROM [rep].[trf_opp_daily_snapshot_new])  -- latest snapshot
  AND snap.Close_Snap_Qtr_Diff = 0                  -- closing this quarter
GROUP BY bts.BTS_Geo, bts.BTS_Region, bts.BTS_Geo_Sort, bts.BTS_Region_Org_Sort
ORDER BY bts.BTS_Geo_Sort, bts.BTS_Region_Org_Sort
```

---

## Joining snapshot to opportunity_live

When you need the opp's **current** snapshot state enriched with live opportunity detail, anchor to
the **latest snapshot per opp** (`rn = 1`) — not a single global date and never `GETDATE()`:

```sql
WITH latest_snapshot AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) AS rn
    FROM [rep].[trf_opp_daily_snapshot_new]
)
SELECT
    snap.snapshot_date        AS As_Of_Snapshot,
    snap.Opp_Id,
    snap.Raw_Stage            AS Stage_At_Snapshot,
    snap.Total_NACV           AS NACV_At_Snapshot,
    snap.Stage_Age, snap.S1_Age, snap.NextStep_Age,
    o.StageName               AS Current_Stage,
    o.Total_ARR__c            AS Current_ARR,
    o.MEDDPICC_Score__c,
    o.Loss_Reason__c
FROM latest_snapshot snap
JOIN [sfdc_trf].[opportunity_live] o
    ON snap.Opp_Id = o.Id
    AND o.IsDeleted = 0
WHERE snap.rn = 1
```

The per-opp `ROW_NUMBER() … ORDER BY snapshot_date DESC` + `rn = 1` is the canonical
"latest snapshot" pattern — it keeps every opp's most-recent state (including opps that stopped
appearing in the feed after they closed), where a single global `MAX(snapshot_date)` would drop them.

> Note: `Total_NACV` (snapshot) and `Total_ARR__c` (live) are different metrics — alias them clearly when selecting both.

---

## Key differences from opportunity_live

| | `opportunity_live` | `trf_opp_daily_snapshot_new` |
|--|-------------------|------------------------------|
| Row grain | One per opp | One per opp per day |
| Use for | Current state | Historical / trend |
| Financial field | `Total_ARR__c` | `Total_NACV` |
| Territory join col | `Bookings_Team_static__c` | `Bookings_Team_static` (no `__c`) |
| Stage field | `StageName` | `Raw_Stage` |
| ETL date field | `snaplogic_extract_date` (ignore) | `snapshot_date` (use this) |
| Quarter context | Derive manually | Pre-computed (`QuarterStartDate`, `NumberQuarterDay`, etc.) |
---

## Handoff

- Computing coverage curve → load [`analysis/coverage-curve.md`](../analysis/coverage-curve.md)
- Need geo columns → load [`tables/territory-mapping.md`](territory-mapping.md)
- Need Stage_Age / S1_Age (opp age at a point in time) → this file is the source; join to [`tables/sku-nacv-fact.md`](sku-nacv-fact.md)
