# Coverage Curve — Context

**When to load**: Building or understanding coverage metrics — open pipe, booked, LTB, coverage multiple, WoW delta.  
**Requires**: [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) (primary data source) · [`tables/territory-mapping.md`](../tables/territory-mapping.md) (geo breakdown)  
**Used by**: [`analysis/gtm-dashboard.md`](gtm-dashboard.md) (coverage view in the dashboard)  
**This is NOT**: A forecast or recommendation engine — it is purely observational point-in-time pipeline state.

**What this is**: A point-in-time view of pipeline health using the daily snapshot table.  
**Core question**: At any given week of a quarter, how much open pipe do we have relative to what's still left to book?  
**Related tables**: [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) (primary) · [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) (live booked) · [`tables/territory-mapping.md`](../tables/territory-mapping.md)

---

## The four measures

Everything builds from four numbers at a given point in time:

| Measure | Source | Column | Definition |
|---------|--------|--------|------------|
| `open_pipe` | `[rep].[trf_opp_daily_snapshot_new]` | `Cal_IACV` | `Stage_Pipe_Category = 'Pipe'` at the snapshot date |
| `booked` | `[rep].[trf_opp_daily_snapshot_new]` | `Cal_IACV` | `Stage_Pipe_Category = 'Won'` at the snapshot date |
| `LTB` | derived | — | `target − booked` (Left to Book) |
| `coverage` | derived | — | `open_pipe / LTB` |

> `Cal_IACV` is the correct column for both open pipe and booked from the snapshot table.  
> Do not use `Total_NACV` from the snapshot — it is not used for this analysis.

---

## Coverage formula

```
LTB      = target − booked
coverage = open_pipe / LTB
```

Coverage > 1× means more pipe than needed. Coverage < 1× means pipe gap.  
A healthy quarter starts ~3–5× at week 1 and glides toward 1× at close.  
When LTB ≤ 0 (booked ≥ target), coverage is undefined — quarter is already made.

---

## How the snapshot table gives us point-in-time state

The snapshot table has one row per opportunity per `snapshot_date`.  
To get pipeline state at any point in time, filter to a single `snapshot_date`:

```sql
-- Open pipe at a specific date
SELECT
    SUM(snap.Cal_IACV)                               AS open_pipe
FROM [rep].[trf_opp_daily_snapshot_new] snap
WHERE snap.snapshot_date      = '2025-07-10'
  AND snap.Stage_Pipe_Category = 'Pipe'
  AND snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')

-- Booked at a specific date (cumulative Won so far in the quarter)
SELECT
    SUM(snap.Cal_IACV)                               AS booked
FROM [rep].[trf_opp_daily_snapshot_new] snap
WHERE snap.snapshot_date      = '2025-07-10'
  AND snap.Stage_Pipe_Category = 'Won'
  AND snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
```

---

## Weekly view — one snapshot per week

To build the week-over-week curve, downsample to one snapshot per week.  
Week numbers: `((snapshot_date − quarter_start).days // 7 + 1)` clipped to 1–13.

**Rule**: use the earliest snapshot in each week — this gives start-of-week pipe.  
**Exception**: the current in-flight week uses the latest snapshot — "as of now."

```sql
-- Weekly coverage curve for a quarter
WITH weekly AS (
    SELECT
        snap.Opp_Id,
        snap.snapshot_date,
        snap.Stage_Pipe_Category,
        snap.Cal_IACV,
        snap.Bookings_Team_static,
        snap.QuarterStartDate,
        snap.QuarterWeek,
        -- earliest snapshot in each week = start-of-week state
        ROW_NUMBER() OVER (
            PARTITION BY snap.Opp_Id, snap.QuarterWeek
            ORDER BY snap.snapshot_date ASC
        ) AS rn
    FROM [rep].[trf_opp_daily_snapshot_new] snap
    WHERE snap.QuarterStartDate = '2025-07-01'     -- scope to one quarter
      AND snap.Bookings_Team_static IS NOT NULL
      AND snap.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
)
SELECT
    w.QuarterWeek                                    AS week_of_quarter,
    MIN(w.snapshot_date)                             AS week_start_date,
    SUM(CASE WHEN w.Stage_Pipe_Category = 'Pipe'
             THEN w.Cal_IACV ELSE 0 END)             AS open_pipe,
    SUM(CASE WHEN w.Stage_Pipe_Category = 'Won'
             THEN w.Cal_IACV ELSE 0 END)             AS booked
FROM weekly w
WHERE w.rn = 1
GROUP BY w.QuarterWeek
ORDER BY w.QuarterWeek
```

Then in Python or a downstream layer:
```python
df['LTB']      = target - df['booked']
df['coverage'] = df['open_pipe'] / df['LTB'].where(df['LTB'] > 0)
df['cov_wow']  = df['coverage'].diff()   # week-over-week change
```

---

## Week-over-week coverage change

`cov_wow = coverage(week N) − coverage(week N−1)`

Positive = coverage increased (more pipe vs LTB than last week — good if booked is growing).  
Negative = coverage fell (pipe is converting or dropping — expected as quarter progresses).  
A large negative spike mid-quarter often signals pipe attrition, not bookings.

---

## Snapshot filters — always apply

```sql
WHERE snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
  AND snap.Raw_Stage NOT IN (
      'Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
      'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction'
  )
```

**Snapshot duplication**: the snapshot table emits each (opp, snapshot_date) row twice due to an upstream join. Always `drop_duplicates()` in Python or `SELECT DISTINCT` in SQL after pulling. Without this, open_pipe and booked are exactly 2× too high.

---

## Geo breakdown

Join to `[sharepoint].[Map_Booking_Team_Static_live]` on `Bookings_Team_static` with `ActiveTeam = 'Active'`.  
Use `BTS_RegionFamily` for geo bucketing — **not** `BTS_Geo` (loses Public Sector).  
Four buckets: `AMS` / `EMEA` / `APAC` / `Public Sector`.

---

## Historical coverage reference

| Quarter | Wk 1 open pipe | Wk 1 coverage | Wk 4 coverage | Attainment |
|---------|---------------|---------------|---------------|------------|
| FY24 Q2 | $69.2M | 3.34× | 3.26× | 102% |
| FY24 Q3 | $78.0M | 3.62× | 2.96× | 107% |
| FY24 Q4 | $117.2M | 3.13× | 2.82× | 99% |
| FY25 Q1 | $88.6M | 3.98× | 3.33× | 75% |
| FY25 Q2 | $96.5M | 3.46× | 3.18× | 76% |
| FY25 Q3 | $99.3M | 3.04× | 2.70× | 86% |
| FY25 Q4 | $189.4M | 3.83× | 3.42× | 81% |
| FY26 Q1 | $99.7M | 4.49× | 3.43× | 73% |
| FY26 Q2 | $99.9M | 3.41× | 2.86× | in-flight |

FY24 hit target at lower coverage because conversion was meaningfully better.
Conversion has degraded year-over-year — this is real, not a calculation artifact.

---

## What this is NOT

- This is not a forecast
- This is not a prediction of whether the quarter will close
- There is no "needed coverage" recommendation engine in this context — that is a separate layer built on top of these mechanics
- The snapshot table is purely the source of historical pipeline state; the coverage curve is observational
---

## Handoff

- Building the full GTM intelligence dashboard → load [`analysis/gtm-dashboard.md`](gtm-dashboard.md)
- Need snapshot table column details → load [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)
- Need geo breakdown → load [`tables/territory-mapping.md`](../tables/territory-mapping.md)
