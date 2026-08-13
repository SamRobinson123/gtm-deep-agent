# Coverage Curve Dashboard — the standalone project, and how to maintain it

**When to load**: Maintaining, debugging, re-running, or rebuilding the Coverage
Curve Analysis dashboard — the weekly open-pipe-vs-left-to-book curve with its
self-contained HTML UI. For coverage *mechanics* on the snapshot table inside
this repo, load [`../analysis/coverage-curve.md`](../analysis/coverage-curve.md)
instead.

**Where it lives**: `Coverage Curve Analysis/` at this repo's root (path has
spaces — quote it). It is its OWN git repository nested inside this one, with
its own `.venv`, `.env` (never read — credential rule applies), and CLAUDE.md.
Work on it happens in that folder with its own git history; nothing there is
imported by `pipeline/` or `agent/`.

**The authoritative reference is the project's own
`COVERAGE_CURVE_CONTEXT.md`** (659 lines) — verified 2026-08-13 to match the
working-tree code (which is AHEAD of its last commit, 2026-05-29; the doc
post-dates the code changes). Read it before any change. `planning/PLAN.md`
holds the full methodology. This file carries only what routing and judgment
need; it does not replace that reference.

---

## What it is

One question: at each week of a quarter, how many dollars of open pipe per
dollar still to book — `coverage = open_pipe / (target − booked)` — and how
does that curve compare to prior quarters at the same week of life. Weekly
manual run: pull daily opp snapshots from Synapse, pin one snapshot per week,
compute coverage, render a self-contained HTML dashboard (no server, no build
step). Complements `gtm-weekly-reporting/` (also in that folder) — this is the
time-series curve that tool doesn't have.

## Running it

```bash
cd "Coverage Curve Analysis"
uv run python -m backend.build_coverage        # full: Synapse pull -> parquet -> HTML (VPN + its own .env)
uv run python -m backend.coverage_render       # re-render only, from cached parquets
uv run python scripts/build_new_segment_dashboard.py --as-of 2026-06-02   # tier-logic comparison build
uv run python -m scripts.pressure_test_product_alloc                      # product-allocation assertions
```

The dashboard's as-of pill = latest `snapshot_date` in the data, NOT the run
date. Output: `output/coverage_dashboard.html` + parquets + `meta.json`.

## Sources and the dollar-metric rule

| Table | Used for |
|---|---|
| `[rep].[trf_opp_daily_snapshot_new]` | open_pipe, ls_pipe, weeks 1–12 booked (`Cal_IACV`) |
| `[src].[sku_nacv_fact]` | live booked (`Product_NACV = NACV_USD − Uplift_USD`) |
| `[src].[trf_sku_nacv_npa_allocated]` | per-opp SKU mix for the product page |
| `[sharepoint].[Map_Booking_Team_Static_live]` | geo bucketing (AMS/APAC/EMEA/Public Sector) |

**Never mix the two dollar metrics**: `Cal_IACV` (snapshot) is pipe;
`Product_NACV` (sku) is booked. `Total_NACV` is pulled and deliberately unused.
Geo comes from `BTS_RegionFamily`, NOT `BTS_Geo` — `BTS_Geo` loses Public
Sector.

## The three load-bearing mechanics

1. **Week pinning** (`_pin_to_week_date`): one snapshot per (quarter, week).
   Every week pins to its EARLIEST in-quarter snapshot (start-of-week balance;
   week 1 therefore anchors to the quarter-start snapshot and ties PBI starting
   pipe) — EXCEPT the in-flight current week, which pins to the LATEST snapshot
   to tie gtm-weekly-reporting's as-of-today number. A 14-day pre-quarter
   snapshot buffer exists so week 1 anchors correctly.
2. **Booked source split by week**: weeks 1–12 = snapshot `Cal_IACV` where
   Stage='Closed Won'; week 13 (final) = live `Product_NACV` cumulative (ties
   Historic.xlsx / PBI); the in-flight week is overridden to the live total
   (`_override_inflight_latest_booked`, called in all three builders so pages
   tie each other); future quarters get a single week-1 standing point.
3. **Recommendation engine**: per (geo, deal_type, week),
   `rec_coverage = 1 / median(conversion)` over closed non-sparse quarters,
   where `conversion = (final_booked − booked_at_week_N) / open_pipe_at_week_N`
   — deliberately "loose" (credits pipe that entered after week N). Sparse
   quarters (< 8 weeks of data; FY24 Q1) are excluded from training.

## Gotchas that produce silently wrong numbers (§14/§20 of the reference)

- `pull_snapshot()`'s `drop_duplicates()` is load-bearing — the snapshot table
  emits every row twice; removing it doubles open_pipe.
- NPA rows must be KEPT in the live booked pull (real de-bookings; excluding
  them overstated FY26 Q1 by ~$1.75M) and the inner collapse is SUM, not MAX.
- Do NOT multiply by `allocation_weight` in opp_products — upstream already
  applied the split; using it again overstates ~2.6%.
- Three spellings of the booking-team column exist (`Bookings_Team_static` /
  `Bookings_Team_Static` / `Booking_Team_Static`); rename on entry before the
  geo join.
- Multi-product opps are PRO-RATED across families by SKU NACV share (shares
  sum to 1; no-SKU opps go to 'Other', never defaulted to a real product).
- **Segment page is BROKEN upstream**: `[rpt_cx].[account_segment_quarterly]`
  was removed from Synapse; production serves a frozen payload
  (`data/inputs/segment_payload_frozen.json`). The proposed replacement is
  `[sfdc_trf].[account_live]` with
  `COALESCE(Current_Segment__c, X2019_Segment_expected__c)`; the comparison
  build exists and the decision is the operator's.
- Targets come from three differently-shaped Excel files (`FY'24/25/26
  Targets.xlsx`); `data/inputs/loaders.py` owns every normalization
  (`New business`→`New Business`, `PubSec`→`Public Sector`, FY26 `Geo` not
  `Geo_top level`, quarter keys as `FY26 Q2`).

## UI / design system (locked — rebuilds must match)

Vanilla JS + inline SVG in one template (`frontend/dashboard_template.html`),
payload embedded as JSON in `<script id="coverage-data">`. Geist / Geist Mono;
Tricentis blue `#1e5fbf` primary; orange `#b45309` = needed/warn; green
`#047857` good; red `#b91c1c` bad. Chart conventions are locked: actual =
solid blue 2.25px with per-week dots (current week dot larger); needed =
dashed orange 2.25px (5/4 dash); gap band green where actual ≥ needed, red
below, split at the intersection; 1× parity line = 1.5px dashed. **Never swap
actual/needed colors or styles.** Four pages (Dashboard, Segment, Product,
Methodology) behind a segmented pill nav persisted to `localStorage['cc-page']`;
coverage above |50×| renders as grey `>50×` with the exact value in tooltip.

## Boundaries for the agent

Changes to that project happen in its own folder under its own git, honoring
its own CLAUDE.md (simplest code wins). Its `.env` holds a connection string —
never read it; if a rebuild needs Synapse from THIS repo instead, the `query`
tool is the path. Validate product-allocation changes with
`pressure_test_product_alloc` before reporting numbers from a rebuilt
dashboard.
