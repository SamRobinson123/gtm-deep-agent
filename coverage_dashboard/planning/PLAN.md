# Coverage Curve Analysis

## Project Specification

## 1. Vision

A weekly-cadence analytical tool that pulls daily opportunity snapshots from Azure Synapse and renders an interactive HTML dashboard showing how **pipe coverage** has evolved week by week — within the current quarter and across prior quarters at the same point in their lifecycle.

The cousin tool `gtm-weekly-reporting` reports *this week's* pipeline. This tool's distinguishing job is the **coverage curve**: a per-quarter weekly time series of `open_pipe / left-to-book`, plotted so the analyst can see the curve shape, compare quarters at the same week of life, and spot whether coverage is gliding healthily toward 1.0× or stalling.

Sibling project to `gtm-weekly-reporting` (separate codebase; may share data source and conventions; does **not** consume its outputs).

## 2. The Question This Tool Answers

> *"At each week of a quarter, how many dollars of open pipe were sitting in front of the team for every dollar still to book — and how does that curve compare to prior quarters at the same week of life?"*

Concrete sub-questions:

- Is coverage gliding healthily from a high opening multiple toward 1.0× at quarter close, or is it stalling?
- How does **FY26 Q3 at week 6** compare to **FY26 Q2 at week 6** and **FY25 Q4 at week 6**?
- Are we above or below the historic coverage trajectory at this point in the quarter?
- Is `ls_cov` (late-stage coverage) tracking close to `total_cov` (high-confidence pipe), or is there a wide gap (early-stage pipe-heavy)?
- **Per region family:** which sub-rollups (AMS East, EMEA DACH, APAC, Public Sector, etc.) are tracking ahead of historic norms, and which are behind?

## 3. Inputs

### 3.1 Source tables

| Table | Grain | Role |
|---|---|---|
| `[rep].[trf_opp_daily_snapshot_new]` | one row per (opportunity, snapshot_date) | primary — pipeline state over time |
| `[src].[sku_nacv_fact]` | one row per (opportunity, SKU) | joined only for product / geo, optional for coverage |
| `[sharepoint].[Map_Booking_Team_Static_live]` | one row per booking team | `Bookings_Team_static` → Region Family / Geo / Region / Segment / Product Family / VP / FLM. Live mirror of the BI team's reference table; same columns as the legacy `Map_Booking_Team_Static.csv`. Pulled with `SELECT * FROM [sharepoint].[Map_Booking_Team_Static_live]`. See §5. |

Each row of the snapshot table is **one opportunity as it stood on one `snapshot_date`**. Walking the `snapshot_date` values traces how the pipeline looked through a quarter — meaning history is **native to the source**, not something we have to accumulate locally.

> ⚠️ **Source duplication (open pipe / LS pipe).** As of 2026-05-28 `[rep].[trf_opp_daily_snapshot_new]` returns each `(opp, snapshot_date)` row **byte-identical twice** (an upstream join blow-up). `pull_snapshot()` applies `drop_duplicates()` to restore the documented one-row-per-(opp, snapshot_date) grain *before* any aggregation — without it `open_pipe` and `ls_pipe` sum each opp twice and read exactly **2×** high. See §6.3.

### 3.2 Reference inputs (Excel)

The booking-team mapping was previously read from a CSV on SharePoint; it's now sourced from Synapse (see §3.1, `[sharepoint].[Map_Booking_Team_Static_live]`). Only the Excel targets files remain as file-based inputs:

| File | Source | Role |
|---|---|---|
| `FY'24 Targets.xlsx`, sheet `Sheet1` | Strategic Analytics planning workbook (`data/inputs/`) | Quarterly target ACV $ at **GEO × Deal Type** grain (high-level only). Pivot-style export with subtotal rows that need to be filtered. Deal-type values use `New Customer` — **rename to `New Business` on load** (canonical project naming, see §8.4). See §8. |
| `FY'25 Targets.xlsx`, sheet `datalake 25` | Strategic Analytics planning workbook (`data/inputs/`) | Quarterly target ACV $ at fine grain (business unit × prod fam × Product × GEO × Source × Deal Type × Segment). Long-format datalake. Deal-type values use `New business` (already canonical, minor case-normalization to `New Business`). See §8. |
| `FY'26 Targets.xlsx`, sheet `datalake_FY26` | Strategic Analytics planning workbook (`data/inputs/`) | Quarterly target ACV $ at fine grain. Similar to FY25 but with different column names (`Product Family_L2`, `BU_2`, `Geo`, `Geo_top level`, `Tiers`, `Solex`). Deal-type values use `New business` (canonical). See §8. |
| `bookings` (TBD) | (TBD) | Historic actual bookings — useful for sanity-checking targets or as a backup. Not currently required. |

### 3.3 The canonical Synapse pull

> *Consolidated 2026-06-07: this section (with §3.3a, §6.3 and §11a) absorbs the retired `METHODOLOGY.md` / `METHODOLOGY.docx` / `planning/NEEDED_PIPELINE_METHODOLOGY.md` — PLAN.md is now the single methodology reference.*

The snapshot SQL lives at `backend/sql/snapshot.sql` (pulled per fiscal year by `backend/snapshot.py::pull_snapshot()`, with `snap_start = fy_start − 14 days` so the end-of-prior-quarter snapshots are available for week-1 anchoring). The four filter predicates that govern what makes it in:

1. **`Cal_IACV != 0`** — drops zero-value rows. ~2.5% of survivors have a NULL Product; those are labeled `Unassigned` rather than dropped. (Doesn't affect coverage since we don't slice by product.)
2. **`Bookings_Team_static NOT IN ('Account Management', 'Global', 'QAS Account Management')`** (and not NULL) — restricts the pull to net-new / expansion sales pipeline.
3. **`Raw_Stage` exclusions** — drops admin / duplicate / churn stages outright (`Closed - Duplicate`, `Stage 6 - Closed - Admin`, `Stage 7 - Churned`, `Opportunity Rejected`, `Stage 0 - Renewal Outreach Not Started`, `0 - First Interaction`).
4. **FY date range on both `snapshot_date` and `CloseDate`** — both bounded by the fiscal-year window.

The query joins two helper CTEs: `opp_product` (the opp-level `MIN(CASE Family…)` canonical product — single attribution, see §3.5a) and `account_segment` (the account's **initial** tier = earliest-ever `QuarterStartSegment` from `[rpt_cx].[account_segment_quarterly]` — the segment page's deliberate convention; accounts that later graduate stay in their starting tier). The `Stage` CASE maps `Raw_Stage` → Open / Closed Won / Closed / Other per §4 — note `6 - Closed/Pending` maps to **Closed Won** (a pending-close deal is *not* open pipe; it shows as booked).

### 3.3a Included / excluded — quick reference

| Item | Open / LS pipe | Booked |
|---|---|---|
| Source table | `trf_opp_daily_snapshot_new` (snapshot) | `sku_nacv_fact` (live) |
| Metric | `Cal_IACV` (gross) | `Product_NACV` = `NACV_USD − Uplift_USD` (net) |
| `6 - Closed/Pending` | not in open pipe (it's "Closed Won" stage) | **included as booked** |
| `Closed Won`, `Stage 5 - Closed Won` | not in open pipe | included |
| Open funnel (Discovery → Negotiation) | included in open pipe | not booked |
| `Closed Lost`, `Closed Deferred` | excluded | excluded |
| Admin/dup/churn/rejected/renewal-outreach/first-interaction | excluded outright | excluded |
| Non-quota teams (AM, Global, QAS AM, null) | excluded | excluded |
| NPA negative-adjustment rows | n/a | **kept** (net the bookings) |
| Zero-value rows (`Cal_IACV`/`NACV_USD` = 0) | excluded | excluded |
| Deal types beyond NB/Exp/Upsell/PS | included if non-null (no target → no coverage) | excluded (live pull filters to the four) |

### 3.4 Columns the coverage dashboard actually uses

`Opportunity_ID`, `Cal_IACV`, `snapshot_date`, `CloseDate`, `Raw_Stage`, `Deal_Type`, `Stage`, `Bookings_Team_static` (for the region-family join).

Other columns from the pull (`Account_Id`, `Total_NACV`, `CreateDate`, `Product`, `Geo`) are kept for consistency with the cousin pipeline but unused by the coverage builder.

**Deal_Type naming.** The cousin project's SQL renames `Opp_Type = 'New Business'` → `'New Customer'` via `CASE WHEN`. **This project does not.** Canonical naming here is `New Business` (matches the targets files — see §8.4). Our snapshot SQL should pass `Opp_Type` through unchanged.

### 3.5 `Cal_IACV` vs `Total_NACV` vs `Product NACV` (live SKU pull)

Three dollar concepts live in this pipeline. They are **not equal**.

| Metric | Source | Grain | Used for |
|---|---|---|---|
| `Cal_IACV` | `[rep].[trf_opp_daily_snapshot_new]` | opp × snapshot_date | `open_pipe`, `ls_pipe` — matches PBI open-pipe report |
| `Total_NACV` | `[rep].[trf_opp_daily_snapshot_new]` | opp × snapshot_date | unused (selected but ignored) |
| `Product_NACV` = `NACV_USD − Uplift_USD` | `[src].[sku_nacv_fact]`, `Period='Period_1'` | opp × SKU | `booked` — matches PBI bookings report |

`Cal_IACV` is gross (including uplift); `Total_NACV` is net of uplift; `Product_NACV` is per-SKU net-of-uplift NACV that, summed per opp, lines up with what Finance / PBI report for bookings.

**Why two sources for one dashboard.** Historic investigation (2026-05-25) showed the snapshot's `Cal_IACV` over-states booked by ~3% relative to the live PBI Bookings number. Two root causes:
1. **`6 - Closed/Pending` mapped to `Closed Won`.** Eight Q1 2025 opps in `6 - Closed/Pending` at their latest 2025 snapshot were never finalized in live data — pure phantoms in `Cal_IACV`-based booked.
2. **CloseDate revision drift.** Opps whose `CloseDate` was in Q1 2025 mid-year but later revised to 2024-12-01 / 2026-04-01 / etc. still show in the snapshot's Q1 2025 window because the audit override picked their latest 2025 snapshot.

Fix: keep `Cal_IACV` for open pipe (it matches PBI open-pipe at the daily level), but compute `booked` from the live `sku_nacv_fact` table — see §6.3. Live data reflects the current Salesforce state, so phantom-pending and revised-CloseDate opps drop out naturally.

**Read the right metric for each measure: `Cal_IACV` for open/late-stage pipe, `Product_NACV` for booked. Do not mix.**

### 3.5a The live pull (`backend/sql/live_booked.sql`) — filters and grain

Pulled by `backend/snapshot.py::pull_live_booked()` over `LIVE_BOOKED_START..END` (2024-01-01 → 2026-12-31). Included: `Period = 'Period_1'`, `NACV_USD != 0`, `Record_Type ∈ {Product, Service, Platinum support}`, `Deal_Type ∈ {New Business, Expansion, Upsell, Professional services}`, the same non-quota-team exclusions as the snapshot, and `StageName ∈ {6 - Closed/Pending, Closed Won, Stage 5 - Closed Won}` (**pending counts as booked**, matching the snapshot stage taxonomy). **`NPA…` adjustment rows are KEPT** — real negative de-bookings/credits the source-of-truth nets in (dropping them over-stated FY26 Q1 by ~$1.75M); their `Uplift`-record-type add-backs fail the Record_Type/team filters, so the SUM nets correctly. Each `(opp, SKU)` collapses with **SUM, not MAX** (a booking plus a later negative correction must net).

**Grain = one row per (opp × product family), SKU-LINE attribution (2026-06-07).** Each family carries exactly its own SKU lines' dollars — the same product cut as `Historic.xlsx` — so the product page's booked ties Historic **product-by-product** (validated for all 9 closed quarters: e.g. FY25 Q1 Tosca $7.73M, NeoLoad $2.11M, worst diff ±$0.000M). The earlier one-row-per-opp grain filed each whole deal under one alphabetical-`MIN()` family, moving multi-product deals' dollars between families in both directions (Tosca read $3.7M, NeoLoad $2.9M). Grains that group by opp-level dimensions (geo / deal type / tier) are unaffected: the family rows partition each opp's dollars, so their sums are identical. The 'Other' bucket can be **negative** (Historic's blank-product adjustment lines — Excel pivots silently hide them). Also carried: `Quarter_Start_Segment` (initial tier, same convention as the snapshot) so the segment page's live booked keeps the tier cut.

**Product $ attribution — pro-rated by SKU mix (Sam, 2026-06-08).** Every $ column on the product page is split across the families an opp carries, so multi-product deals contribute to each. `opp_products.sql` returns one row per (opp, family) **with `nacv`**; `_product_share_map()` turns that into per-opp shares (`positive nacv_family / Σ positive nacv`, so NPA credits don't make negative shares), and `_attach_product_weight()` fans each snapshot opp row out to one per family with `w = Cal_IACV × share`. Because shares sum to 1 per opp, **Σ w = Σ Cal_IACV** — the product rows still total the page-1 figure to the dollar (validated: all quarters/weeks tie). Opps with no SKU rows (~7%) keep their whole value under "Other". This replaced the old whole-opp `MIN()` attribution, which dumped a multi-product opp's entire pipe under the alphabetically-first family — Sealights FY26 Q1 wk-1 open pipe read **$0.46M → $6.94M** (real coverage ~0.4× → **6.27×**); qTest/Tosca were under-counted, Data Integrity/Recurring Services over-counted.

| Metric | Attribution | Source |
|---|---|---|
| Open/LS pipe $, weeks 1–12 booked | **pro-rated** snapshot `Cal_IACV` by SKU share | `opp_products.sql` `nacv` weight |
| Booked $ (wk 13 / latest / future standing) | **SKU-line** (already each family's own $) | live `live_booked.sql` |
| Rate metrics (conversion → needed coverage) | **SKU-inclusive** (opp counts under every family, weight ignored) | `opp_products.sql` pairs |

All three sum/tie to the company total; the rate metrics deliberately overlap (don't sum to "All") because numerator and denominator must share units — single attribution starved multi-product families (Sealights' degenerate 98.7% conversion / 1.5× need). The one approximation in the pro-rating: SKU shares come from the *current* `sku_nacv_fact` applied to every week an opp was open (an opp's mix rarely changes mid-life).

## 4. Stage Taxonomy

`Raw_Stage` → `Stage` mapping (computed in SQL):

| `Raw_Stage` values | `Stage` |
|---|---|
| `Closed Deferred`, `Closed Lost` | `Closed` |
| `6 - Closed/Pending`, `Closed Won`, `Stage 5 - Closed Won` | `Closed Won` |
| `Closed - Duplicate`, `Stage 6 - Closed - Admin`, `Stage 7 - Churned`, `Opportunity Rejected`, `0 - First Interaction` | `Other` |
| everything else | `Open` |

**Late stages** (the `LATE_STAGES` constant — used to subset `Stage = 'Open'` into `ls_pipe`):

- `3 - Executive Presentation`
- `4 - Technical Evaluation`
- `5 - Negotiation / Business Procurement`
- `6 - Closed/Pending`
- `Stage 4 - Closed Pending`

## 5. Region Family Taxonomy

The leaf grain of pipeline data is `Bookings_Team_static`. The dashboard reports at **Region Family** — a rollup level (e.g. `AMS East`, `EMEA DACH`, `APAC`, `Public Sector`). The mapping is pulled from Synapse via `SELECT * FROM [sharepoint].[Map_Booking_Team_Static_live]` — same columns as the legacy `Map_Booking_Team_Static.csv` it replaces.

### 5.1 The CSV is not a drop-in source — three corrections are required

The CSV's `BTS_RegionFamily` column has **16 distinct values**, finer than the **~11 buckets** the planning workbook's targets are keyed on. Using the column raw would silently split rollups (APAC into ANZ/Asia/Japan, etc.) and break the targets join. Three normalization steps are required:

**(a) Rename the CSV's finer values back to canonical bucket names** (the cousin project's "Decision A"):

| `BTS_RegionFamily` (CSV) | Canonical bucket |
|---|---|
| `APAC ANZ`, `APAC Asia`, `APAC Japan` | `APAC` |
| `AMS LATAM` | `LATAM` |
| `Pubsec` | `Public Sector` |

All other `BTS_RegionFamily` values pass through unchanged.

**(b) Accept the CSV's classification where it disagrees with prior hand-coded values** (Decision B from the cousin project's migration). Two teams will land in different rollups than the legacy dict, and these are **expected corrections**, not bugs:

- `EMEA Core Nordics` → `EMEA North` (previously `EMEA DACH`)
- `EMEA Core MEA South` → `EMEA North` (previously `EMEA South`)

(The cousin project's migration doc also lists `AMS DevOps` and `EMEA DevOps` shifts, but those booking teams are defunct — see §5.3 — so the shifts no longer apply here.)

**(c) Normalize casing on the join key.** Data may emit `EMEA Core Benelux` while the CSV has `EMEA Core BeNeLux`. Case-insensitive matching or lowercase normalization on both sides is required, or BeNeLux pipeline silently fails to map.

### 5.2 Do not use `BTS_Territory` as the join key

`BTS_Territory` is **coarser** than `Bookings_Team_static` — it collapses multiple booking teams into one territory (e.g. `EMEA Core Nordics` + `EMEA Core BeNeLux` both → `EMEA Core BeNeLux Nordics`). Joining on it loses the leaf grain. The mapping join must be `Bookings_Team_static`.

### 5.3 Filter convention for "current rows"

The mapping is filtered to **`Active Team == 'Active'`** in Python after the Synapse pull (could also be applied in the SQL `WHERE [Active Team] = 'Active'`). This is the project's canonical "currently staffed" filter — implemented in `data/inputs/loaders.py::load_booking_team_mapping(active_only=True)`.

**Why not `BTS_Is_Curr == 1`?** The cousin project's migration doc warns that `Active Team` drops `AMS DevOps` and `EMEA DevOps` (which have `Active Team = ""`). That warning **does not apply to this project**: those booking teams are defunct. The `Active Team` filter correctly drops them.

Consequence: do not expect any rows with `BTS_RegionFamily = 'DevOps'` to appear in the dashboard. If a future booking team needs to be re-introduced as "active," updating the source CSV's `Active Team` column is the right intervention — no code change.

### 5.3a Bucketing comparison with the cousin project

The cousin (`gtm-weekly-reporting`) reaches the same 4 buckets via a different path. Documented here for diff-awareness — our approach is functionally equivalent.

| Step | Cousin (`pipeline.py`) | This project (`coverage_builder.py`) |
|---|---|---|
| Source columns joined | `Booking_Team_Static` → `REGION_FAMILY_MAP` (hardcoded dict) → `Region Family`; separately, snapshot's `Geo` column | `Bookings_Team_static` → `BTS_RegionFamily` (Synapse mapping) |
| Override for Pubsec | `Geo_View = np.where(Region Family == 'Public Sector', 'Pubsec', Geo)` | `_bucket_region_family()` collapses any RegionFamily starting with "AMS"/"EMEA"/"APAC" to that geo; `Pubsec` → `Public Sector` |
| Result bucket names | `AMS`, `EMEA`, `APAC`, **`Pubsec`** | `AMS`, `EMEA`, `APAC`, **`Public Sector`** |
| LATAM handling | Region Family `AMS LATAM` → Region `LATAM` (kept separate via `region_of()`) | Collapsed into `AMS` |

The naming divergence (`Pubsec` vs `Public Sector`) is intentional — our project chose the more explicit name (see §5.1(a)). It costs us a rename when joining to anything keyed at the cousin's grain.

### 5.4 Other columns available from the same CSV

Loaded into the same DataFrame but unused for v1 region-family slicing — kept as future enhancements:

- `BTS_Geo` — top-level Geo (AMS / APAC / EMEA / Pubsec)
- `BTS_Region` — a rollup between Region Family and Geo
- `BTS_Segment`, `BTS_GeoSegment` — Core / Enterprise / Public Sector
- `BTS_ProductFamily`, `BTS_GeoProductFamily` — Core / DevOps / Sealights / Public Sector
- `BTS_FLM`, `BTS_VP` — management drill-down
- `AI_Strategist` — internal field

## 6. From Snapshot Rows to a Weekly Series

### 6.1 Quarter assignment

A deal belongs to the quarter its **`CloseDate`** falls in. For each quarter, the builder keeps rows where **both** `CloseDate` and `snapshot_date` fall in that quarter — i.e. snapshots taken *during* the quarter being measured.

### 6.2 Weekly downsampling

Source is daily. For each ISO week, pin to a **single snapshot date** so every opp counted in week N is observed on the same calendar date. The pin rule (`_pin_to_week_date()` in `coverage_builder.py`):

- **Every week → beginning-of-week balance** = earliest in-quarter snapshot in the week (standing pipe at the start of the week). Week 1's earliest in-quarter = the start-of-quarter snapshot (Apr-1 for Q2, Jan-1 for Q1), so PBI's "starting pipe" falls out automatically (FY26 Q2 wk1 = $106.10M ≈ PBI $106.18M; Q1 = $104.77M). Needs the ~2-week pre-quarter buffer in the pull (`snap_start`) only to exclude it (week 1 = first in-quarter snap, not the buffer).
- **Exception — the in-flight current week** (current quarter's latest week with snapshot data) → its **latest** snapshot (today's standing pipe), matching `gtm-weekly-reporting`'s `resolve_snapshot_date`, so the current week **ties to GTM to the dollar** (FY26 Q2 wk9 = $46.78M = GTM's 2026-06-01 run). Past weeks stay on their week-start balance.

**History (Sam, through 2026-06-01).** Pinning evolved several times: (1) *all-Friday/max*; (2) *earliest-per-week*; (3) week 1 = end-of-prior-quarter (Dec-31 ≈ $104.3M); (4) week 1 = start-of-quarter (Jan-1/Apr-1, PBI ≈ $104.8M/$106.1M); (5) every week = latest-in-week (matched GTM but dropped the PBI week-1 tie — wk1 read the end-of-week-1 value ≈ $99.7M/$99.9M); (6) hybrid: week 1 = start-of-quarter, weeks 2–13 = latest-in-week; (7) **current: beginning-of-week for every week** (earliest in-quarter snap = week-start standing pipe; week 1 = start-of-quarter = $106.1M falls out), **except the in-flight current week = latest snapshot** (today, GTM tie $46.78M). So the curve reads "pipe at the start of each week" and the live week reads "as of now." NOTE: `_prep_snapshot_for_recs` filters to in-quarter snapshots, so it uses the same beginning-of-week rule; the recommendation weekly series uses week-start open pipe.

**Why pin to one date at all (the original fix):** an even earlier version kept "the latest snapshot per opp per ISO week" instead of pinning the whole week to one date. That meant opp A could be measured on Friday while opp B was measured on Tuesday (because B had no Friday snapshot — e.g., it moved to `Closed - Duplicate` and dropped out of the SQL pull mid-week). The result was a "kitchen-sink" weekly slice that over-counted by ~$5M. Pinning every opp in the week to one calendar date fixes that, regardless of whether that date is the week's first or last.

### 6.3 The three measures, per weekly snapshot

| Measure | Source | Definition |
|---|---|---|
| `open_pipe` | snapshot (`Cal_IACV`) | sum of `Cal_IACV` where `Stage = 'Open'` |
| `ls_pipe` | snapshot (`Cal_IACV`) | sum of `Cal_IACV` where `Stage = 'Open'` **and** `Raw_Stage ∈ LATE_STAGES` |
| `booked` | **snapshot `Cal_IACV` wks 1–12; live total at wk 13** (see below) | weeks 1–12: snapshot `Cal_IACV` where `Stage='Closed Won'` per week; week 13: live `Product_NACV` total (true final, ties Historic.xlsx); current quarter: snapshot for past weeks, **live total so far at its latest week** (2026-06-05); future: live standing. **Both paths count `6 - Closed/Pending` as Closed Won.** |

**De-duplication (protects `open_pipe` / `ls_pipe`).** `pull_snapshot()` calls `drop_duplicates()` on the raw pull because the source currently emits every `(opp, snapshot_date)` row twice (§3.1). This runs *before* the `Stage`/`Raw_Stage` aggregation, so each opp's `Cal_IACV` is counted once. Both pipe measures derive purely from the snapshot's `Cal_IACV` and were **unaffected** by the (now-resolved) `sku_nacv_fact` corruption that hit `booked` (the product/geo join in the snapshot SQL collapses per opp via `GROUP BY`). Open-pipe `Cal_IACV` continues to match the PBI open-pipe report.

**`booked` source issue — resolved 2026-05-29.** A 2026-05-28 upstream blow-up had `[src].[sku_nacv_fact]` returning ~32 byte-identical copies per `(opp, product)` with the `NACV_USD` value inflated ~11–32×, making `booked` read ~10× high. The source was fixed on/before 2026-05-29 (0 identical-copy dups remain in 2024–2026). Fixing the source then exposed two now-wrong defenses in `live_booked.sql`, both corrected: (1) the inner `(opp, SKU)` collapse changed `MAX` → **`SUM`** (a handful of SKUs carry a booking + a later negative correction; `MAX` dropped the correction); (2) **`NPA…` rows are now KEPT** — they are real negative adjustments (de-bookings/credits) that the source-of-truth nets in, not junk placeholders (their `Uplift`-record-type add-backs stay excluded via the `Record_Type` + `Account Management` filters). Validated opp-by-opp against the user's `Historic.xlsx` ("Stage = Closed Won", sum `Product NACV` by `Opp Close Date`): **all 8 closed quarters FY24 Q1–FY25 Q4 match to the dollar**; FY26 Q1 = $16.61M vs Historic $16.63M (0.1%). `Historic.xlsx` is the booked source-of-truth — reconcile against it if booked ever drifts.

**Booked = snapshot `Cal_IACV` for weeks 1–12, live TOTAL at week 13 (Sam, 2026-06-01).** `booked` is sourced by **week**:

| Week | `booked` source | Why |
|---|---|---|
| **Weeks 1–12** (every quarter) | **snapshot** `Cal_IACV` where `Stage='Closed Won'`, per week | Same gross metric as open pipe (consistent coverage), frozen, matches `gtm-weekly-reporting`. The weekly cumulative progression. |
| **Week 13** (final week) | **live** `sku_nacv_fact` total for the quarter (`_live_booked_by_week()` wk-13 cumulative `Product_NACV`) | The true final bookings — ties to `Historic.xlsx`/PBI. Snapshot Closed-Won understates the final total. |
| **Current in-flight quarter** (FY26 Q2) | snapshot for past weeks; **latest week = live total so far** (`_override_inflight_latest_booked()`, Sam 2026-06-05) | The current week answers "what have they ACTUALLY booked" from the live source — intentionally ≠ GTM's snapshot booked (gross `Cal_IACV` incl. pending vs net live `Product_NACV`). Open pipe still ties GTM. |
| **Future** (FY26 Q3/Q4) | total live `Product_NACV` so far, at week 1 (`_future_standing_frame()`, §6.4) | Pre-stamped Closed-Won deals; single current-standing total. |

The live override is applied to **week-13** rows (`wk13_mask`) and, since 2026-06-05, to the **in-flight quarter's latest week** (`_override_inflight_latest_booked()`, all three builders so page 1 / segment / product keep tying); all other weeks keep their snapshot Closed-Won booked. Examples: FY26 Q1 wk12 = $11.17M (snapshot) → wk13 = $16.61M (live ≈ Historic.xlsx $16.63M); FY26 Q2 wk8 = $6.90M (snapshot) → wk9 (current) = $9.06M (live so far, vs $9.17M snapshot).

**Both booked paths count `6 - Closed/Pending` as Closed Won.** The snapshot stage CASE maps it to `Closed Won`; `live_booked.sql` includes it via `StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won')` (e.g. ≈ $0.43M of FY26 Q2's ~$8.7M).

**Why the week-13 split.** Snapshot Closed-Won `Cal_IACV` *understates* the final-quarter total (FY26 Q1 wk13 snapshot = $11.17M vs live total $16.61M ≈ Historic.xlsx $16.63M) — late deals book after the last in-quarter snapshot. So weeks 1–12 use snapshot for a consistent gross-metric progression matching GTM, while week 13 jumps to the live total so the end-of-quarter booked ties to the source-of-truth. (Evolved this session: snapshot-historic → live-all-quarters → snapshot-historic → **snapshot wks 1–12 + live wk13**.)

**`_live_booked_by_week()` mechanics:** derive `(quarter, week_of_quarter)` from each live SKU row's `Opp_Closed_Date`, `_attach_geo`, `sum(Product_NACV)` per `(quarter, geo, deal_type, week)`, reindex to 13 weeks, cumulative-sum. Only the **week-13** slice (= the full-quarter cumulative total) is merged onto the frame and overrides `booked` at week 13.

### 6.4 Future quarters — current standing pipeline (Sam, 2026-05-29)

A quarter whose start date is **after the run date** (e.g. FY26 Q3/Q4 when run in Q2) has no in-quarter snapshots, so the §6.1 `close_q == snap_q` filter yields nothing — the weekly curve can't exist yet. But the pipe and bookings for those deals exist *today*. `_future_standing_frame()` (in `coverage_builder.py`) detects these quarters automatically (`_quarter_start(q) > pd.Timestamp.now()`) and emits a **single week-1 "current standing" row per (geo, deal_type)**:

- `open_pipe` / `ls_pipe` = the **latest snapshot's** open pipe for deals whose `CloseDate` falls in that quarter.
- `booked` = total live Closed-Won so far for the quarter (not split by week).
- every target `(geo, deal_type)` combo is included (zero-filled) so the **full target** flows through — this is what fixed FY26 Q4 reading $56.8M instead of its true $59.0M.

`build_coverage` drops the scattered live-booked-only rows for these quarters and substitutes this frame. The renderer treats them as non-sparse (a single week-1 point, not a thin historical quarter), so `currentWeek = 1` and the KPIs/cells read the standing values. Coverage = `open_pipe / (target − booked)` as usual (FY26 Q3 ≈ 3.2×, Q4 ≈ 3.2× at the 2026-05-28 snapshot). No weekly trajectory is drawn — there's no history yet, just "what's standing in front of the team today for that quarter."

## 6a. Cousin project's coverage convention — how `gtm-weekly-reporting` does it

`gtm-weekly-reporting/src/pipeline.py::build_deal_type_table` is the canonical reference for Geo × Deal Type coverage. Read 2026-05-23 via SharePoint sync.

```python
# Cousin's deal-type table — Geo × Class (New/Existing)
geos = ["AMS", "EMEA", "APAC", "Pubsec"]
classes = ["New", "Existing"]   # 2 buckets, not 3
# Per (geo, class):
#   Total Pipe = sum(NACV_M) where Stage == 'Open'
#   LS Pipe    = sum(NACV_M) where Stage == 'Open' AND Is_LS
#   ACV        = sum(NACV_M) where Stage == 'Closed Won'   (the "booked" value)
#   Target     = DEAL_TYPE_TARGETS_M[(fy, quarter, geo, class)]
ltb = Target - ACV
Total_Pipe_Cov = where(ltb > 0, Total_Pipe / ltb, NaN)
LS_Pipe_Cov    = where(ltb > 0, LS_Pipe    / ltb, NaN)
```

### What's the same as our project

- Coverage formula: `pipe / (target − booked)` with `NaN` when `LTB ≤ 0` ✅
- Stage definitions (`Open`, `Closed Won`, `Late-Stage` membership) ✅
- Single $-amount metric: `Cal_IACV` at opp grain ✅
- Per-quarter, per-snapshot calculation ✅

### What differs (5 things)

1. **Deal-type bucket count**: cousin = **2** (`New`, `Existing`); ours = **3** (`New Business`, `Expansion`, `Upsell`). The cousin's `deal_type_class()`:
   ```python
   def deal_type_class(dt):
       if dt == "New Customer":                                     return "New"
       if dt in ("Expansion", "Upsell", "Professional Services"):   return "Existing"
       return None  # rows with other Deal_Type get dropped
   ```
2. **Canonical deal-type names**: cousin = `New` / `Existing`; ours = `New Business` / `Expansion` / `Upsell` (and we don't pull `Professional Services` from Synapse).
3. **Booked filter**: cousin sums *all* Closed Won rows in the quarter; we filter `CloseDate ≤ snapshot_date` (so an opp flagged Closed Won *before* its CloseDate is not counted yet).
4. **Geo bucket name**: cousin = `Pubsec`; ours = `Public Sector` (intentional, §5.1).
5. **Naming of the "booked" measure**: cousin column is `ACV`; ours is `booked`. Same calculation underneath.

### Why this matters

The user has asked us to *align with the cousin's approach for Geo × Deal Type coverage* (2026-05-23). The biggest semantic divergence — **deal-type bucket count** — was resolved in favor of keeping our finer grain (`New Business` / `Expansion` / `Upsell`, plus `Professional Services`); we do **not** adopt the cousin's 2-bucket `New` / `Existing` collapse.

## 7. The Coverage Formula

Applied per weekly snapshot:

```
LTB        = target − booked           (Left-to-Book)
total_cov  = open_pipe / LTB           (None when LTB ≤ 0)
ls_cov     = ls_pipe   / LTB           (None when LTB ≤ 0)
```

- `total_cov` answers: *for every dollar still to book, how many dollars of open pipe are in front of the team?*
- `ls_cov` is the same question restricted to late-stage pipe — a tighter, higher-confidence read.
- When `LTB ≤ 0` (team has already booked the full target), coverage is undefined and stored as `None`. The dashboard renders a gap, not a spike to infinity.

### Interpretation guide

- A healthy quarter **opens well above 1×** (commonly 3–5×).
- The line **glides down toward 1×** as the quarter progresses: pipe converts to bookings, `open_pipe` falls, `booked` rises, `LTB` shrinks.
- Coverage **staying high late** = pipe is not converting.
- Coverage **below 1× early** = pipeline-generation problem.
- `ls_cov` close to `total_cov` = pipe is mostly late-stage (good); wide gap = pipe is mostly early-stage.

## 8. Targets

### 8.1 Sources — one file per fiscal year, three different formats

Three workbooks live in `data/inputs/`:

| File | Sheet | Format | Grain |
|---|---|---|---|
| `FY'24 Targets.xlsx` | `Sheet1` | Pivot export w/ subtotal rows | GEO × Deal Type (high-level only) |
| `FY'25 Targets.xlsx` | `datalake 25` | Long-format datalake | BU × ProdFam × Product × GEO × Source × Deal Type × Segment |
| `FY'26 Targets.xlsx` | `datalake_FY26` | Long-format datalake (different column names) | Product Family_L2 × business unit × BU_2 × Geo × CONCAT BU × Geo_top level × Source × Deal Type × Solex × Tiers |

Sanity checks: FY24 grand total = **$100.4M**; FY25 total = **$134.2M**; FY26 TBD on first load.

Each file has 50+ sheets of Strategic Analytics working sheets; **load only the named sheet above**. The other sheets are not stable contracts.

### 8.2 Per-year column-name reconciliations

The three files use different column names for the same logical fields. The loader normalizes them to one canonical column set before any aggregation:

| Canonical column | FY24 source | FY25 source | FY26 source |
|---|---|---|---|
| `geo` | `GEO` | `GEO` | `Geo_top level` |
| `deal_type` | `Deal Type` | `Deal Type` | `Deal Type` |
| `product` | (not present) | `Product` | `Product Family_L2` |
| `business_unit` | (not present) | `business unit` | `business unit` |
| `source` | (not present) | `Source` | `Source` |
| `segment` | (not present) | `Segment` | `Tiers` |
| `Q1` ... `Q4` (per FY) | `Sum of Q1'24` ... | `Q1'25` ... | `Q1'26` ... |

FY24 is the coarsest — has only `geo` and `deal_type`. FY25 and FY26 are at fine grain but with different column names.

The FY26 file additionally has a `datalake_FY24,FY25` sheet that combines those two years at FY25 grain. **Not used as a source** — the per-year files are canonical.

### 8.3 Aggregation strategy

The dashboard needs targets at three grains. For each fiscal year:

1. **Top-level quarterly** = `sum(Q{n})` across all rows.
2. **Per-Deal-Type quarterly** = `groupby(deal_type).sum(Q{n})` — three buckets (`Expansion`, `New Business`, `Upsell`).
3. **Per-GEO quarterly** = `groupby(geo).sum(Q{n})` — four buckets (`AMS`, `APAC`, `EMEA`, `Public Sector`).

These are the only three grains the dashboard reports at. Other dimensions (product, source, segment, business unit) are aggregated away. The fine-grain dimensions remain available in the loaded DataFrame for future drill-downs.

### 8.4 Value normalizations on load

Canonical project naming: deal-type bucket is **`New Business`** (matches FY25/FY26 sources, title-cased). The snapshot SQL must not rename `New Business → New Customer`; FY24's `New Customer` must be renamed to `New Business` on load. See §3.4.

| Issue | Fix on load |
|---|---|
| FY24 `Deal Type` uses `New Customer`; FY25/FY26 use `New business` | Rename `New Customer` → `New Business` (FY24) and case-normalize `New business` → `New Business` (FY25/FY26). Canonical is `New Business`. |
| FY25/FY26 use `PubSec` for the public-sector GEO; canonical bucket per §5.1(a) is `Public Sector` | Rename `PubSec` → `Public Sector` on load |
| FY24 has subtotal rows (`AMS Total`, `EMEA Total`, …, `Grand Total`) | Filter rows where `deal_type` is NaN — leaves only leaf rows |
| FY24 has forward-fill blank GEO cells (pivot export) | `ffill()` the geo column before filtering |
| Quarter column names vary by year (`Sum of Q1'24`, `Q1'25`, `Q1'26`) | Melt to long with canonical `quarter` key `"FY24 Q1"`, `"FY25 Q1"`, etc. (§8.6) |

### 8.5 Region-family targets — the granularity gap (carried forward)

The dashboard reports at **Region Family** (`AMS East`, `EMEA DACH`, `APAC`, `Public Sector`, …) per §5. All three targets files are at **GEO** (`AMS`, `APAC`, `EMEA`, `Public Sector`).

GEO is *coarser* than Region Family. Mapping up is straightforward (the booking-team CSV's `BTS_Geo` column rolls any region family up to a GEO). For **GEO-sized region families** in the dashboard (the top APAC / LATAM / Public Sector rows, which already collapse sub-regions per §5.1(a)) targets are available directly.

For **sub-GEO region families** (`AMS East`, `AMS West`, `AMS South`, `EMEA DACH`, `EMEA North`, `EMEA South`, …) no real target exists in any file. Three options (carried from prior draft, decision still pending §18 Q11):

- Use the proxy (each region family's actual final bookings) — v1 recommendation.
- Allocate GEO target down to region family by historic booking share.
- Wait for Finance to provide region-family-level targets.

### 8.6 Quarter key convention

Canonical key: **`"FY{nn} Q{n}"`**, e.g. `"FY24 Q1"`, `"FY25 Q3"`, `"FY26 Q2"`. Two digits for the year, matching the source-spelling pattern minus the apostrophe.

The loader returns the canonical key. Nothing downstream sees `Q1'24` / `Sum of Q2'25` / `Q3'26`.

### 8.7 Proxy fallback (still needed, narrower scope)

With three years of real targets, the proxy is now a corner case rather than the default:

- It still applies for **sub-GEO region families** in any year (no real target exists).
- It still applies for **future quarters not yet in any uploaded file** (e.g. FY27 before that file lands).
- It does **not** apply for FY24, FY25, or FY26 at top-level / per-Deal-Type / per-GEO grains — real targets are available.

`target_is_proxy` is still computed per quarter so the dashboard can flag which mode each cell is in.

### 8.8 Unified loader output

`load_quarter_targets()` returns one tidy long-format DataFrame with all three years stitched together. Suggested schema:

| Column | Type | Notes |
|---|---|---|
| `fiscal_year` | str | `"FY24"` / `"FY25"` / `"FY26"` |
| `quarter` | str | `"FY24 Q1"` etc. |
| `geo` | str | `AMS` / `APAC` / `EMEA` / `Public Sector` (post-normalization) |
| `deal_type` | str | `Expansion` / `New Business` / `Upsell` (post-rename) |
| `target_usd` | float | Raw $; the builder converts to $M for display |
| (optional) `product`, `business_unit`, `source`, `segment` | str | NaN where the source year doesn't have them (FY24) |

Builder calls `.groupby()` on whichever dimensions it needs.

### 8.9 ACV vs Cal_IACV — still unconfirmed

Targets files all use **`ACV`** as the metric (FY24 has no Metric column but title is "Targets ACV"; FY25 explicitly has `Metric = ACV`; FY26 TBD on inspection). Snapshot uses `Cal_IACV`. Unresolved: is "ACV" gross-of-uplift (= `Cal_IACV`) or net (= `Total_NACV`)? See §18 Q13.

## 9. Outputs — Dashboard design (locked 2026-05-24)

Single self-contained `output/coverage_dashboard.html`. JSON payload injected into a `<script id="coverage-data">` tag, rendered by vanilla JS + hand-rolled SVG, opens in any browser, no server, no build step.

### Aesthetic direction

Modern shadcn-style web app, **Tricentis design system** — same look as `gtm-weekly-reporting/handoff/preview_dashboard_v3.html`. Soft cards with hairline borders, two-layer shadows, generous radii (10px). Strong information hierarchy: large numbers, restrained chrome.

### Type system (mandatory)

- **Sans**: `Geist` weights 400/500/600/700 — page titles, headings, body
- **Mono**: `Geist Mono` weights 400/500 — every number on the page, axis labels, KPI values
- Both loaded from Google Fonts (`fonts.googleapis.com/css2?family=Geist...&family=Geist+Mono...`)
- **Never** revert to Inter / Arial / Helvetica / system-ui

### Color palette (CSS variables)

Light theme (`[data-theme="light"]`):
```
--background:    #ffffff       --surface:       #ffffff       --surface-2:     #f8fafc
--foreground:    #0f172a       --ink-2:         #334155       --ink-3:         #64748b       --ink-4:         #94a3b8
--primary:       #1e5fbf       (Tricentis blue — accent, current quarter, "carrying" line)
--primary-soft:  #eaf2fd       (tint backgrounds)
--good:          #047857       --good-soft:     #ecfdf5       (positive deltas, "above needed")
--good-band:     rgba(4,120,87,0.16)
--bad:           #b91c1c       --bad-soft:      #fee2e2       (negative deltas, "below needed")
--bad-band:      rgba(185,28,28,0.14)
--warn:          #b45309       --warn-soft:     #fffbeb       (target line, "needed" line color)
--border:        #e2e8f0       --border-strong: #cbd5e1
--cur-line:      #1e5fbf       (actual coverage / pipe lines)
--need-line:     #b45309       (needed coverage / pipe lines — dashed)
```

Dark theme inverts surface/ink, lightens primary to `#60a5fa`, lightens warn to `#fbbf24`. Same semantic accents.

### Layout (top to bottom)

The 1200px-max-width `.shell` contains seven blocks, in order:

1. **Topbar** (sticky) — Tricentis logo PNG (`frontend/assets/tricentis-logo.png`) + brand name "Coverage Adequacy / Tricentis · GTM Pipeline Trajectory", live-snapshot pill, "As of {date}" pill, sun/moon theme-toggle button.
2. **Intro** — h1 "What pipe did we need — and what did we have?" + lead paragraph.
3. **KPI strip** — pinned to the latest snapshot (`DATA.defaultQuarter` at `currentWeek`), **not** to the filter selection. Three cells: Open Pipe / Late-Stage Pipe / Coverage. Each shows label, big mono value (34px), WoW delta pill. Coverage value rendered in Tricentis blue. Header row shows "● Latest snapshot · {quarter} · Wk N / 13" + "As of {date}".
4. **Filter bar** — single horizontal row, four groups separated by thin vertical dividers: `FY · Quarter · Deal type · Geo`. Each group is a label + pills. "All Geos" and "All Deal Types" are the first option in their respective rows.
5. **Hero answer card** — colored left rail (blue/red/green based on adequacy). Two badges (status: closed/in-flight, conversion %). h2 slice title "{Deal Type} · {Geo} · {Quarter}". One-sentence answer with **color-coded keywords**: actual coverage in `var(--cur-line)`, needed in `var(--need-line)`, attainment in good/bad. 4-stat strip below (Target / Booked / Attainment / Coverage).
6. **Two stacked charts**:
   - **§01 Coverage trajectory** — actual line (blue solid, 2.25px, with per-week dots), needed line (orange dashed, 2.25px, 5/4 dash), 1× parity reference. **Gap band** between the two lines: green where actual ≥ needed, red where below. Bands split at intersection points (see `buildGapBand` in template).
   - **§02 Pipeline composition $M** — booked area (slate tint) at bottom, actual open pipe stacked on top (blue), needed open pipe line (orange dashed) where needed_pipe = `LTB × needed_coverage`. Gap band between actual-open and needed-open. Target horizontal dashed line in green.
7. **§03 Weekly detail table** — Quarter inherits from filter bar, but the table has its **own Week pill** (1–13, disabled beyond `currentWeek`). 16 rows × 10 columns at (real-geo × real-deal-type) grain (excludes the "All" pseudo-options); coverage cell color-coded green/red. Total row at bottom with aggregate needed coverage.

Plus colophon footer with definition / method / source.

### Interaction patterns

- **State persistence**: theme, FY, quarter, dt, geo, and table-week all saved to `localStorage` so reloads keep selection.
- **Filter pills**: rounded `--radius-sm` (6px), filled-blue when active, hover gives soft-blue background. Disabled state is faded.
- **Hover on charts**: vertical dashed crosshair snaps to nearest week, tooltip pops above showing all values for that week (actual cov, needed cov, open pipe, booked, LTB, etc.).
- **"All" pseudo-options**: at the head of Geo and Deal Type pill rows. Selecting "All Geos" aggregates across geos (sum); "All Deal Types" aggregates across deal types. Both → grand aggregate. Recommendations for these aggregates are **dollar-weighted across historic quarters**, not averages of per-cell recommendations.
- **Reveal animation**: top-down staggered fade-in on first load (`.reveal` class, 0.45s cubic-bezier easing, 0.06s delay step).

### Chart conventions (lock these for any new chart)

- Mono axis labels (10px, `--ink-3`).
- Hairline grid lines at `--border` opacity 0.6.
- Per-week dots on actual lines, current/latest week dot rendered larger (4.5px vs 2.8px).
- Reference lines (1× parity, target) are 1.5px dashed at `--ink-4`.
- **Actual = solid blue, Needed = dashed orange** — never swap colors or styles between charts.
- **Gap shading** is the headline visual move: it should be present anywhere we compare actual vs needed.

### Why this look

The user explicitly chose this direction over earlier editorial-magazine and dark-trading-terminal experiments. Locked 2026-05-24. Future sections should match these conventions rather than re-invent.

### Dashboard sections (current scope — four-page structure, updated 2026-06-05)

The original single-page layout, and the later Overview/Deep-Dive two-tab structure, were both retired (the Overview tab was removed entirely in the 2026-06-05 dead-code sweep). Top-level navigation is a segmented pill control persisted to `localStorage` as `cc-page`, with four pages: **01 Dashboard · 02 Segment · 03 Product · 04 Methodology**. The visual system, palette, type, and chart conventions above still apply.

**01 Dashboard** (geo × deal type — the analyst page):
1. KPI strip — row 1 (Open Pipe / LS Pipe / Coverage / Needed Coverage) + row 2 (Target / Booked / Attainment). All week- and filter-driven (the KPI follows the Geo/Deal-type/Week pills; the §01 Total row never does — a recurring "why don't these match" trap). Needed Coverage is per-quarter loose conversion for closed quarters, hidden for in-flight quarters.
2. Filter bar — FY · Quarter · Week · Deal type · Geo.
3. **§01 Weekly detail by Geo × Deal Type** — one row per (geo × deal type): Open / LS / Booked / Target / LTB / Coverage / LS Coverage / Δ WoW ×2. Coverage cell colored ok/bad vs needed. PS appears here only. **Display conventions (2026-06-06, all three §01 tables):** Booked/Target/LTB show 2 decimals (1-decimal rounding made a $6.7K LTB read as $0.1M — EMEA·T3 FY26 Q2 displayed 648.6× coverage while hand-math on the rounded cells said 44×); coverage multiples and WoW deltas above |50×| display as grey `>50×`/`·` with the exact value in the tooltip (near-exhausted LTB = denominator noise; same cap convention as the §02 glides).
4. **§02 Recommended coverage glide** — per-cell median historic needed coverage × 13 weeks. The per-cell replacement for the blanket "carry 4×" rule.

**02 Segment** (geo × account tier, initial-tier convention):
1. Segment KPI strip — its Needed Coverage card follows the same quarter-state rule as the §01 table below (shared `perQuarterNeed()` JS helper).
2. **§01 Weekly detail by Geo × Segment** — same columns as the Dashboard table plus a **Needed** column. **Needed is quarter-state-dependent (2026-06-05):** for a CLOSED quarter it is that quarter's own retrospective needed at the selected week — `1 ÷ ((final booked − booked by now) ÷ open pipe now)` — so it reconciles against the row's own numbers (AMS·T1 FY26 Q1 wk1 = 6.1×); for the IN-FLIGHT quarter it falls back to the historic median (final booked unknown). Totals row follows the same rule on summed cells; Coverage coloring judges against whichever mode is active.
3. **§02 Recommended coverage glide** — historic-median carry schedule by geo × tier (this one is *always* the median — cross-quarter guidance).

(The §03 Win-rate-by-tier and §04 Pipe-conversion-by-tier charts were dropped 2026-06-07 per Sam — mirroring the product page's chart removal — along with their backend chain: `compute_segment_win_rate`, the `_eoq_won_lost` helper, `win_rate_segment.parquet`, and the `segmentWinRate` payload key.)

**03 Product** (single dimension, no geo; single-attribution $ via the snapshot's MIN() product, SKU-inclusive rates):
1. Product KPI strip (All-products) — its Needed Coverage card follows the same quarter-state rule as the Segment page (shared `perQuarterNeed()`): closed quarter → that quarter's own retrospective needed; in-flight → the SKU-inclusive historic median.
2. **§01 Weekly detail by Product** — Testim and Vera rows are hidden (no quarterly target) but their dollars ARE in the Total row: the Total reads the All-products cell directly (the same series the KPI reads, 2-decimal display), so KPI = Total = page 1 by construction. Carries a **Needed** column (re-added 2026-06-05) under the same quarter-state rule as the Segment table: closed → per-quarter retrospective; in-flight → SKU-inclusive median. Coverage coloring judges vs that column. **All $ columns are SKU-mix-attributed** (open/LS pipe + wks 1–12 booked pro-rated by SKU share; final/latest booked SKU-line from the live pull — see §3.5a). Sums to the company total to the dollar; booked ties Historic.xlsx product-by-product. Geo/deal-type/segment grains are unaffected (they don't slice by product).
3. **§02 Recommended coverage glide** — needed coverage from **SKU-inclusive** conversion (`opp_products.sql`; an opp counts toward every product family it carries — single attribution starved small products and gave Sealights a degenerate 1.5× need; SKU-inclusive gives 6.8×). Final booked in the conversion = the **live quarter total** (2026-06-05, same source as the other pages' wk-13 booked), so the "All Products" glide row ties the Dashboard and Segment glides exactly (verified 0.0 difference at every week).

(The §03 Win-rate-by-product and §04 Pipe-conversion-by-product charts were dropped 2026-06-05 per Sam, along with their backend chain — `compute_product_win_rate`, `win_rate_product.parquet`, and the `productWinRate`/`productConversion` payload keys. The SKU-inclusive `product_conversion` frame itself stays: the §02 needed-coverage recs consume it.)

**04 Methodology** — in-dashboard summary of stage buckets, include/exclude rules, week pinning, coverage and needed-coverage math (the full reference is this document — §3.3/§3.3a/§3.5a/§6/§7/§11a).

Section §-numbers are presentation labels tied to page position, not stable IDs across versions (see Open Questions Q64).

## 10. Data Flow

```
Synapse                                Python                                HTML
-------                                ------                                ----
[rep].[trf_opp_daily_snapshot_new]
        │  (snapshot SQL, §3.3)
        ▼
   snapshot DataFrame ─┐                  pull_snapshot()          backend/snapshot.py
                       │
[src].[sku_nacv_fact]  │  (live SKU SQL — §6.3)
        │              ▼
        └─►  live_booked DataFrame        pull_live_booked()       backend/snapshot.py
                       │
[sharepoint].[Map_Booking_Team_Static_live]
        │  SELECT *
        ▼
   mapping DataFrame ──┤   load_booking_team_mapping()    data/inputs/loaders.py
                       │
                       ▼
                  build_coverage(snapshot, mapping, targets,
                                 live_booked=live_booked)  backend/coverage_builder.py
                  ├─ _attach_geo()  → snapshot & live
                  ├─ snapshot → open_pipe / ls_pipe / (raw booked, unused)
                  ├─ _live_booked_by_week() → cumulative booked per week
                  ├─ outer-merge live_booked onto snapshot frame
                  └─ override `booked` with live value
                          │
                          ▼
                     coverage DataFrame
                          │
                          ▼
                  render_dashboard_html()                   backend/coverage_render.py
                          │
                          ▼
                  coverage_template.html  (JSON injected into
                                            <script id="coverage-data">)
                          │
                          ▼
                  coverage_dashboard.html
```

## 11. Slices

- **Quarter** (X-axis grouping)
- **Region Family** (via `Map_Booking_Team_Static.csv`, normalized per §5.1). Targets available at GEO level only — sub-GEO families use the proxy target (§8.4).
- **Deal Type** (`Deal_Type`: New Business / Expansion / Upsell). Real targets available (§8.2).

Out of scope for v1: product, region sub-rollups below region family, segment, VP, source. The mapping CSV (§5.4) and the targets file (§8.1) both expose these dimensions — cheap to add later.

## 11a. Needed Coverage — the recommendation engine (consolidated methodology)

*Added 2026-05-23 as the project's "north-star" question; shipped as the §02 glides + Needed columns. This section absorbs the retired `NEEDED_PIPELINE_METHODOLOGY.md` (2026-06-07).*

### What we compute

For each `(geo, deal_type, week_of_quarter)`, look across closed FY24 and FY25 quarters and answer: **what coverage multiple did teams actually need to carry at this week to hit target?**

If Upsell historically converts at 50% (half of week-4 pipe becomes booked by EOQ), an Upsell team needs only **2× coverage** at week 4. If New Business converts at 25%, that team needs **4× coverage** at week 4 — and the generic "carry 4×" guidance is exactly right for them but a waste for Upsell.

### The math

For each closed quarter Q, each cell (geo, deal_type), and each week N:

```
conversion(Q, geo, dt, N) = booked_by_EOQ(Q, geo, dt) − booked_at_week(Q, geo, dt, N)
                          ───────────────────────────────────────────────────────
                                 open_pipe(Q, geo, dt, N)
```

This is "of the open pipe sitting at week N, what fraction closed by quarter-end?"

Then **recommended coverage at week N**:

```
rec_coverage(geo, dt, N) = 1 / median( conversion(Q, geo, dt, N)  for Q in closed_quarters )
```

We use the **median** rather than the mean to limit outlier-quarter influence. The training set is **all complete quarters** (Sam, 2026-06-05): closed quarters whose *snapshot* reached week 13 (the `open_pipe.notna()` guard, applied in the deal-type, segment AND product recs, keeps the live-booked wk-13 reindex from sneaking the in-flight quarter in with a bogus low conversion), minus **sparse quarters** (fewer than `SPARSE_WEEK_THRESHOLD = 8` open-pipe weeks — FY24 Q1, with 2 of 13). Currently FY24 Q2 → FY26 Q1 = 8 quarters, identical across all three pages. Quarters where the team missed target by a wide margin are kept in — they reflect "real conversion rates" even when undershooting; the recommendation is calibrated to those realities. Sam twice declined narrowing the set to recent quarters (e.g. FY25+) even though regime-shifted cells (AMS Tier 1: FY24 converted 34–48%, recent 12–19%) make the all-history median sit between eras — the §01 tables now answer "what did THIS quarter actually need" separately (per-quarter Needed for closed quarters), which resolves that tension without shrinking the training set.

"Loose" conversion: it credits **all** bookings between week N and EOQ — including pipe that entered the funnel after week N. Deliberate: it reflects how teams actually performed carrying X pipe at week N, not a frozen-pipe hypothetical. Side effect: short-cycle slices can exceed 100% conversion → `rec_coverage < 1×` ("this cell needs less standing pipe because new pipe lands and closes mid-quarter"). Conversions where `open_pipe ≤ 0` or `conversion < 0` are dropped as edge cases. Aggregation grains — (geo, dt), (All, dt), (geo, All), (All, All) — are each computed **from scratch** at that grain (dollar-weighted), never averaged from cell-level numbers; this is why the All-row glides on the deal-type, segment and product pages tie each other exactly (verified 0.0 difference at every week — same opp population partitioned three ways, same live final booked).

### Why this is not "1 ÷ win rate"

Win rate is a single quarter-level number (won ÷ (won+lost)). Needed coverage is a **per-week curve**, because the denominator (open pipe) shrinks as losers drop out over the quarter — high early (pipe still full of eventual-losers), declining toward EOQ (pipe gets "purer"). A flat `1/win_rate` can't capture that glide path.

### Needed pipe (at display time)

`needed_pipe(week N) = LTB(week N) × rec_coverage = (target − booked_at_week_N) × rec_coverage(geo, dt, N)` — computed in the dashboard JS so it always uses the latest per-week booked.

### Product-page recs — PRO-RATED conversion (matches the §01 table)

The product glide (`compute_product_conversion`) computes conversion on the **same pro-rated SKU-mix pipe and booked the §01 table uses** (Sam, 2026-06-08): open pipe / booked-by-week are the snapshot `Cal_IACV` pro-rated by SKU share (`_attach_product_weight`), and `booked_eoq` is the live **SKU-line** total per family. So the §02 needed coverage (1 ÷ median conversion) and the §01 actual coverage it colors against share one pipe definition — apples-to-apples, "actual ≥ needed ⟺ on track to hit target" holds per product. Because shares sum to 1, the product rows sum to the "All" row, and the **"All" glide ties the deal-type/segment glides exactly** (verified 0.0 at every week). This replaced an earlier SKU-inclusive basis (each family counted the opp's whole pipe) — that was a workaround for the old `MIN()` attribution starving multi-product pipe (Sealights' degenerate 98.7% conversion); pro-rating fixes it at the source (Sealights pro-rated wk-1 pipe ~$6.9M, conversion ~15%, needed ~5.4×). One robustness note: very early quarters where a product had near-zero pipe can throw a huge single-quarter conversion (Sealights FY24 Q3: $21K pipe → 23×) — the cross-quarter **median** absorbs it.

### Quarter-state "Needed" on the §01 tables (vs the always-median glide)

The §01 detail tables and KPI strips answer a different question per quarter state (shared `perQuarterNeed()` JS helper): **closed quarter** → that quarter's OWN retrospective needed at the selected week, `1 ÷ ((final booked − booked-by-now) ÷ open-pipe-now)` — reconciles against the row's own numbers (cov ≥ need ⟺ the cell hit target from that point); **in-flight** → the historic median (final booked unknown). The §02 glides are *always* the cross-quarter median — prospective carry guidance. This split resolved the recurring "the median looks wrong vs my hand math" reports (Sam's checks were per-quarter: APAC·T1 4.2×, AMS·T1 6.1× = FY26 Q1's own needed vs medians 3.6×/3.8×) without narrowing the training set, which Sam twice declined — regime-shifted cells (AMS T1: FY24 converted 34–48%, recent 12–19%) make the all-history median sit between eras by design.

### Edge cases (carried forward)

- **Sample size**: ~8 observations per cell-week; thin slices (PS, tiny tiers) are noisy — `n_quarters` + `conversion_min`/`max` are exposed in the parquets for sanity checks.
- **Professional Services** has no target → conversion computes but there's no LTB; directional only.
- **Cells that never hit target**: their conversion reflects it and the needed multiple is high — correct, the bar *is* high there.
- **Near-exhausted LTB**: coverage multiples and WoW deltas above |50×| display as grey `>50×`/`·` (exact value in tooltip) — denominator noise, not signal (the 648.6× incident: $6.7K LTB).

## 12. Run Cadence

Manual, weekly. The analyst runs the entry script on Monday. The tool:

1. Pulls the latest fiscal-year snapshot from Synapse.
2. Loads the booking-team mapping CSV from SharePoint and joins.
3. Builds the per-quarter weekly series (overall + region-family + deal-type breakdowns).
4. Computes coverage columns.
5. Renders `coverage_dashboard.html`.

The snapshot table is the source of truth for history — **no local time-series store is required**. Re-running the script is naturally idempotent.

## 13. Repo Layout (current state)

```
coverage-curve-analysis/
├── backend/
│   ├── __init__.py
│   ├── synapse.py                 # Synapse connection helper (pyodbc + .env)
│   ├── snapshot.py                # pull_snapshot() + pull_live_booked()
│   ├── coverage_builder.py        # build_coverage() — joins, weekly downsample, coverage math, live-booked merge
│   ├── build_coverage.py          # Entry point: pull → build → save parquet + summary
│   └── sql/
│       ├── snapshot.sql           # The canonical snapshot pull query
│       └── live_booked.sql        # Live SKU NACV pull from [src].[sku_nacv_fact] (§6.3)
├── data/
│   ├── inputs/
│   │   ├── __init__.py
│   │   ├── loaders.py             # load_booking_team_mapping (SQL), load_quarter_targets (xlsx)
│   │   └── FY'2{4,5,6} Targets.xlsx   # Real reference data (gitignored)
│   └── __init__.py
├── frontend/
│   ├── assets/                    # tricentis-logo.png
│   └── dashboard_template.html    # Dashboard template (JSON payload injected at render)
├── output/
│   └── coverage.parquet           # Latest coverage frame (gitignored)
├── planning/
│   └── PLAN.md                    # This document
├── tests/                         # (not yet present) pytest, focused on coverage math + mapping join
├── .env                           # Synapse credentials (gitignored, .env.example committed)
├── .env.example
├── pyproject.toml                 # uv-managed Python project
└── README.md
```

Entry point: **`uv run python -m backend.build_coverage`** (needs VPN + a valid `.env`). One command does the whole pipeline: pulls snapshot + live-booked + mapping + targets, builds the coverage frame, writes `output/*.parquet`, and renders `output/coverage_dashboard.html`. It forces UTF-8 stdout on startup (`_force_utf8_stdout()`), so it runs cleanly on a Windows cp1252 console without needing `PYTHONIOENCODING=utf-8` — earlier that crash surfaced misleadingly as "Synapse pull failed".

## 14. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12, `uv`-managed | Matches cousin project; analyst familiarity |
| Synapse client | Same library as cousin project | Reuse credentials/patterns |
| Data wrangling | pandas | Standard |
| Dashboard | Single HTML file + inline React via Babel standalone | No build step, no server |
| Charts | Recharts via CDN | Cousin precedent |
| Tests | pytest | Standard |

Deliberate non-choices: no local time-series cache, no FastAPI / server, no Docker, no LLM, no frontend build.

## 15. Out of Scope

- Real-time data — weekly cadence is the contract.
- Region slicing below Region Family (e.g. specific booking teams, sub-regions). (Product and segment/tier slicing, originally out of scope, shipped as the 03 Product and 02 Segment pages.)
- Forecasting / predictive coverage.
- Replacing `gtm-weekly-reporting`.
- Authentication, server deployment.

## 16. Known Caveats

- **Sparse early quarters.** If the snapshot table doesn't extend back far enough, an early quarter may have only a few weekly points. Not comparable to full quarters; the dashboard flags this in a footnote.
- **Proxy target.** Until real targets are loaded into `QUARTER_TARGETS_M`, coverage reads as sufficiency vs. actual result, not attainment vs. plan. Curve always converges to 1.0× at close by construction.
- **Opp-grain, `Cal_IACV`.** Numbers will differ from any SKU-grain or `Total_NACV`-based pull. For this dashboard the opp-grain `Cal_IACV` snapshot is canonical.
- **Region-family expected shifts.** Adopting the canonical CSV mapping moves four teams (`AMS DevOps`, `EMEA DevOps`, `EMEA Core Nordics`, `EMEA Core MEA South`) into different rollups than the cousin project's pre-migration dict. These are corrections, not bugs.

## 17. Cleanup (FinAlly Artifacts) — Complete

This repo previously hosted the FinAlly trading-workstation capstone. The legacy FastAPI scaffolding under `backend/` was deleted on 2026-05-22 when the folder was repurposed to house this project's coverage-pipeline modules (§13).

README.md and CLAUDE.md were rewritten earlier to match this project.

---

## 18. Open Questions

Genuinely-unresolved questions, consolidated from the review rounds. Original IDs are kept in parens for traceability. Resolved / superseded / rejected / applied items have been removed — their outcomes live in §1–17 or in the code.

### Targets

- **(Q2)** Is the planning workbook keyed at `APAC` (current bucket) or at sub-regions (`APAC ANZ` / `APAC Asia` / `APAC Japan`)? The §5.1(a) rename would hide a future sub-region target split.
- **(Q11)** Per-region-family targets — proxy (v1 default), allocate the GEO target by historic booking share, or wait for Finance? Decision pending (§8.5).
- **(Q13)** Is the targets files' `ACV` gross-of-uplift (= `Cal_IACV`) or net (= `Total_NACV`)? If net, coverage mixes metrics and skews high. Confirm with the `datalake 25` owner (§8.9).
- **(Q16)** Will FY27 follow FY26's column convention or invent a new one? Determines whether a new per-year loader adapter is needed.
- **(Q20)** Are FY26's `Solex` / `Tiers` business-meaningful filters (e.g. strategic-deal coverage)? v1 sums everything; flagging in case a filter knob is wanted.

### Mapping

- **(Q3 / S30)** Pick one canonical booking-team column name and rename on ingress — three spellings exist: `Bookings_Team_static` (snapshot), `Bookings_Team_Static` (mapping), `Booking_Team_Static` (live SKU). Suggest lowercase `bookings_team_static`.
- **(Q4 / S1)** If we add deal-type-by-region targets, the §5.1(a) canonical-bucket rename must apply there too — a single shared rename utility (baked into the loader) is the simpler design.
- **(Q15)** Fold `PubSec → Public Sector` into the same name-normalization layer that handles the mapping's `Pubsec`. One pass over both inputs.
- **(Q21)** On first run, assert `[sharepoint].[Map_Booking_Team_Static_live]` still has the CSV's expected columns (`assert set(expected) <= set(df.columns)`) — a silent Synapse rename would break the loader.
- **(Q22)** Confirm how "live" `_live` is — genuine view, daily ETL copy, or other? Affects the freshness story we tell users.
- **(Q23)** Apply `Active Team = 'Active'` in SQL vs Python — Python keeps the `active_only` knob meaningful; fine at ~37 rows, reconsider if the table grows.

### Coverage & methodology

- **(Q7)** How far back does `[rep].[trf_opp_daily_snapshot_new]` extend? Bounds same-week-of-life overlay comparisons.
- **(Q8)** Tricentis FY definition — calendar-year or offset? Pin once to remove a class of off-by-one bugs.
- **(Q9)** Add a smoke check in tests so a sales-process stage rename (the late-stage list) surfaces loudly.
- **(Q10)** Ensure `Cal_IACV != 0` is applied in any standalone xlsx code path, or numbers silently diverge.
- **(Q41)** Re-check at 2026-EOY that the live pull's `6 - Closed/Pending` inclusion doesn't pollute bookings if a future process leaves opps stuck there long-term.
- **(Q42)** Extend `LIVE_BOOKED_START` earlier than `2024-01-01` if FY23 conversion curves are ever wanted (cheap — the table is small).
- **(Q43)** Surface in dashboard help that live-booked attributes by current `Opp_Closed_Date` (vs the snapshot's first-seen-Closed-Won week), so weekly slices reconcile against the cousin's Friday-snapshot report.
- **(Q44)** One-time audit of re-org'd teams: a frozen snapshot geo and the current live geo can disagree for the same opp, splitting open_pipe and booked across geos.
- **(Q45)** Keep the snapshot for open/LS pipe (PBI parity + true week-by-week history); revisit moving everything to `sku_nacv_fact` only if the cousin does.
- **(Q46)** Add a `booked_source` column to `coverage.parquet` on the next schema touch (self-documents that booked came from the live pull).
- **(Q47)** FY26 Q2 has pre-stamped future Closed-Won opps; show the actual curve with an "as of week N" annotation rather than clipping data.
- **(Q48)** Verify `coverage_render.py` has no its-own `.last()` / week-13 assumption that needs the same in-flight pinning fix applied in `build_coverage.py`.

### Recommendation engine

- **(Q30)** Ship with median conversion; expose mean / mean-of-winners as a toggle later if useful.
- **(Q31)** Include FY26 Q1 (closed) in the training set; documented so we don't forget the seasonality tradeoff.
- **(Q32)** Cells with < 3 quarters of usable conversion → fall back to the deal-type across-geo average and mark "low-confidence."
- **(Q33)** Anchor the headline recommendation at week 4 (the action window); show the full week-by-week curve as secondary.
- **(Q34)** Surface the recommendation both as a full section and as a color overlay on the coverage matrix.
- **(Q53)** Leave `SPARSE_WEEK_THRESHOLD = 8` as-is; revisit if a mid-quarter data-pipeline gap ever wrongly flags a quarter sparse.
- **(Q54)** Consider adding `weeks_with_data` to `coverage.parquet` when a second (non-dashboard) consumer appears.
- **(Q61)** Strict-conversion logic was dropped from the Overview; resurrect a "no-new-pipe floor" view only if leaders ask for it.
- **(Q68)** PS win rate is rising while NB/EX/UP fall — sample-size artifact (PS ~75–145 opps/FY) or a real services-mix shift? Out of current scope; worth a follow-up.

### Dashboard & design

- **(Q35)** Design lock (§9) holds — propose any deviation explicitly in PLAN before implementing.
- **(Q36)** Defer extracting CSS design tokens to a separate file until a second consumer (PDF / slides) exists.
- **(Q37)** Stay vanilla JS + hand-rolled SVG; revisit Recharts / React only if a hard-to-hand-roll chart or drag-to-zoom emerges.
- **(Q38)** Keep the KPI strip lean; the slice-grain "booked" question is covered by the Deep Dive cards.
- **(Q40)** Defer a high-contrast / projector theme until requested.
- **(Q57)** Don't make `lastDefined` skip zeros — it would hide legitimate "fully booked, open pipe = 0" states at quarter close. The upstream NaN fix is the correct guard.
- **(Q64)** Assign stable section IDs (`coverageGlide`, `winRateProof`, `recCovGlide`) only if cross-references between sections become common; §-numbers are presentation labels, not stable IDs.
- **(RQ1)** "Needed Coverage" KPI switches methodology by quarter state (per-quarter loose for closed, hidden for in-flight, historic median for future) — consider color-coding the active mode so a fast reader notices.
- **(RQ2)** Overview §02 ("share of bookings vs share of pipe") and Deep Dive §02 ("rec coverage glide") share a number but tell different stories; draw the connection (high-leverage cells need low coverage) explicitly.
- **(RQ3)** PS appears only on the Deep Dive weekly table and is excluded from all coverage-derived charts — add a one-line "PS excluded from coverage-derived views" note in the colophon / table header.
- **(RQ4)** Draw the "win rate fell → needed coverage rises" causal chain explicitly (a Needed-Coverage trend line in Overview, or a one-liner on the §02 glide table).
- **(RQ5)** Document the strict-vs-loose choice in the dashboard itself (tooltip on the Needed Coverage sub-label) — a viewer can't tell it's loose math today.
- **(RQ6)** `state.geo` (Deep Dive) and `state.overviewGeo` (Overview) are intentionally separate; add a "sync filters" affordance if cross-tab navigation becomes common.
- **(RQ7)** Align "current quarter" identification: `DATA.defaultQuarter` (latest, possibly future/empty) vs JS `latestQuarterWithData()`. Two drifting implementations caused a prior "no data showing" bug.

## 19. Open Simplification Opportunities

Deferred / uncommitted cleanups. Applied and rejected items have been removed.

- **(S1)** Bake the §5.1(a) region-family rename into `load_booking_team_mapping()` so downstream never sees raw `BTS_RegionFamily` values (see Q4).
- **(S2)** Skip the cousin's diff-vs-old-dict scaffolding — we start from the CSV, there's no legacy dict to diff against.
- **(S3)** Keep the small rename map (`APAC ANZ → APAC`, …) as a Python constant near the loader, not a config file.
- **(S4 / S5)** One `QUARTER_TARGETS_M`-style layer for v1 (top-level + per-region-family proxy fallback); don't port the cousin's four target dicts.
- **(S6)** Drop the xlsx-snapshot standalone mode (second code path, second filter location) or relegate it to a test fixture.
- **(S7)** Make the SQL stage mapping list open stages explicitly with an `Unknown` default, so a sales-process rename fails loudly instead of silently joining Open.
- **(S10)** Load quarterly target columns only; skip the unused monthly (`jan`…`dec`) columns.
- **(S13)** Cache the cleaned long-format targets frame to a gitignored parquet (mtime-invalidated) if xlsx read time ever becomes a complaint.
- **(S18 / S42)** Lift `deal_type_class()`, `LATE_STAGES`, `WON_STAGES`, `LOST_STAGES` from the cousin verbatim (or to a shared `stages.py`) so stage definitions stay identical across both projects.
- **(S19)** Skip the cousin's `Geo_View` override — `_bucket_region_family()` already handles the Public Sector mis-classification.
- **(S20)** Compute conversion / rec-coverage in `coverage_builder.py`, not the renderer, so the parquet is self-contained.
- **(S21)** Store recommendations as a small (geo × dt × week ≤ 208-row) lookup table joined for display, not per (quarter, geo, dt, week).
- **(S22)** No confidence intervals in v1 — show median + (min, max) range in a tooltip; defer CIs.
- **(S23)** Add a "how to add a chart that matches the design language" comment block atop the template `<script>` (dot/now-marker, weeks-pill, legend-row patterns).
- **(S24)** Inline `tricentis-logo.png` as base64 (or set a `<base>`) so the single-file dashboard has no path dependency.
- **(S25)** Pre-compute the per-slice headline sentence in `coverage_render.py` so the prose is unit-testable and the template stays thin.
- **(S27 / S31 / RS5)** Extract the shared snapshot prep (geo attach, quarter assign, `_week_of_quarter`, snapshot-date pin) into one helper used by `build_coverage`, `_live_booked_by_week`, and `_prep_snapshot_for_recs`.
- **(S29)** Consider removing the `6 - Closed/Pending → Closed Won` mapping from `snapshot.sql` (now dead for output) — but first validate whether PBI open-pipe matches `Cal_IACV` *because of* or *despite* it.
- **(S32)** Optionally LEFT JOIN the booking-team mapping inside `live_booked.sql` to push geo bucketing to the DB and surface team-name typos earlier; the Python join is already fast.
- **(S33)** Add `weeks_with_data` + `is_sparse_quarter` to `coverage.parquet`. (S34 — sparse-quarter exclusion from all three recs training sets — done 2026-06-05 via the shared `SPARSE_WEEK_THRESHOLD` constant.)
- **(S35)** Add a unit test for the KPI WoW delta at week 1 (no week 0) if/when a test suite lands.
- **(S36)** Optionally grey out no-data weeks on the Week pill row for sparse quarters (the banner already explains).
- **(S37)** When outer-merging two sources where one is "ahead" in time, fill NaNs carefully (track which side supplied each row) — the original blanket `fillna(0)` conflated three different "missing" cases.
- **(S38)** Rename the `strictPerQuarter` payload key → `perQuarterRecs` (it now holds loose-conversion values).
- **(S39)** Formally retire or preserve the legacy strict recommendation logic if a strict-vs-loose comparison is ever wanted again.
- **(S41)** Clear stale Overview / drill-down localStorage keys (`cc-ovGeo` / `cc-ovFy`, etc.) on first load — harmless but lingering in users' browsers now that the Overview page and its state are gone (2026-06-05 dead-code sweep).
- **(S44)** Add one canonical `is_quarter_closed(quarter, frame)` helper — "closed" is currently interpreted four slightly-different ways across the codebase.
- **(RS2)** Split the payload into `DATA.overview` / `DATA.deepDive` subsets if it ever grows beyond comfortable (~800–900 KB today is fine).
- **(RS3)** Consolidate the scattered deal-type code constants (`NB` / `EX` / `UP` / `PS`) into one "displayable deal types" list near the top of the JS.
- **(RS4)** Reconcile column naming between `recommendations.parquet` (`conversion_median`, `rec_coverage`) and `recommendations_per_quarter.parquet` (`conv`, `open_pipe_usd`).
- **(RS6)** Unify the win-rate and recommendation computations (both iterate snapshot + closed-quarter filter) into one "historic metrics" pass.
- **(RS7)** Give the inline chart-card explainer paragraphs a consistent collapsed-by-default treatment to reduce vertical scroll.

## 20. Reference Findings

Hard-won factual data from resolved investigations — kept because it isn't captured in §1–17 (F7 specifically asks to keep the coverage-by-quarter table).

### Snapshot-table cadence — FY24 ramp (from Q51)

The source table didn't reach weekly cadence until April 2024:

| Month | Distinct snapshot days | Notes |
|---|---:|---|
| 2024-01 | 1 (Jan 1) | `Cal_IACV` summed to $0 → filtered out by `Cal_IACV != 0` |
| 2024-02 | 1 (Feb 29) | Usable, falls into week 9 of Q1 |
| 2024-03 | 2 (Mar 1, Mar 31) | Mar 1 → week 9 (overrides Feb 29 via max-per-week pin); Mar 31 → week 13 |
| 2024-04+ | ~6/month | Weekly cadence stable from here on |

Net: FY24 Q1 has only **2 of 13 weeks** with usable open-pipe data (wk 9 $27.5M, wk 13 $716K). FY23 and earlier have no data and aren't pulled. This is why FY24 Q1 renders as a sparse-quarter flat representative (§9 sparse handling).

### Coverage by quarter — all 12 (from Q55; F7 asks to keep this)

Same formula (`open_pipe / (target − booked)`) applied across every quarter:

| Quarter | Wk1 open $M | Wk1 cov | Wk4 cov | EOQ attainment |
|---|---:|---:|---:|---:|
| FY24 Q1 | (sparse) | ~1.5× (after sparsification) | ~1.5× | 101% |
| FY24 Q2 | $69.2M | 3.34× | 3.26× | 102% |
| FY24 Q3 | $78.0M | 3.62× | 2.96× | 107% |
| FY24 Q4 | $117.2M | 3.13× | 2.82× | 99% |
| FY25 Q1 | $88.6M | 3.98× | 3.33× | 75% |
| FY25 Q2 | $96.5M | 3.46× | 3.18× | 76% |
| FY25 Q3 | $99.3M | 3.04× | 2.70× | 86% |
| FY25 Q4 | $189.4M | 3.83× | 3.42× | 81% |
| FY26 Q1 | $99.7M | 4.49× | 3.43× | 73% |
| FY26 Q2 | $99.9M | 3.41× | 2.86× | 24% (in-flight) |

Takeaway: FY24 carried slightly *lower* coverage but hit target — conversion was meaningfully better in FY24. The cross-year delta is a real business signal (conversion has degraded since FY24), not a calculation artifact.

### Win rate by fiscal year (from Q67), by deal count

| Deal Type | FY24 | FY25 | FY26 | Direction |
|---|---:|---:|---:|---|
| New Business | 12.9% | 10.9% | 7.0% | ↓ |
| Expansion | 36.0% | 28.9% | 30.7% | ↓ then partial recovery |
| Upsell | 56.0% | 52.1% | 52.0% | ↓ slight |
| Professional Services | 49.3% | 53.1% | 62.5% | ↑ |

Win rate fell for NB / EX / UP — the upstream cause of degraded conversion. PS rises but is small-volume and excluded from the Overview narrative (see Q68).

## 21. Feedback Worth Keeping

- **F1. Trust the source-of-truth, not the dashboard.** When the snapshot disagrees with PBI / live, the snapshot's metric *definition* is usually the suspect, not the data. Confirm discrepancies against the user's own Excel / live pull before changing code; live source-of-truth pulls are short and fast.
- **F2. Per-opp reconciliation breaks cases open.** An aggregate $471K gap could be anything; a per-opp join ("318 matched + 18 phantom + 29 missing") pinpointed the cause. Worth a reusable per-opp reconciliation helper in a diagnostics script.
- **F3. Canonical-name memories are load-bearing.** The `[[deal-type-canonical-naming]]` memory caught the live SQL renaming `New Business → New Customer` immediately — without it we'd have silently dropped FY25 / FY26 deal-type joins. Keep pinning canonical names with the *why*.
- **F4. Self-joins with slightly-different filters are a smell.** The deprecated audit override joined the snapshot back into itself on three group keys that all had to agree across two passes; a single-source pull eliminated that merge-key bug class.
- **F5. "Looks weird" → dump the per-week data first.** Nine times out of ten a chart anomaly is in the row count / data availability, not the math (FY24 Q1 was a source-table history gap, not a pipeline bug).
- **F6. Make every filter affect every visible number.** Wiring the 3 KPIs to honor filters was ~30 lines but high-leverage. Once data is correct, front-load interactivity completeness.
- **F7. "Same math everywhere?" deserves a documented answer.** Cross-period comparisons invite "is this fair?" — the coverage-by-quarter table (now §20) settles it durably. (This is why §20 exists.)
- **F8. "Current coverage" = `lastDefined`, not `currentWeek`.** The hero / KPI reads `lastDefined(cell.totalCov)`, which diverges from `cell.totalCov[cur-1]` whenever data exists beyond the current week. Silent "current vs latest-defined" mismatches are how dashboards lie — align or rename on the next hero-card pass.
- **RF1. The strict→loose recommendation pivot was the project's most consequential methodology call.** Strict (no-new-pipe) was a reasonable conservative try; the user's "9× is too high — we missed by 20%, not 300%" was the right critique. Surface the assumed conversion behavior explicitly before locking in `1/conv`.
- **RF2. The user iterates fast and prunes hard.** ~6 sections added and ~5 dropped during the restructure. Don't pre-build "complete" views — ship the next view and watch the user keep or kill it.
- **RF3. `/quiz` surfaces confusion early.** Invoked twice during the restructure; both times it exposed an ambiguity (`1/median` vs `1/median×LTB`; per-quarter vs historic median) before the code landed confused.
- **RF4. "Why didn't it update when I changed quarter?" recurs.** Multiple KPIs have failed to react to filter changes; a JS-level "filter change updates X-Y-Z" smoke test would catch it earlier.
- **RF5. The dashboard does what §2 set out to do** — the coverage curve, plus prescriptive per-cell coverage guidance, plus the win-rate causal explanation.
