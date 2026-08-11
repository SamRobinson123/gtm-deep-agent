# Slip — how much moves, where it lands, and how to trace it

**Source:** `[rep].[trf_opp_daily_snapshot_new]`, cached as
`data/snapshot_hist.parquet` (19.9M rows, Q3 FY25 – Q2 FY26).
**Status:** measurements below are **computed from the snapshot feed on
2026-08-11** and reproducible from `agent/waterfall.py`. Assumptions are labelled
separately and are **stated, not established**.

Read with [`pipe-create-waterfall.md`](pipe-create-waterfall.md) (where slip feeds
the derivation) and [`../tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)
(the table contract).

---

## Why this file exists

The model reduces slip to a single number and uses it only to shrink one term:

```
expected_from_existing_pipe = open_pipe x (1 - slip_rate) x pre_Q_win_rate
```

That treats slipped pipe as **destroyed**. It is not destroyed. It lands in a
specific later quarter and becomes that quarter's open pipe. The legacy workbook
models both directions — `In Q Inflow`, `In Q Outflow`, `Pre Q Inflow`,
`Pre Q Outflow` — and **we implement the outflow half and none of the inflow
half.** Everything below is the missing half, measured.

---

## What slip is, and what it is not

Take the pipe **open at a point in time**, dated to close in that quarter. Follow
those same opps to quarter end. Partition:

| Outcome | Meaning |
|---|---|
| `won` | reached a Closed Won stage |
| `lost` | reached a Closed stage that is not Won |
| `slipped` | still open, and its `CloseDate` **moved past the quarter end** |
| `held` | still open, `CloseDate` did **not** move out |

**Slip is the moved bucket only.** The won/lost exclusion is load-bearing: a won
deal whose close date shifted would otherwise count as slip and badly overstate
it. `held` is the residual — open but not yet pushed.

### The stage mapping is a lookup, not a substring test

From the model owner's `Slip Assumption.ipynb` (2026-08-11). This is
authoritative. It is applied **in SQL** — the same `CASE` in `SKU_SQL`,
`SNAP_SQL` and `SNAP_HIST_SQL`, so one mapping serves both sources — and mirrored
in `agent/waterfall.py` (`WON_STAGES` / `LOST_STAGES` / `OTHER_STAGES` /
`OPEN_STAGES`) for parquet cached before the column existed.

| Outcome | `Raw_Stage` values |
|---|---|
| **Closed Won** | `Closed Won`, `Stage 5 - Closed Won`, `6 - Closed/Pending` |
| **Closed** (lost) | `Closed Lost`, **`Closed Deferred`** |
| **Other** — excluded entirely | `Closed - Duplicate`, `Stage 6 - Closed - Admin`, `Stage 7 - Churned`, `Opportunity Rejected`, `0 - First Interaction` |
| **Open** | everything else |

Two traps this closes:

- **`Closed Deferred` is a LOSS, not slip.** It is a decided outcome. Counting it
  as still-open pipe that moved would overstate slip substantially — it is the
  third most common stage in the feed (3.8M rows).
- **`Stage 4 - Closed Pending` is OPEN**, despite containing "Closed". A
  `contains("Closed")` rule books it as lost. That rule was in this codebase until
  2026-08-11; the mapping is now explicit, and an unrecognised stage is recorded
  in `attrs["unmapped_stages"]` rather than silently becoming open pipe.

`OTHER_STAGES` is also filtered at pull time by `config.EXCLUDED_STAGES`, so
those rows never reach the classifier. The list is duplicated in the model
deliberately, so the rule survives a change to the pull.

Slip is **not** the sales cycle. They act on different populations and answer
different questions; see the comparison table in
[`pipe-create-waterfall.md`](pipe-create-waterfall.md) Step 2. In short: sales
cycle describes *newly created* pipe, slip describes the *already open* base.

---

## Measurement recipe

`agent/waterfall.py`:

| Function | Returns |
|---|---|
| `classify_outcomes(snap, q_start, q_end, anchor)` | one row per opp with its outcome — the shared classification |
| `slip(quarter_start, grain, from_point, snapshot_file)` | won/lost/slipped/held dollars and `slip_rate` per grain key |
| `slip_destinations(quarter_start, from_point, snapshot_file)` | **where the slipped pipe landed**, as shares by quarter offset |
| `slip_forecast(quarter_start, open_pipe, grain)` | the forecast: prior-year rate x prior-year destination curve, in dollars |
| `prior_year_quarter(q)` / `slip_anchor(q, as_of, prior)` | which historic quarter to measure, and from where |

Both `slip()` and `slip_destinations()` go through `classify_outcomes()`. Keep it
that way — a second copy of the classification will drift.

### Anchoring matters more than the window

Measured on Q3 FY25:

| Anchor | Days left | Starting open pipe | Slipped | Rate |
|---|---:|---:|---:|---:|
| Quarter start | 91 | $103,032,856 | $60,212,724 | **58.4%** |
| W7 equivalent (2025-08-11) | 50 | $66,572,354 | $42,553,971 | **63.9%** |

Fewer dollars move from W7, but the **rate is higher**. The pipe still open at W7
is enriched in deals that do not close — the fast closers already resolved. So a
W7 balance needs a W7-measured rate; applying a quarter-start rate to a
mid-quarter balance mismatches populations.

---

## Where the slipped pipe lands

**This is the part the model does not have.** Shares of slipped dollars by
destination quarter offset (1 = the next quarter):

| Slipped out of | Slipped $ | Opps | Q+1 | Q+2 | Q+3 | Q+4 |
|---|---:|---:|---:|---:|---:|---:|
| Q3 FY25 | $60,212,724 | 687 | **80%** | 11% | 6% | 2% |
| Q4 FY25 | $108,300,047 | 1,094 | **41%** | **43%** | 10% | 6% |
| Q1 FY26 | $65,838,054 | 742 | 56% | 34% | 9% | 1% |
| Q2 FY26 | $65,408,000 | 718 | 54% | 40% | 3% | 2% |

**Q4 is the outlier and it is a real seasonal effect.** Pipe slipping out of Q4
skips Q1 and lands in Q2 — a calendar year-boundary push. Everywhere else the
next quarter dominates.

> ### Do not pool these into an average
>
> **Directive from the Strategic Analytics lead, 2026-08-11.** A blended curve is
> the average of an 80/11 shape and a 41/43 shape, and describes neither. Applied
> to Q4 it would move roughly a third of Q4's slipped dollars into the wrong
> quarter. **Each quarter is forecast from the same quarter a year earlier — its
> rate and its destination curve, together.** That pairing is the model.

The destination is a **stronger seasonality signal than the slip RATE.** The rate
varied only 52.6%–58.4% across these four quarters, while the destination split
ranges from 41% to 80% into Q+1. If slip is seasonal anywhere, it is here.

### The forecast mechanic

`slip_forecast(quarter_start, open_pipe, grain)` composes it. Both assumptions
come from `prior_year_quarter(quarter_start)` — never from a pool:

```
slipped        = open_pipe x slip_rate              # prior-year same quarter
to quarter n+k = slipped x destination_share[k]     # same prior-year quarter
```

Run on 2026-08-11:

| Target | Source | Open pipe | Slip rate | Slipping | → Q+1 | → Q+2 | → Q+3 |
|---|---|---:|---:|---:|---:|---:|---:|
| Q3 FY26 | Q3 FY25 | $75,741,019 | 71.1% | $53,824,503 | $42,936,068 (80%) | $6,070,714 (11%) | $3,398,839 (6%) |
| Q4 FY26 | Q4 FY25 | $211,460,105 | 62.4% | $131,956,805 | $54,081,254 (41%) | $56,235,775 (43%) | $13,563,473 (10%) |

Read the Q3 row: of Q3 FY26's open pipe, $53.8M is forecast to slip, and **$42.9M
of it lands in Q4 FY26**. Nothing in `derive_targets()` receives that $42.9M
today — it is subtracted from Q3 and arrives nowhere. That is the missing inflow,
quantified.

---

## Slip is serial

Following Q3 FY25's slippers into Q4 FY25, the quarter they landed in
($48,031,983 of the $60.2M went to Q+1):

| Outcome in the destination quarter | Opps | Dollars | Share |
|---|---:|---:|---:|
| **Slipped AGAIN** | 285 | $26,599,476 | **55%** |
| Lost | 151 | $12,664,648 | 26% |
| Won | 66 | $6,275,439 | 13% |
| Held | 28 | $2,492,420 | 5% |

**Once-slipped pipe wins at 13.1% in its new quarter.** The model applies the
`later` win rate — mean 0.158 across territories — to *all* pre-existing pipe.
Slipped pipe is not the same asset as freshly-matured pipe, and treating them
alike overstates what existing pipe will deliver.

More than half slips a second time, so a single-hop destination model
understates how far pipe eventually travels.

---

## Dollars do not carry forward intact

Q3 FY25's slipped opps, valued at both ends of the quarter:

| | |
|---|---:|
| Value at quarter start | $60,212,724 |
| Value at quarter end | $62,021,929 |
| **Drift** | **+$1,809,205 (+3.0%)** |
| Opps whose value changed | 360 of 687 |

Slipped opps get re-scoped as they move.

> **Decision, Strategic Analytics lead 2026-08-11: do not apply the +3%.** The
> forecast carries the **value at the anchor** — the pipe as it stands when the
> assumption is made. The drift is a description of what happened to those opps,
> not an uplift to apply forward. Re-scoping is a real effect but it is not a
> reliable one, and building a +3% into a forecast would inflate every future
> quarter by construction.

Recorded here because the measurement is worth knowing even though it is not
used: it means a destination figure taken at quarter end is 3% larger than the
same pipe measured at the anchor, and the two must not be mixed in one table.

---

## Tracing one opportunity

`Opp_Id` is stable across snapshots, so an opp's whole path is recoverable:

```python
h = snap[snap.Opp_Id == opp_id].sort_values("snapshot_date")
h[h.CloseDate.ne(h.CloseDate.shift())][["snapshot_date", "CloseDate", "Raw_Stage", "value"]]
```

A real example — `0068c00000zFvCeAAK`, AMS Core East Canada:

| snapshot_date | CloseDate | Raw_Stage | value |
|---|---|---|---:|
| 2025-07-01 | 2025-09-26 | 3 - Executive Presentation | $225,000 |
| 2025-08-05 | 2025-11-14 | 5 - Negotiation | $180,000 |
| 2025-09-23 | 2025-12-19 | 5 - Negotiation | $264,000 |
| 2025-09-27 | 2025-12-10 | 5 - Negotiation | $264,000 |
| 2025-10-30 | 2025-11-28 | 5 - Negotiation | $264,000 |
| 2025-11-10 | 2025-12-10 | 5 - Negotiation | $264,000 |
| 2025-11-17 | 2025-12-31 | 5 - Negotiation | $264,000 |

Two things this shows that the aggregates hide. The close date **oscillates**
rather than stepping cleanly forward — Dec 19, then Dec 10, then Nov 28, then Dec
31 — so "which quarter did it land in" depends on when you look. And the value
moved twice before settling. A destination measured at quarter end is a snapshot
of a moving target, not a final answer.

---

## What the model does with slip today, and what it ignores

**Uses:**
- `slip_rate` per grain key, as the `(1 - slip_rate)` haircut on open pipe
- measured per quarter on the same quarter a year earlier
  (`prior_year_quarter()`), anchored at the equivalent point in flight

**Ignores:**
- **Destination.** Nothing receives the slipped pipe. The workbook's
  `In Q Inflow` / `Pre Q Inflow` have no counterpart in `derive_targets()`.
- **Serial slip.** Modelled as a one-time loss.
- **The distinct win rate of slipped pipe** (13.1% vs the 0.158 `later` mean).
- **Value drift.**

Also unresolved in the workbook itself: `In Q Outflow` is omitted from its own
`Pre Q Bookings` formula — `=$L+($N+$O+$Q+$R)*$AN` includes `$O` and `$R` but not
`$P`. See known issue 2 in [`pipe-create-waterfall.md`](pipe-create-waterfall.md).

---

## Coverage requirements

Slip needs a **completed** quarter and **contiguous snapshot coverage** across it.

`config.HIST_SNAP_WINDOWS` is a list of **disjoint** ranges, so a quarter can fall
in a gap between them while the feed's min and max still straddle it. `slip()`
guards this by requiring a real snapshot within 7 days of both the anchor and the
quarter end. Without that guard the starting population is empty, `slip_rate` is
0/0, and the caller sees a confident **0.0%** — which is how slip silently
contributed nothing for several sessions.

If you add a quarter to the analysis, add its range to `HIST_SNAP_WINDOWS` and
re-pull `snapshot_hist`.

---

## The owner's notebook — `Slip Assumption.ipynb`

Reviewed 2026-08-11. It is the origin of the stage mapping above, and it differs
from this implementation in four ways that are worth settling.

### 1. It measures END-OF-QUARTER slip, not whole-quarter slip

The notebook takes the pipe due to close in the **last ~2 weeks** of a quarter,
as seen from a snapshot shortly before, and checks how much pushed past quarter
end. That is a different and complementary question to the one `slip()` answers
(all pipe open at an anchor, dated to close in the quarter).

Both are legitimate. End-of-quarter slip is the sharper operating signal — it is
the pipe a quarter is actually relying on. Whole-quarter slip is what the
derivation needs, because the derivation is haircutting the whole open base.
**They are not interchangeable and should never be quoted as the same number.**

### 2. The 80.0% figure in the notebook does not measure slip

Traced 2026-08-11. The population cell selects, from the **2025**-03-16 snapshot,
opps with `CloseDate` between **2026**-03-19 and **2026**-03-31 — a year out. The
slip test then asks whether `CloseDate > 2025-03-31`, which is true for that
entire population by construction, roughly twelve months over.

So 22 of 22 opps "slipped", and the 20% shortfall is the single opp missing from
the 4/1 snapshot (23 → 22), not pipe that held. **The 80.0% is an artifact of the
year mismatch.** The surrounding SQL also filters `snapshot_date` to 2026 while
the loaded frame contains 2025 dates, so `df_main` predates that cell.

Reading the intent, `2026` should be `2025` in both bounds. Re-run that way it
would be a genuine end-of-quarter slip measurement — worth doing, and worth
comparing against the 58.4% whole-quarter figure.

*(The coincidence that our Q3 FY25 destination curve also sends 80% to Q+1 is
unrelated. Different populations, different questions.)*

### 3. Value column — `Total_NACV` vs `Cal_IACV`. **Unresolved.**

The notebook values pipe with `Total_NACV`. This implementation uses `Cal_IACV`,
and `Total_NACV` **is not pulled** — it is not in `SNAP_SQL`, so it is absent from
`snapshot.parquet` and `snapshot_hist.parquet`.

Every dollar figure in this file is therefore on `Cal_IACV`. Whether that agrees
with `Total_NACV` is **untested**, and root `docs/README.md` hard rule 3 says
never use `Amount` and prefer `Total_ARR__c` / `NACV__c`, which points toward the
notebook's choice. Settling this needs a decision and a re-pull; until then the
figures here are internally consistent but not reconciled to the notebook.

### 4. Snapshot cadence and close-date bound

| | Notebook | Here |
|---|---|---|
| Cadence | `IsQuarterWeekStartDate = 1` — weekly | every daily snapshot |
| Close-date bound | `CloseDate BETWEEN QuarterStartDate AND Next2QtrEndDate` | unbounded |
| Meetings | `Stage_Pipe_Category <> 'Meeting'` and `NOT NULL` | not filtered (532 rows of 19.9M) |

The close-date bound matters most: capping at `Next2QtrEndDate` would truncate the
destination curve at roughly Q+2 and hide the Q+3/Q+4 tail this file reports. The
weekly cadence would cut the pull from 19.9M rows to roughly a seventh at no loss
for slip, which only ever reads two dates per quarter.

---

## Stated assumptions vs measured facts

**Measured** (reproducible from the functions above): every table in this file.

**Stated assumptions** — per the Strategic Analytics lead, 2026-08-11, these are
choices, not established facts:

1. **Slip is seasonal**, so a quarter's assumption comes from the same quarter a
   year earlier rather than the most recent completed quarter.
2. **Q1–Q2 FY26 are the recency alternative**, carried so the two readings can be
   compared. The docs flag both windows as unestablished.
4. Mid-quarter, the **equivalent point-in-time** is the like-for-like anchor.
5. Pipe is carried at its **anchor valuation**; the +3% re-scoping drift is
   measured but not applied.

---

## Open questions

1. ~~Should destination be fitted per quarter-of-year?~~ **Decided 2026-08-11:
   yes.** Each quarter is forecast from the same quarter a year earlier, rate and
   destination together. Pooling is explicitly rejected.
2. ~~Which valuation carries?~~ **Decided 2026-08-11: the value at the anchor.**
   The +3% drift is not applied forward.
3. **Should serial slip compound**, or be modelled directly as a single
   multi-quarter distribution measured over a longer horizon? The 55% re-slip
   rate means a one-hop curve understates travel.
4. **Should slipped pipe carry its own win rate** (13.1%) rather than the general
   `later` rate (0.158)?
5. **Territory grain.** Fitted globally today. Q3 FY25 had 687 slipped opps
   across 31 booking teams — median 19, with 12 teams under 10 — so a per-
   territory curve needs a fallback and more history than one quarter.
6. **Does destination belong in the solve at all**, i.e. should slipped pipe feed
   the destination quarter's existing-pipe term? That is the workbook's
   inflow/outflow model, and it is deliberately **not** implemented yet — it
   would move every quarter after the first while the `$0` sales cycle tail is
   still unresolved.
