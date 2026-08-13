# Coverage Curve Analysis — Codebase Context

> Generated 2026-07-18. Source: PLAN.md (SharePoint), all backend Python files,
> SQL files, and loaders in the project conversation context.
> Use this as the single reference before making any changes to the pipeline.

---

## 1. What This Project Does

One question drives everything:

> "At each week of a quarter, how many dollars of open pipe were sitting in
> front of the team for every dollar still to book — and how does that curve
> compare to prior quarters at the same week of life?"

Weekly manual run. Pulls daily opportunity snapshots from Azure Synapse,
downsamples to one pinned snapshot per week, computes coverage = `open_pipe /
(target − booked)`, and renders a self-contained HTML dashboard. No server.
No build step. No forecast. Not a replacement for `gtm-weekly-reporting` — it
adds the *time-series curve* that tool doesn't have.

---

## 2. Repo Layout

```
coverage-curve-analysis/
├── backend/
│   ├── synapse.py              # pyodbc connection via SYNAPSE_CONN_STR in .env
│   ├── snapshot.py             # pull_snapshot(), pull_live_booked(), pull_opp_products()
│   ├── coverage_builder.py     # all math: build_coverage, build_segment_coverage,
│   │                           #   build_product_coverage, compute_recommendations,
│   │                           #   compute_segment_recommendations,
│   │                           #   compute_product_recommendations, compute_product_conversion
│   ├── build_coverage.py       # entry point: pull → build → parquet → render
│   ├── coverage_render.py      # build_payload(), render() → HTML
│   └── sql/
│       ├── snapshot.sql        # canonical snapshot pull
│       ├── live_booked.sql     # live SKU NACV pull
│       └── opp_products.sql    # SKU-inclusive per-opp product families
├── data/inputs/
│   ├── loaders.py              # load_booking_team_mapping(), load_quarter_targets(),
│   │                           #   load_segment_targets(), load_product_targets()
│   ├── FY'24 Targets.xlsx
│   ├── FY'25 Targets.xlsx
│   └── FY'26 Targets.xlsx
├── scripts/
│   ├── build_new_segment_dashboard.py   # comparison build with new tier logic
│   ├── freeze_segment_payload.py        # captures segment payload from last good HTML
│   ├── pressure_test_product_alloc.py   # validates SKU-share allocation math
│   ├── analyze_multi_product_opps.py    # ad-hoc: multi-product opp stats
│   ├── scrape_pipe_balance.py           # scrapes GTM Exec Pipe Balance workbooks
│   └── segment_tier_shift_analysis.py   # compares X2019 vs Current segment defs
├── frontend/
│   ├── dashboard_template.html          # Vanilla JS + SVG dashboard template
│   └── assets/tricentis-logo.png
├── output/                              # gitignored parquets + rendered HTML
└── planning/PLAN.md                     # full methodology (this doc summarises it)
```

Entry point: `uv run python -m backend.build_coverage`

---

## 3. Data Sources

### 3.1 Synapse Tables

| Table | Grain | Used for |
|---|---|---|
| `[rep].[trf_opp_daily_snapshot_new]` | (opp, snapshot_date) | open_pipe, ls_pipe, raw booked |
| `[src].[sku_nacv_fact]` | (opp, SKU) | live booked (Product_NACV) |
| `[src].[trf_sku_nacv_npa_allocated]` | (opp, allocated family) | per-opp SKU mix for product page |
| `[sharepoint].[Map_Booking_Team_Static_live]` | booking team | geo bucketing |
| `[rpt_cx].[account_segment_quarterly]` | (account, quarter) | account tier — **REMOVED from Synapse** (see §10 Segment Issue) |
| `[sfdc_trf].[account_live]` | account | replacement tier source (proposed) |

### 3.2 Dollar Metrics — Do Not Mix

| Metric | Source column | Used for |
|---|---|---|
| `Cal_IACV` | snapshot table | open_pipe, ls_pipe — matches PBI open-pipe |
| `Product_NACV` = `NACV_USD − Uplift_USD` | sku_nacv_fact | booked — matches PBI bookings / Historic.xlsx |
| `Total_NACV` | snapshot table | **unused** (selected but ignored) |

### 3.3 Snapshot Filters (snapshot.sql)

Four filter predicates determine what rows enter the pipeline:

1. `Cal_IACV != 0` — drops zero-value rows
2. `Bookings_Team_static NOT IN ('Account Management', 'Global', 'QAS Account Management')` and NOT NULL
3. `Raw_Stage` exclusions — drops `Closed - Duplicate`, `Stage 6 - Closed - Admin`, `Stage 7 - Churned`, `Opportunity Rejected`, `Stage 0 - Renewal Outreach Not Started`, `0 - First Interaction`
4. Date bounds on both `snapshot_date` and `CloseDate` (per fiscal year)

### 3.4 Live Booked Filters (live_booked.sql)

- `Period = 'Period_1'`, `NACV_USD != 0`
- `Record_Type IN ('Product', 'Service', 'Platinum support')`
- `Deal_Type IN ('New Business', 'Expansion', 'Upsell', 'Professional services')`
- Same non-quota-team exclusions as snapshot
- `StageName IN ('6 - Closed/Pending', 'Closed Won', 'Stage 5 - Closed Won')`
- **NPA rows KEPT** — they are real negative adjustments (de-bookings). Excluding them overstated FY26 Q1 by ~$1.75M.
- Inner collapse = SUM not MAX (a booking + negative correction must net correctly)
- Grain = one row per (opp × product family), SKU-line attributed

---

## 4. Stage Taxonomy

```python
Raw_Stage → Stage mapping:
  'Closed Deferred', 'Closed Lost'          → 'Closed'
  '6 - Closed/Pending', 'Closed Won',
  'Stage 5 - Closed Won'                    → 'Closed Won'
  'Closed - Duplicate', 'Stage 6 ...',
  'Stage 7 - Churned', 'Opportunity
   Rejected', '0 - First Interaction'       → 'Other'
  everything else                           → 'Open'

LATE_STAGES (subset of Open):
  '3 - Executive Presentation'
  '4 - Technical Evaluation'
  '5 - Negotiation / Business Procurement'
  '6 - Closed/Pending'
  'Stage 4 - Closed Pending'
```

`6 - Closed/Pending` maps to **Closed Won** in stage taxonomy AND is included in booked. A pending-close deal is not open pipe.

---

## 5. Geo Bucketing

### 5.1 Four Canonical Buckets
`AMS` / `APAC` / `EMEA` / `Public Sector`

### 5.2 How It Works (`coverage_builder._attach_geo`)

1. Join snapshot's `Bookings_Team_static` (lowercased) to mapping's `Bookings_Team_Static` (lowercased)
2. `_bucket_region_family(BTS_RegionFamily)` → primary geo:
   - `Pubsec` / `PubSec` → `Public Sector`
   - starts with `AMS` → `AMS`
   - starts with `EMEA` → `EMEA`
   - starts with `APAC` → `APAC`
   - anything else (DevOps, Sealights-specific) → None → fallback
3. `_bucket_from_team_name(Bookings_Team_static)` → fallback for historic/defunct teams
   - checks `public sector` / `pubsec` first (catches "AMS Public Sector")
   - then prefix: `ams` → AMS, `apac` → APAC, `emea` → EMEA

**Critical:** Use `BTS_RegionFamily` not `BTS_Geo` — `BTS_Geo` loses Public Sector.

### 5.3 Booking Team Mapping

Pulled from Synapse: `SELECT * FROM [sharepoint].[Map_Booking_Team_Static_live]`
Filtered to `ActiveTeam == 'Active'` in Python (DevOps teams are defunct, correctly excluded).

Required columns: `ActiveTeam`, `Bookings_Team_Static`, `BTS_RegionFamily`

---

## 6. Weekly Downsampling Logic

### 6.1 Quarter Assignment

Deal belongs to its `CloseDate`'s quarter. `FY{nn} Q{n}` format (calendar year = fiscal year).

### 6.2 Snapshot Window

Per fiscal year, pull snapshots from `fy_start − 14 days` to `fy_end`, but only keep rows where `snapshot_date` is within `[quarter_start − 14 days, quarter_end]` for the deal's own CloseDate quarter. The 14-day pre-buffer lets week 1 anchor to the end-of-prior-quarter snapshot.

### 6.3 Week Pinning (`_pin_to_week_date`)

**Rule:** one `snapshot_date` per (quarter, week_of_quarter). Week numbers computed as `((snapshot_date − quarter_start).days // 7 + 1).clip(1, 13)`.

- **All weeks → beginning-of-week**: keep the *earliest* in-quarter snapshot in that week.
  - Week 1's earliest = the quarter start date (Jan-1, Apr-1, etc.) → ties PBI "starting pipe" automatically
- **Exception — current in-flight week**: keep the *latest* snapshot → ties `gtm-weekly-reporting`'s "as of today" number

This means: curve reads "pipe at the start of each week" except the live week reads "as of now."

### 6.4 The Three Weekly Measures

| Measure | Source | Rule |
|---|---|---|
| `open_pipe` | snapshot `Cal_IACV` | Stage = 'Open' |
| `ls_pipe` | snapshot `Cal_IACV` | Stage = 'Open' AND Raw_Stage in LATE_STAGES |
| `booked` | **split by week** | wks 1–12: snapshot Closed Won Cal_IACV; wk 13: live Product_NACV total; current quarter latest week: live total so far |

**Booked source by week — the critical split:**

| Week | booked source | Why |
|---|---|---|
| Weeks 1–12 | snapshot `Cal_IACV` where Stage='Closed Won' | Consistent gross metric, matches GTM weekly |
| Week 13 (final) | live `Product_NACV` cumulative | True final number, ties Historic.xlsx / PBI |
| In-flight current week | live total so far | "_override_inflight_latest_booked()" — intentionally differs from GTM snapshot booked |
| Future quarters | live Product_NACV so far at week 1 | `_future_standing_frame()` |

### 6.5 Future Quarters

Quarters starting after the run date get a single week-1 "current standing" point via `_future_standing_frame()`:
- `open_pipe` / `ls_pipe` from latest snapshot for deals closing in that quarter
- `booked` = live Closed-Won total so far
- All (geo, deal_type) target combos are zero-filled so full target flows through

### 6.6 Sparse Quarters

`SPARSE_WEEK_THRESHOLD = 8`. Quarters with fewer than 8 weeks of open_pipe data (currently only FY24 Q1 with 2 weeks) are flagged sparse. In the renderer they get a flat "representative" line (max open_pipe broadcast to all 13 weeks). In the recommendation engine they are **excluded from the training set**.

---

## 7. Coverage Formula

```
LTB       = target − booked         (Left-to-Book)
total_cov = open_pipe / LTB         → None when LTB ≤ 0
ls_cov    = ls_pipe   / LTB         → None when LTB ≤ 0
```

Coverage > 1× = you have more pipe than you need. Coverage glides from high (~3–5× at wk 1) toward 1× at quarter close.

---

## 8. Recommendation Engine (Needed Coverage)

### 8.1 What It Computes

For each (geo, deal_type, week_of_quarter), across all closed non-sparse quarters:

```
conversion(Q, geo, dt, N) = (final_booked − booked_at_week_N) / open_pipe_at_week_N

rec_coverage(geo, dt, N)  = 1 / median(conversion across closed quarters)
```

"Loose" conversion: credits ALL bookings between week N and EOQ, including new pipe that entered the funnel after week N. Deliberate — reflects how teams actually perform.

### 8.2 Training Set

- Closed quarters = snapshot reached week 13 (checked via `open_pipe.notna()` rows, not live-booked reindex rows, to exclude in-flight quarters from training)
- Sparse quarters excluded
- Currently FY24 Q2 → FY26 Q1 = ~8 quarters
- Negative conversions dropped; conversions where open_pipe ≤ 0 dropped

### 8.3 Aggregation Levels

Computed independently at all four grains (dollar-weighted, not averaged from cells):
- (geo, deal_type) — per cell
- (All, deal_type) — all geos, one deal type
- (geo, All) — one geo, all deal types
- (All, All) — grand aggregate

### 8.4 Quarter-State Rule for §01 "Needed" Column

- **Closed quarter**: per-quarter retrospective needed = `1 / ((final_booked − booked_now) / open_pipe_now)` — reconciles against the row's own numbers
- **In-flight quarter**: falls back to historic median (final booked unknown)

The §02 glide charts are **always** the cross-quarter median.

### 8.5 Product Recommendations

`compute_product_conversion()` derives conversion from the **same pro-rated pipe and booked the §01 product table uses** (not the old SKU-inclusive opp-count basis). This ensures "actual ≥ needed ⟺ on track to hit target" holds per product.

Sealights FY24 excluded from its own product recs (acquired FY24, ~$50K pipe → astronomical outlier conversions).

---

## 9. Product Page — SKU-Mix Pro-Rating

### 9.1 Why It Exists

Old approach: each multi-product opp's whole `Cal_IACV` filed under alphabetically-first `MIN()` family. Result: Sealights FY26 Q1 wk1 open pipe read $0.46M instead of $6.94M.

New approach (since 2026-06-08): pro-rate each opp's `Cal_IACV` across its product families by SKU NACV share.

### 9.2 Key Functions

```python
_product_share_map(opp_products)
# Returns (Opportunity_ID, product, share) where shares sum to 1 per opp.
# Basis = positive NACV per family (NPA credits can't create negative shares).
# Source: [src].[trf_sku_nacv_npa_allocated] via opp_products.sql

_attach_product_weight(snap, shares, value_col='Cal_IACV')
# Fans each opp row out to one row per family.
# w = value_col * share
# Opps with no SKU rows → whole value under 'Other' (NOT defaulted to Tosca)
# Σ w == Σ Cal_IACV → product rows still total page-1 figure
```

### 9.3 Attribution by Metric Type

| Metric | Attribution |
|---|---|
| Open/LS pipe, weeks 1–12 booked | Pro-rated by SKU share |
| Week 13 / latest / future booked | SKU-line from live pull (`_live_booked_product_by_share`) — also uses shares for consistency |
| Conversion rates (for recs) | Pro-rated (same as §01 table) |

### 9.4 opp_products.sql Source

`[src].[trf_sku_nacv_npa_allocated]` — NPA-allocated version of sku_nacv_fact. Upstream has ALREADY split no-product lines to real families. `allocation_weight` is provenance metadata only — do NOT multiply by it (would apply the split twice, overstating ~2.6%).

Correct: `SUM(NACV_USD)` per (opp, canonical family).

---

## 10. Segment (Account Tier) Page

### 10.1 Current State — BROKEN

`[rpt_cx].[account_segment_quarterly]` was **removed from Synapse**. The live segment build collapses every opp to "Unassigned."

**Workaround in production:** `freeze_segment_payload.py` extracted the segment section from the last good dashboard HTML and saved it to `data/inputs/segment_payload_frozen.json`. `coverage_render._segment_payload()` serves this frozen JSON instead of the broken live build when the file exists.

### 10.2 Proposed Replacement

`[sfdc_trf].[account_live]` with `COALESCE(Current_Segment__c, X2019_Segment_expected__c)`:
```sql
account_segment AS (
    SELECT Id,
        CASE WHEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
                  IN ('Tier 1', 'Tier 2', 'Tier 3')
             THEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
        END AS QuarterStartSegment
    FROM [sfdc_trf].[account_live]
)
```

`build_new_segment_dashboard.py` builds a comparison dashboard using this logic without touching the production dashboard.

### 10.3 Tier Convention

**Initial tier** (earliest-ever `QuarterStartSegment`) is deliberate — accounts that graduate tiers over time stay in their starting tier. Historic.xlsx shows current tier; the dashboard shows starting tier.

### 10.4 Canonical Segments

`Tier 1` / `Tier 2` / `Tier 3` / `Unassigned` (opps whose account has no tier — no target, pipe still counted so segment totals tie page 1).

---

## 11. Targets

### 11.1 Files and Sheets

| File | Sheet | Format |
|---|---|---|
| `FY'24 Targets.xlsx` | `Sheet1` | Pivot with subtotal rows |
| `FY'25 Targets.xlsx` | `datalake 25` | Long-format |
| `FY'26 Targets.xlsx` | `datalake_FY26` | Long-format, different column names |

### 11.2 Column Name Mapping (loaders.py normalizes these)

| Canonical | FY24 | FY25 | FY26 |
|---|---|---|---|
| `geo` | `GEO` | `GEO` | `Geo` (not `Geo_top level`!) |
| `deal_type` | `Deal Type` | `Deal Type` | `Deal Type` |
| `segment` | n/a | `Segment` | `Tiers` |
| `Q1`–`Q4` | `Sum of Q1'24` | `Q1'25` | `Q1'26` |

### 11.3 Key Normalizations on Load

- FY24: `New Customer` → `New Business`; `ffill()` geo; drop subtotal rows
- FY25/26: `New business` → `New Business`; `PubSec` → `Public Sector`
- FY26 geo re-bucketing: `AMS Public Sector` → `Public Sector`, `LATAM` → `AMS`
- Segment: `tier 1` → `Tier 1` (`.str.title()`)
- Quarter key canonical form: `"FY26 Q2"` (not `Q2'26` or `Sum of Q2'26`)

### 11.4 Loader Functions

```python
load_quarter_targets()   # → (fiscal_year, quarter, geo, deal_type, target_usd)
load_segment_targets()   # → (fiscal_year, quarter, geo, segment, target_usd) [FY25/26 only]
load_product_targets()   # → (fiscal_year, quarter, product, target_usd) [FY25/26 only]
load_booking_team_mapping(conn)  # → DataFrame from Synapse, filtered to Active
```

---

## 12. Build Pipeline (`build_coverage.py`)

```
Synapse pull (needs VPN):
  pull_snapshot()            → FY24, FY25, FY26 snapshots → concat
  pull_live_booked()         → 2024-01-01 to 2026-12-31
  pull_opp_products()        → SKU-inclusive per-opp families
  load_booking_team_mapping()

Load targets (xlsx):
  load_quarter_targets()
  load_segment_targets()
  load_product_targets()

Build frames:
  build_coverage()           → output/coverage.parquet
  build_segment_coverage()   → output/coverage_segment.parquet
  build_product_coverage()   → output/coverage_product.parquet
  compute_product_conversion() → output/product_conversion.parquet

Compute recommendations:
  compute_recommendations()  → output/recommendations.parquet
                               output/recommendations_per_quarter.parquet
  compute_segment_recommendations() → output/recommendations_segment.parquet
  compute_product_recommendations() → output/recommendations_product.parquet

Render:
  render_dashboard()         → output/coverage_dashboard.html
  meta.json                  → {"asOfDate": "latest snapshot date"}
```

The "as-of" date = latest snapshot_date in the data, NOT the run date.
Re-rendering HTML without re-pulling data keeps the pill on the data's date.

---

## 13. Render Pipeline (`coverage_render.py`)

### 13.1 Key Constants

```python
GEO_DISPLAY = [
    {"code": "All", "label": "All Geos"},
    {"code": "AMS", ...}, {"code": "APAC", ...},
    {"code": "EMEA", ...}, {"code": "Public Sector", ...},
]
SEGMENT_DISPLAY = [...Tier 1/2/3 + Unassigned...]
PRODUCT_DISPLAY = [...Tosca/Testim/qTest/NeoLoad/DI/LiveCompare/Sealights/Vera/RS/Other...]
QUARTER_ORDER = ["FY24 Q1", ..., "FY26 Q4"]
SPARSE_WEEK_THRESHOLD = 8
FROZEN_SEGMENT_PATH = data/inputs/segment_payload_frozen.json
```

### 13.2 Payload Structure

```json
{
  "asOfDate": "2026-06-22",
  "weeksInQuarter": 13,
  "fiscalYears": ["FY24", "FY25", "FY26"],
  "quartersByFy": {...},
  "geos": [...],
  "dealTypes": [...],
  "quarters": {
    "FY26 Q2": {
      "currentWeek": 9,
      "aggregate": { "openPipe": [...13 values...], "booked": [...], "totalCov": [...], "target": 123456789 },
      "cells": {
        "AMS·NB": { "openPipe": [...], "booked": [...], "totalCov": [...], "lsPipe": [...], "recCov": [...13 recs...] },
        ...all geo×dealtype combos...
      },
      "isSparseSnapshot": false,
      "weeksWithData": 9
    }
  },
  "segment": { "segments": [...], "quarters": {...cells keyed geo·segCode...}, "presentQuarters": [...] },
  "product": { "products": [...], "quarters": {...cells keyed by product code...}, "presentQuarters": [...] },
  "strictPerQuarter": {...per-quarter loose conversion lookup...}
}
```

### 13.3 Sparse Quarter Handling in Renderer

`_sparsify_series()`: replaces `openPipe` and `lsPipe` with `max(non-null values)` broadcast to all 13 weeks. Recomputes `totalCov` per week from the flat open_pipe. Does NOT touch `booked` (live booked is complete even when snapshots are sparse).

---

## 14. Known Data Issues and Fixes

### 14.1 Snapshot Table Duplication (active mitigation)

`[rep].[trf_opp_daily_snapshot_new]` emits each (opp, snapshot_date) row **twice** (upstream join blow-up). `pull_snapshot()` calls `drop_duplicates()` immediately after the SQL pull. Without this, open_pipe and ls_pipe are exactly 2× high.

### 14.2 sku_nacv_fact Corruption (resolved 2026-05-29)

Was briefly returning ~32 identical copies per (opp, SKU) with inflated NACV. Fixed upstream. Inner collapse changed MAX → SUM as a result. NPA rows reinstated.

### 14.3 FY24 Q1 Sparse Data

Snapshot table only reached weekly cadence in April 2024. FY24 Q1 has only 2 usable week points (wk 9 and wk 13). Flagged as sparse and excluded from recommendation training.

### 14.4 account_segment_quarterly Removed

See §10. Frozen segment payload is the current production workaround.

### 14.5 Booking-Team Column Name Inconsistency

Three slightly different spellings exist across the codebase:
- `Bookings_Team_static` — snapshot table column
- `Bookings_Team_Static` — mapping table column
- `Booking_Team_Static` — live_booked table column

The live_booked pull renames to `Bookings_Team_static` in Python before the geo join:
```python
live = live.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
```

---

## 15. Dashboard Structure (4 Pages)

Navigation: segmented pill control, state persisted to `localStorage` as `cc-page`.

### Page 01: Dashboard (Geo × Deal Type)
- KPI strip: Open Pipe / LS Pipe / Coverage / Needed Coverage / Target / Booked / Attainment
- Filter bar: FY · Quarter · Week · Deal type · Geo
- §01 Weekly detail table (geo × deal type × week): Open / LS / Booked / Target / LTB / Coverage / LS Coverage / Δ WoW
- §02 Recommended coverage glide (per-cell median historic needed coverage by week)

**Display caps:** Coverage multiples and WoW deltas above |50×| display as grey `>50×`/`·` with exact value in tooltip (denominator noise from near-exhausted LTB). Booked/Target/LTB show 2 decimals (1-decimal rounding caused a $6.7K LTB to read $0.1M → 648.6× coverage).

### Page 02: Segment (Geo × Tier)
- §01 Weekly detail by Geo × Segment, includes Needed column (per-quarter retrospective for closed quarters, historic median for in-flight)
- §02 Recommended coverage glide by geo × tier

### Page 03: Product (Single dimension)
- §01 Weekly detail by Product, includes Needed column
- Testim and Vera rows hidden (no quarterly target) but their $ ARE in Total row
- Total reads All-products cell = KPI = page 1 by construction
- §02 Recommended coverage glide by product (SKU-inclusive conversion basis)

### Page 04: Methodology
- In-dashboard summary of stage buckets, include/exclude rules, week pinning, coverage math

---

## 16. Canonical Product Families

```python
_PRODUCT_CANONICAL = frozenset({
    "Tosca", "Testim", "Data Integrity", "Vera", "qTest",
    "LiveCompare", "NeoLoad", "Sealights", "Recurring Services",
})
# Everything else (null, No_Product_Assigned, AI Credits, etc.) → "Other"
```

SQL family → canonical mapping (same CASE in snapshot.sql, live_booked.sql, opp_products.sql):
- `Tosca OSV`, `TTA`, `TEE`, `Tosca` → Tosca
- `Testim`, `Testim Salesforce`, `TTA for SFDC/SNOW`, `Tricentis Device Cloud`, `Mobile` → Testim
- `Tosca BI`, `Tosca DI` → Data Integrity
- `Tricentis Sealights` → Sealights
- `Record_Type IN ('Service', 'Platnium Support')` → Recurring Services
- `Additional Services` → Recurring Services (opp_products.sql only)
- `Flood` → NeoLoad (opp_products.sql only)

---

## 17. Historical Coverage Reference (All Quarters)

From PLAN.md §20:

| Quarter | Wk1 open $M | Wk1 cov | Wk4 cov | EOQ attainment |
|---|---:|---:|---:|---:|
| FY24 Q1 | (sparse) | ~1.5× | ~1.5× | 101% |
| FY24 Q2 | $69.2M | 3.34× | 3.26× | 102% |
| FY24 Q3 | $78.0M | 3.62× | 2.96× | 107% |
| FY24 Q4 | $117.2M | 3.13× | 2.82× | 99% |
| FY25 Q1 | $88.6M | 3.98× | 3.33× | 75% |
| FY25 Q2 | $96.5M | 3.46× | 3.18× | 76% |
| FY25 Q3 | $99.3M | 3.04× | 2.70× | 86% |
| FY25 Q4 | $189.4M | 3.83× | 3.42× | 81% |
| FY26 Q1 | $99.7M | 4.49× | 3.43× | 73% |
| FY26 Q2 | $99.9M | 3.41× | 2.86× | 24% (in-flight at time of capture) |

**Key signal:** FY24 carried slightly lower coverage but hit target — conversion was meaningfully better in FY24. Conversion has degraded since then (not a calculation artifact).

Win rates by year:

| Deal Type | FY24 | FY25 | FY26 |
|---|---:|---:|---:|
| New Business | 12.9% | 10.9% | 7.0% |
| Expansion | 36.0% | 28.9% | 30.7% |
| Upsell | 56.0% | 52.1% | 52.0% |
| Prof Services | 49.3% | 53.1% | 62.5% |

---

## 18. Design System (Dashboard Visual Language)

Geist / Geist Mono fonts (Google Fonts). Tricentis blue `#1e5fbf` as primary. Orange `#b45309` for needed/warn lines. Green `#047857` for good. Red `#b91c1c` for bad.

**Chart conventions (locked):**
- Actual line = solid blue, 2.25px
- Needed line = dashed orange, 2.25px, 5/4 dash
- Gap band = green (actual ≥ needed) or red (actual < needed), split at intersection
- Per-week dots on actual lines; current/latest week dot larger
- 1× parity reference = 1.5px dashed `--ink-4`

**Never swap actual/needed colors or styles across charts.**

---

## 19. Scripts Reference

### `build_new_segment_dashboard.py`

Builds `output/coverage_dashboard_NEW_SEGMENT.html` using the `account_live` COALESCE tier join instead of `account_segment_quarterly`. Swaps the `account_segment` CTE in both snapshot.sql and live_booked.sql. Defaults to `--as-of 2026-06-02` (the date the frozen original was captured) so only the tier logic changes, not the data vintage. Does NOT touch the production dashboard.

Run: `uv run python scripts/build_new_segment_dashboard.py`

### `freeze_segment_payload.py`

Extracts `payload["segment"]` from a dashboard HTML's `<script id="coverage-data">` tag and writes it to `data/inputs/segment_payload_frozen.json`. Run this whenever you have a known-good HTML before `account_segment_quarterly` was removed.

### `pressure_test_product_alloc.py`

Four assertions against the product allocation math:
- A: Shares sum to 1.0 per opp, no negatives
- B1: Pro-ration identity — Σ w == Σ Cal_IACV per (quarter, week, Stage)
- B2: Page tie — Σ products == page-1 total per (quarter, week)
- C: No-SKU opps → 'Other' (NOT defaulted to a real product)

### `segment_tier_shift_analysis.py`

Quantifies how moving from X2019 (initial/fixed tier) to COALESCE (Current falling back to X2019) reshapes the tier population. Read-only, does not touch the dashboard. Can take an Excel export via `--excel` instead of a live Synapse pull.

---

## 20. Open Issues / Known Gotchas

1. **Segment page broken** — `account_segment_quarterly` removed from Synapse. Frozen payload is the production stopgap. New tier source pending confirmation (see §10).

2. **Booking-team column naming** — three spellings exist. Always rename on entry before geo join.

3. **NPA rows** — must be KEPT in the live booked pull. Their Uplift-record-type add-backs are already excluded by the Record_Type filter. Excluding NPA rows overstated FY26 Q1 booked by ~$1.75M.

4. **Snapshot duplication** — `drop_duplicates()` in `pull_snapshot()` is load-bearing. Remove it and open_pipe doubles.

5. **opp_products.sql `allocation_weight`** — do NOT multiply NACV_USD by allocation_weight. The upstream table already applied the split. Using it again overstates booked ~2.6%.

6. **SKU-line vs pro-rated** — booked at week 13 / future standing uses SKU-line attribution (via `_live_booked_product_by_share` which applies shares for consistency). Weeks 1–12 use pro-rated snapshot. Both sum to the same company total.

7. **`_override_inflight_latest_booked`** — called in all three builders (deal-type, segment, product) so pages keep tying each other at the current week.

8. **Dense product grid** — `build_product_coverage` explicitly reindexes each quarter to the full (product × week) grid to prevent products with no late-pipe from silently dropping their target from the aggregate LTB.

9. **`perQuarterNeed()` JS helper** — shared between Segment and Product pages. Returns per-quarter retrospective needed for closed quarters, historic median for in-flight. The §02 glide always shows the median.

10. **Coverage >50×** — displayed as grey `>50×` (near-exhausted LTB = denominator noise). Exact value shown in tooltip.

---

## 21. Running the Pipeline

```bash
# Full pipeline (needs VPN + valid .env with SYNAPSE_CONN_STR)
uv run python -m backend.build_coverage

# Template-only re-render (no Synapse pull, uses cached parquets)
uv run python -m backend.coverage_render

# Comparison segment dashboard (new tier logic, no production change)
uv run python scripts/build_new_segment_dashboard.py --as-of 2026-06-02

# Validate product allocation math
uv run python -m scripts.pressure_test_product_alloc

# Analyse multi-product opp mix
uv run python -m scripts.analyze_multi_product_opps         # open pipe
uv run python -m scripts.analyze_multi_product_opps booked  # Closed Won
```
