# Code Review — Coverage Curve Analysis

Reviewed 2026-05-28 against `planning/PLAN.md` and `planning/NEEDED_PIPELINE_METHODOLOGY.md`.
Files reviewed: `backend/snapshot.py`, `backend/synapse.py`, `backend/coverage_builder.py`,
`backend/build_coverage.py`, `backend/coverage_render.py`, `backend/sql/snapshot.sql`,
`backend/sql/live_booked.sql`, `frontend/dashboard_template.html`, plus `data/inputs/loaders.py`
(load-bearing for targets and the mapping, even though it was outside the listed set).

The pipeline is coherent and matches PLAN's intent on the big calls: snapshot drives
`open_pipe`/`ls_pipe` from `Cal_IACV` with `drop_duplicates()`, `booked` comes from the live
SKU pull, coverage is `pipe / (target − booked)` with `NaN` on `LTB ≤ 0`, and the
weekly downsample pins to the max snapshot date per (quarter, week). The findings below are
mostly correctness edge cases, two data-hazard gaps, and a large block of dead frontend code.

---

## Prioritized summary (most important first)

1. **Critical — `booked` is knowingly ~10× inflated and the dashboard does not say so.**
   `sku_nacv_fact` inflation (PLAN §6.3, `live_booked.sql` header) propagates to `booked`,
   `ltb`, `total_cov`, `ls_cov`, attainment, and every recommendation. Nothing in the
   pipeline or the rendered HTML flags this; a reader sees plausible-looking coverage numbers
   that are wrong. At minimum the dashboard needs a visible banner until the source is fixed.
   (`coverage_builder.py:250-278`, `coverage_render.py` payload, template.)

2. **High — Snapshot-`booked` leaks through when a closed cell has zero live bookings.**
   The live override only replaces `booked` where `booked_live` is non-null. A
   (quarter, geo, deal_type, week) present in the snapshot but absent from the live frame
   keeps the snapshot's `Cal_IACV`-derived `booked` — the exact phantom/`6 - Closed/Pending`
   value PLAN §3.5 set out to eliminate. (`coverage_builder.py:268-270`.)

3. **High — ~10 dead JS functions (~30%+ of the script) still ship in the template.**
   `renderRecCalc`, `renderPipeMetrics`, `computeReccalcRows`, `downloadReccalcCsv`,
   `renderWeeklyWinRateChart`, `renderWeeklyShareChart`, `renderNeededCoverageHistory`,
   `renderDtStrip`, `renderOverviewInsight`, and `attachWeeklyHover` are defined but
   unreachable from `renderAll()`. (S40 — still outstanding.)

4. **Medium — `_legacy_compute_recommendations_loose()` is fully dead code** (~155 lines,
   `coverage_builder.py:618-773`) and duplicates the live `compute_recommendations()` logic.

5. **Medium — `mapping["ActiveTeam"]` and several Synapse column names are assumed without
   any guard.** A silent upstream rename (PLAN Q21) breaks the loader with a raw `KeyError`
   and no actionable message.

6. **Medium — No error handling around the Synapse pull / xlsx load / parquet write**, and
   `pandas.read_sql(... pyodbc)` raises a `UserWarning` on every run (PLAN says SQLAlchemy is
   not used — acceptable, but noisy).

7. **Low — Several documented PLAN simplifications/contracts are unimplemented**: `Total_NACV`
   still pulled but unused (S28), three booking-team column spellings never unified (Q3/S30),
   `booked_source` column never added (Q46), FY24 Q1 still in the recommendation training set
   (S34).

---

## backend/snapshot.py

- **Low — `pull_snapshot` / `pull_live_booked` use `str.format` on SQL containing `{...}`-free
  but `%`-bearing… actually no `%`; fine.** The bigger latent issue: both functions inject
  `fy_start`/`fy_end`/`start_date`/`end_date` via `str.format`. These are internal constants
  today (`build_coverage.py`), so not an injection risk, but the values flow straight into SQL
  text. Keep them constants; if they ever become user input, parameterize.
- **Low — `pull_snapshot` dedup is correct and matches PLAN §3.1/§6.3** (`drop_duplicates(ignore_index=True)`
  before any aggregation). Good. No action.
- **Low — `pull_live_booked` does not dedup in Python** — dedup is pushed into the SQL
  (`GROUP BY` in `live_booked.sql`). That's fine and documented, but note the SQL `MAX(NACV_USD - Uplift_USD)`
  cannot recover the inflated value (see live_booked.sql finding). No code fix possible here.

## backend/synapse.py

- **Low — `connect()` is fine.** Returns a raw pyodbc connection; `build_coverage.main()` uses
  it as a context manager, which pyodbc supports. One gap: no `Encrypt`/timeout enforcement and
  no wrapping of `pyodbc.Error` into a friendlier message, but acceptable for an internal weekly tool.

## backend/coverage_builder.py

- **High — live-booked override leaves stale snapshot `booked` on live-missing cells.**
  `coverage_builder.py:268-270`. After `out["booked"] = out["booked"].fillna(0.0)`, only rows
  with `booked_live.notna()` are overwritten. Snapshot rows for a (quarter, geo, dt, week) that
  produced a `Cal_IACV` Closed-Won figure but have *no* corresponding live row retain that
  snapshot booked value — contradicting PLAN §3.5/§6.3 (“compute `booked` from live only”).
  Suggested fix: after computing `live_by_week`, for any (quarter, geo, dt) that appears in the
  snapshot-derived frame, treat missing live weeks as 0 booked rather than falling back to the
  snapshot value — e.g. zero out `booked` for all snapshot rows first, then layer live on top,
  or left-merge live onto the full (quarter, geo, dt) × 13-week grid so every snapshot cell gets
  an explicit live value (0 if none).

- **Medium — Snapshot-derived `booked` is computed but only conditionally replaced.**
  `coverage_builder.py:232-236` computes a snapshot `booked` (Closed Won `Cal_IACV`) that is
  *intended to be fully overridden* by live. It is only used as the fallback in the bug above,
  and is meaningless (PLAN says snapshot booked over-counts by ~3% plus phantoms). Once the
  override is made unconditional, this column is dead — consider dropping the snapshot `booked`
  aggregation entirely and sourcing `booked` solely from `_live_booked_by_week`.

- **Medium — `_legacy_compute_recommendations_loose()` is dead.** `coverage_builder.py:618-773`.
  Never imported or called anywhere (`build_coverage.py` calls `compute_recommendations`). Its
  docstring even claims it was “Replaced by the strict opp-level `compute_recommendations`,”
  but the live `compute_recommendations` is itself the loose version — the comment is stale.
  Delete, or move to a test fixture (S39).

- **Medium — `build_coverage` and `_prep_snapshot_for_recs` duplicate the entire snapshot-prep
  block** (geo attach, quarter assign, `q_start` math, week_of_quarter clip, max-date pin):
  `coverage_builder.py:180-208` vs `306-328`. PLAN S27/S31/RS5 calls for one shared helper.
  Two copies will drift; extract `_prep_snapshot(snapshot, mapping)`.

- **Low — `compute_recommendations` `weeks_in_quarter` default and the “closed” definition rely on
  `open_pipe.notna()`** (`:372-376`). This is correct given the live-merge introduces NaN
  open_pipe rows, and the comment explains it well. No change, but it is fragile if finding #2 is
  fixed by zero-filling open_pipe; re-verify the closed-quarter detection after that fix.

- **Low — `WON_STAGES` includes `6 - Closed/Pending`** (`:19-23`). This is documented as
  intentional for win rate, but it is the same phantom-pending stage that corrupted snapshot
  booked. Win-rate ACV (`win_rate_acv`) therefore counts phantom-pending `Cal_IACV` as won —
  acceptable per the comment, but worth a smoke check that it doesn't materially skew the
  FY-over-FY win-rate narrative.

## backend/build_coverage.py

- **Medium — No error handling on the network/IO path.** `connect()`, three `pull_snapshot`
  calls, `pull_live_booked`, `load_quarter_targets` (three xlsx reads), and five `to_parquet`
  writes all run unguarded in `main()`. A missing xlsx or a Synapse hiccup dumps a raw
  traceback. For a weekly analyst-run tool, a single try/except around the pull with a clear
  message (“check VPN / .env”) would help.

- **Low — `recs_pq_path` is printed as “per-quarter strict”** (`:67`) but the values are *loose*
  conversion (the strict path was retired). Mislabeled log line; rename to match
  `recommendations_per_quarter` / loose (PLAN S38).

- **Low — `LIVE_BOOKED_START = "2024-01-01"`** hard-codes the FY24 start; consistent with
  `FISCAL_YEARS[0]`. Fine, but the two are independent constants that must be kept in sync —
  derive `LIVE_BOOKED_START`/`END` from `FISCAL_YEARS[0][1]` / `FISCAL_YEARS[-1][2]`.

- **Low — The end-of-quarter summary block** (`:96-115`) re-derives “latest week per quarter”
  with its own logic, a third variant of the “is this quarter closed / what's its last week”
  question (PLAN S44 — four interpretations across the codebase). Consider a shared helper.

## backend/coverage_render.py

- **Medium — Payload carries no `bookedSource` / data-quality flag.** Given the live-booked
  inflation (Critical #1), the renderer is the natural place to inject a top-level
  `dataQuality` / `bookedReliable: false` flag the template can surface as a banner. PLAN Q46
  asks for a `booked_source` column; extend that to a render-time warning.

- **Low — `series = (cell.openPipe ? cell : qdata.aggregate)`** equivalent logic in the template
  (`:2106`) never falls back because `cell.openPipe` is always a populated array. Harmless but
  misleading; see template findings.

- **Low — `_safe` / `min_count=1` handling is correct and well-commented** (`:114-122`). The
  distinction between “all-NaN future weeks” (kept NaN) and zero booked is handled properly for
  `open_pipe`/`ls_pipe`. Note `booked` uses plain `"sum"` (→0 for empty), which is the intended
  behavior (booked of an empty future cell is 0, not unknown). No change.

- **Low — `render()` silently swallows missing parquet files** by substituting empty DataFrames
  (`:398-409`). That means a forgotten `compute_*` step renders a dashboard with blank sections
  rather than failing. Acceptable for resilience, but a one-line warning when a parquet is
  missing would prevent “why is the win-rate tab empty” confusion.

## backend/sql/snapshot.sql

- **Medium — `6 - Closed/Pending → Closed Won` mapping is still present** (`:36`). PLAN S29/Q29
  flags this as now-dead for output (booked comes from live) but wants validation of whether PBI
  open-pipe matches `Cal_IACV` *because of or despite* it before removal. Until validated, leave
  it, but it is a live correctness lever: any opp stuck in `6 - Closed/Pending` is classified
  `Closed Won` here and therefore excluded from `open_pipe`. If that's wrong, open pipe is
  understated. Worth the documented validation.

- **Low — `Total_NACV` is still selected** (`:27`) despite being unused everywhere (PLAN
  §3.5 says “selected but ignored”, S28 says drop it). Minor, but removing it shrinks the pull.

- **Low — The stage `CASE` uses an `ELSE 'Open'` default** rather than enumerating open stages
  with an `Unknown` fallback (PLAN S7). A future sales-process stage rename would silently fall
  into `Open` and inflate pipe rather than failing loudly. Consider listing the known open
  stages explicitly.

- **Low — `Geo` from `opp_product` (`MIN(N.Opp_Geo)`) is selected but unused** by the coverage
  builder (geo comes from the mapping join, PLAN §5). Dead column in the pull.

## backend/sql/live_booked.sql

- **Critical (data, documented) — `MAX(NACV_USD - Uplift_USD)` cannot recover the inflated
  value.** The header comment is correct: all ~32 duplicate copies carry the same already-inflated
  value, so the dedup `GROUP BY` collapses rows but not the inflation. This is a source-data
  problem, not fixable in SQL — but the *consequence* (every downstream booked/coverage number
  is ~10× high) must be surfaced in the dashboard, which it currently is not (see Critical #1).

- **Low — `Record_Type IN ('Product','Service','Platinum support')`** here vs snapshot.sql's
  `('Product','Service','Platnium Support')` (note the misspelling `Platnium` and capitalization
  `Platinum support` vs `Platnium Support`). These two filters are meant to mean the same thing
  but use different literal strings; if the source uses one canonical spelling, one of these
  filters is silently matching nothing. Verify the actual `Record_Type` domain and unify.

- **Low — `Deal_Type IN ('New Business','Expansion','Upsell','Professional services')`** uses
  lowercase `services`. PLAN canonical is `Professional Services` (title case). If the live
  source stores title-case, this filter drops all PS bookings; if lowercase, it's fine but
  inconsistent with `coverage_render.py`'s canonical `Professional Services`. Verify and align.

## frontend/dashboard_template.html

- **High — Dead JS (S40, still outstanding).** Confirmed unreachable from `renderAll()`:
  `renderRecCalc` (2590), `renderPipeMetrics` (2808), `computeReccalcRows` (2568),
  `downloadReccalcCsv` (2979), `renderWeeklyWinRateChart` (1453), `renderWeeklyShareChart` (1778),
  `renderNeededCoverageHistory` (1537), `renderDtStrip` (2329), `renderOverviewInsight` (2353),
  and `attachWeeklyHover` (1870, only called by the three dead chart functions). `renderRecCalc`
  and `renderPipeMetrics` only “call themselves” via event handlers / internal calls but are
  never invoked by `renderOverview()` or the deep-dive path. Deleting these removes a large
  block of the script and the associated stale localStorage keys (`cc-rcGeo/rcDt/rcWeek` — S41).

- **Medium — No data-quality banner for the inflated `booked`.** The template renders coverage,
  attainment, and LTB from `booked` with full confidence (KPI strip `:2197-2244`, detail table,
  charts). Add a dismissable warning banner driven by a payload flag.

- **Low — `series = (cell.openPipe ? cell : qdata.aggregate)`** (`:2106`) is effectively dead —
  every cell has a populated `openPipe` array, so the `qdata.aggregate` fallback never fires.
  Either remove it or, if the intent was “fall back when the cell is empty,” the condition is
  wrong (it should test for an all-null array). This is the F8 “current vs latest-defined”
  family of subtle mismatches.

- **Low — Needed-Coverage methodology switches by quarter state without a visible mode marker**
  (`:2115-2143`, PLAN RQ1/RQ5). Closed quarters use per-quarter loose conversion; in-flight
  hides; the `needSource` sub-label is the only signal. PLAN itself flags this; a color or
  badge on the active mode would reduce misreads.

- **Low — `_colorCell(v) { return ''; }`** (`:1397`) is a no-op kept in `renderNeededCoverageTables`.
  Dead stub; remove or implement.

## data/inputs/loaders.py (reviewed though outside the listed set — load-bearing)

- **Medium — No column-presence guard on the Synapse mapping (PLAN Q21).** `load_booking_team_mapping`
  does `df[df["ActiveTeam"] == "Active"]` (`:31`) with no check that `ActiveTeam` (or the columns
  `_attach_geo` later needs — `Bookings_Team_Static`, `BTS_RegionFamily`) exist. A silent upstream
  rename yields a bare `KeyError`. Add `assert {"ActiveTeam","Bookings_Team_Static","BTS_RegionFamily"} <= set(df.columns)`
  with an actionable message.

- **Medium — The §5.1(a) region-family canonical rename is NOT applied in the loader (PLAN S1/Q4).**
  `load_booking_team_mapping` returns raw `BTS_RegionFamily`. The collapse to canonical buckets
  lives entirely in `coverage_builder._bucket_region_family`. PLAN wants the rename baked into the
  loader so downstream never sees raw values. Today it works only because `_bucket_region_family`
  re-implements it via `startswith` prefixes — but anything else reading the mapping (future
  consumers) gets un-normalized values. Low risk now, but a documented design intent that's unmet.

- **Low — FY24 grand-total filtering is double-guarded and slightly fragile.** `_load_fy24`
  ffills `geo` then drops `deal_type.isna()` rows (`:75-77`), then also drops `geo` containing
  “Total”. The second filter is redundant given subtotal rows have NaN deal_type, but it would
  also drop any legitimate geo containing the substring “Total” — none exist today, so fine.

- **Low — `usecols="A:N"` / `"A:O"`** for FY25/FY26 (`:84`, `:108`) is positional and brittle: a
  column insertion in the workbook shifts everything. PLAN §8.2 lists the canonical fields by
  name; reading by header name would be more robust (S10 also notes skipping the monthly columns).

- **Low — No `target_usd` aggregation sanity check.** PLAN §8.1 gives expected totals
  (FY24 $100.4M, FY25 $134.2M). A cheap `assert`/warn on the grand totals after load would catch
  a malformed sheet or a wrong `usecols` range immediately.

---

## Things verified correct (no action)

- Dedup of the doubled snapshot rows before aggregation (`snapshot.py:26`) — matches PLAN §3.1.
- Coverage formula and `NaN` on `LTB ≤ 0` (`coverage_builder.py:285-289`) — matches PLAN §7.
- Weekly downsample pins to max snapshot_date per (quarter, week) (`:204-208`) — matches PLAN §6.2
  and the documented $5M over-count fix.
- Quarter assignment by `CloseDate`, both CloseDate and snapshot in-quarter (`:184-186`) — PLAN §6.1.
- Deal-type names passed through unchanged from `Opp_Type` (snapshot.sql `:33`) — PLAN §3.4; FY24
  `New Customer` → `New Business` rename applied in the loader (`loaders.py:44-48`) — PLAN §8.4.
- Median (not mean) across closed quarters, drop `conv < 0` / `open_pipe ≤ 0`
  (`:441`, `:454-458`) — matches NEEDED_PIPELINE_METHODOLOGY §3-4.
- Per-grain ($-weighted) recommendation aggregation for All-geo / All-dt (`:418-433`) — matches
  methodology §7 (computed from scratch, not averaged).
- `min_count=1` to preserve all-NaN future weeks as “no data” (`coverage_render.py:120-122`) —
  the documented fix for the spurious 0× hero-card bug.
