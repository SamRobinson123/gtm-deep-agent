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

That treated slipped pipe as **destroyed**. It is not destroyed — it lands in a
specific later quarter and becomes that quarter's open pipe. The legacy workbook
models both directions (`In Q Inflow`, `In Q Outflow`, `Pre Q Inflow`,
`Pre Q Outflow`) and for a long time we implemented only the outflow half.

**As of 2026-08-11 both halves are implemented**, along with the Pre-Q/In-Q
timing split:

```
adjusted = open_pipe x (1 - pre_q_slip) + slip_inflow
expected = adjusted x (1 - in_q_slip) x later_win_rate
```

This file is the measurement behind every term in that line, and the record of
which parts are stated assumptions rather than established facts.

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

Shares of slipped dollars by destination quarter offset (1 = the next quarter).
`slip_inflow()` consumes exactly this curve:

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
of it lands in Q4 FY26**. `slip_inflow()` now delivers that to Q4's existing-pipe
base — it used to be subtracted from Q3 and arrive nowhere.

`slip_forecast()` is the standalone view and anchors at the quarter start;
`slip_inflow()` is what the solve calls and anchors at the equivalent point in
flight, so the two differ mid-quarter by design (the run on 2026-08-10 forwards
$49.7M rather than $42.9M). Quote `slip_inflow()` for anything that feeds a
target.

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

## Pre-Q vs In-Q — the cohort split, and why it comes out backwards

> **These are NOT the model's Pre-Q and In-Q rates.** Those are a *timing* split
> and both act on existing open pipe — see "Pre-Q and In-Q are a TIMING split"
> below, which is the section to read before quoting either. What follows splits
> by **create date**, a different axis, and is kept because it explains *why*
> carried pipe slips as heavily as it does.

Splitting the pipe by when it was created asks whether pipe born *inside* a
quarter behaves differently from pipe carried into it. It does — and in the
opposite direction to the intuition.

Reproduce with `waterfall.slip_by_cohort(quarter_start)`.

### First: what the notebook actually does

Read the mechanics before comparing numbers, because `PRE_Q` and `IN_Q` there are
**not create-date cohorts**. They are two sequential push rounds applied to the
same pool:

```python
pre_q_out = calculate_outflow(existing, PRE_Q)
pre_q_in  = calculate_inflow(pre_q_out, PRE_Q)
in_q_out  = calculate_outflow(existing + pre_q_out + pre_q_in, IN_Q)   # same pool
in_q_in   = calculate_inflow(in_q_out, IN_Q)
adjusted  = existing + pre_q_out + pre_q_in + in_q_out + in_q_in
```

`IN_Q` acts on the post-`PRE_Q` balance, which includes the pipe that just
survived round one plus what flowed back in from earlier months. So the two rates
**compound**: for Q3 FY26 the notebook's 0.46 then 0.55 leaves
`0.54 x 0.45 = 24.3%` of a month's pipeline in place before inflow returns any of
it. That is a far harsher haircut than either rate suggests read alone, and it is
not comparable to our single-round measured rate without saying so.

The distribution is `{3 months: 0.60, 6 months: 0.40, 9 months: 0.00}`. A +3 month
shift from any month of a quarter lands in the next quarter, so this is directly
comparable to our quarter-offset curve: **Q+1 60% / Q+2 40% / Q+3 0%** against a
measured pooled **54.9 / 33.8 / 7.8 / 3.2**. Close on the first two hops; the
notebook truncates a tail that is really about 11%.

### The measured cohorts

Three cohorts, all four completed quarters pooled, value-weighted:

| Cohort | Opps | Value | Avg deal | Slip | Won | Lost | Held |
|---|---:|---:|---:|---:|---:|---:|---:|
| `in_q` — created in the quarter | 1,496 | $75,078,424 | $50,186 | **42.4%** | 31.9% | 11.7% | 14.0% |
| `pre_q` — carried in | 3,675 | $322,690,718 | $87,807 | **59.3%** | 11.0% | 27.7% | 2.0% |
| `pre_q_reslip` — carried in, already moved once | 1,957 | $167,796,511 | $85,742 | **59.7%** | 13.4% | 22.7% | 4.2% |

**In-quarter creates slip LESS, not more** — 42.4% against 59.3%. And they win
nearly three times as often, 31.9% against 11.0%.

**Having already slipped barely moves the slip rate** — 59.7% against 59.3%.
What it moves is the *win/lose* mix: re-slipped pipe wins more (13.4% vs 11.0%)
and loses less (22.7% vs 27.7%). The 13.4% here is the same population as the
13.1% in "Slip is serial" above, measured a different way, and the two agree.

### Two confounds, both ruled out

**Exposure.** An opp created in month 3 has less quarter left in which to slip, so
a raw `in_q` rate is biased downward. Restricting to opps created in the
quarter's *first* month — near-full exposure — the gap narrows but does not close:

| `in_q` created in | Opps | Slip | Won |
|---|---:|---:|---:|
| Month 1 | 697 | 48.2% | 25.0% |
| Month 2 | 501 | 36.4% | 37.7% |
| Month 3 | 298 | 37.4% | 40.4% |

48.2% against `pre_q`'s 59.3%. Per quarter, month-1 `in_q` vs `pre_q`: Q3 FY25
46.2 / 59.4, Q4 FY25 57.5 / 57.3, Q1 FY26 36.3 / 62.0, Q2 FY26 46.3 / 61.9. The
direction holds in three quarters of four; **Q4 FY25 is the exception and ties.**

**Deal size.** `in_q` deals average $50k against $88k, so size could be doing the
work. It is not — the gap survives inside every band:

| Band | `in_q` slip | `pre_q` slip | `pre_q_reslip` slip |
|---|---:|---:|---:|
| <$25k | 34.2% | 38.9% | 43.1% |
| $25–100k | 45.6% | 56.6% | 59.6% |
| $100–500k | 45.5% | 62.3% | 58.8% |
| $500k+ | 11.0% | 57.4% | 79.3% |

The $500k+ row rests on 8 / 24 / 10 opps and should not be read as a finding.

### The mechanism

`pre_q` is a **residual pool**, and residual pools are enriched in non-closers.
Pipe created earlier that was going to close on time already did, in an earlier
quarter; what remains to be carried in is disproportionately the pipe that keeps
pushing. `in_q` has not been through that filter yet — it still contains its fast
closers, which is why it both slips less and wins three times as often.

This is the same selection effect documented under "Anchoring matters more than
the window", where the measured rate rises from 58.4% to 63.9% as a quarter
progresses. Here it acts across cohorts instead of across time.

**So the intuition that once-moved pipe should slip less is not what the data
shows.** Slipping once tells you the opp is in the sticky pool; it does not make
it more likely to close next time. It only makes it slightly less likely to be
written off outright.

### Caveats on this measurement

- **Create dates come from `sku_nacv`**, the only source carrying one — the
  snapshot table has no `CreateDate`. It covers 86.9% of snapshot opps; the rest
  fall back to first-appearance in the feed, and opps with neither are excluded
  and counted on `.attrs["unknown_create"]` (187 / 98 / 7 / 0 by quarter).
- **`pre_q_reslip` is unobservable for Q3 FY25.** The historic feed opens on
  2025-07-01, exactly that quarter's start, so no opp can show an earlier close
  date. The empty cohort means "not observable here", not "no re-slipped pipe
  existed" — `.attrs["reslip_observable"]` reports which case you are in.
- **Anchoring differs from `slip()` by necessity.** An in-quarter create does not
  exist at the quarter start, so each opp is anchored at its own first in-quarter
  observation. Both paths share `_partition()` for the outcome rule, so the
  won/lost/slipped/held definition is identical.
- Nothing here is wired into `derive_targets()`. No target moves.

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

## Pre-Q and In-Q are a TIMING split — read this before using either term

Settled with the Strategic Analytics lead, 2026-08-11. The two terms divide slip
by **when it happens**, relative to the quarter being forecast:

| Term | The slip that happens | Applies to |
|---|---|---|
| **Pre-Q slip** | between now and the quarter **starting** | pipe already dated into that quarter |
| **In-Q slip** | between the quarter's start and its **end** | the same pipe, once the quarter is running |

Both act on **existing open pipe**. Neither is a statement about when the pipe was
created. The sales cycle curve is the separate mechanism, and it governs **newly
created pipe only** — which is precisely why the two cannot double-count:
`derive_targets()` multiplies the curve by `create` and never by `existing`.

> The cohort measurement above (`slip_by_cohort`) splits by **create date**, a
> different axis. It is a true finding about the data — carried pipe is a residual
> pool and slips more — but it is **not** what Pre-Q / In-Q mean, and its 42.4% /
> 59.3% must never be used as the Pre-Q / In-Q rates.

### The consequence: the same quarter needs different handling by run date

- **In-flight quarter** (Q3 FY26, run 2026-08-10). Pre-Q slip has **already
  happened** and is baked into today's balance. Closed won is real and observed.
  Only In-Q slip remains — measured from the equivalent point in flight, not from
  the quarter start.
- **Future quarter** (Q4 FY26, run 2026-08-10). It suffers **both**. Today's
  Q4-dated pipe still has 52 days in which to leak before the quarter opens, and
  then the in-quarter rate on whatever survives.
- **Annual planning** (all four quarters, run before Q1). Every quarter is a
  future quarter, and the later ones start with little or no existing pipe. That
  is the case the workbook was built for, and it is why slip inflow matters so
  much there: the back half of the year is supported almost entirely by what the
  front half creates and pushes forward.

`slip()` today measures **In-Q slip only**. Pre-Q slip is measured nowhere.

---

## What supplies a future quarter, and what drains it

For a quarter not yet started, six terms. The model has two.

**Inflows**

| # | Term | Status |
|---|---|---|
| 1 | Pipe already dated to close in the quarter | **modelled** — `open_pipe_at()` |
| 2 | Sales cycle waterfall from earlier quarters' creates | **modelled** — the `carried` tail |
| 3 | **Slip inflow** — pipe pushed out of earlier quarters that lands here | **modelled** — `slip_inflow()` |

**Drains**

| # | Term | Status |
|---|---|---|
| 4 | **Pre-Q slip** — leaks out before the quarter opens | **modelled** — `pre_q_slip()` |
| 5 | In-Q slip — pushes out during the quarter | **modelled** — `(1 - slip_rate)` |
| 6 | Loss | **modelled implicitly** — `win_rates` uses a won+lost denominator, so pipe that dies is already inside the `later` rate. Do **not** add a separate attrition haircut; it would double-count. |

Terms 3 and 4 are the workbook's `Pre Q Inflow` / `Pre Q Outflow` / `In Q Inflow`
/ `In Q Outflow` columns. **Implemented 2026-08-11**; all six terms are now
present. `existing_pipe_bookings()` applies them in the order they happen:

```
adjusted = open_pipe x (1 - pre_q_slip) + slip_inflow
expected = adjusted x (1 - in_q_slip) x later_win_rate
```

Inflow arrives at the quarter boundary, so it is added AFTER the Pre-Q haircut
and escapes it — but it IS exposed to In-Q slip, because arriving pipe can slip
again, which the 55% serial re-slip rate says it frequently does.

**Two double-counts the implementation has to avoid, and does:**

1. **Against the source quarter.** `(1 - slip_rate)` removes from the source
   exactly the dollars `slip_inflow()` forwards. Neither quarter claims them twice.
2. **Against the sales cycle tail.** `slip_inflow()` acts on **existing open pipe
   only, never on `create`**. Newly created pipe already reaches later quarters
   through the sales cycle curve; routing it through slip as well would count it
   twice. This is the constraint to preserve if the function is ever extended.

### Measured sizes, as at 2026-08-10

| | |
|---|---:|
| Q3 FY26 open pipe today | $75,741,019 |
| Q3 FY26 closed won to date | $5,400,518 |
| Q4 FY26 open pipe today | $211,460,105 |
| Q3 FY25 slip rate from the equivalent point (2025-08-10, 51 days left) | 64.1% |
| Q3 FY25 destination curve from that point | Q+1 87.2% / Q+2 7.7% / Q+3 3.2% |
| **Q3 FY26 pipe expected to slip** | $48,568,861 |
| ⟶ **slip inflow landing in Q4** (term 3) | **+$42,329,670** |
| **Pre-Q slip on Q4's own pipe** at 52 days out, prior-year rate 15.3% (term 4) | **−$32,353,396** |
| **Net unmodelled swing into Q4** | **+$9,976,273** |

### What the terms actually moved

Run 2026-08-10, Q3 + Q4 FY26, Territory grain:

| | Q3 FY26 | Q4 FY26 |
|---|---:|---:|
| Pre-Q slip rate | — (in flight) | 15.3% at 52d lead, from Q4 FY25 |
| Slip inflow received | — | $49,655,938 (87.2% of $56,974,986 slipping out of Q3) |
| Expected from existing pipe, before | $2,953,360 | $12,265,604 |
| Expected from existing pipe, after | $2,953,360 | $12,928,530 |

**Q3 moves by exactly $0** — it is in flight, so its Pre-Q slip has already
happened and there is no earlier quarter in the solve to send it inflow. That
zero is the regression test worth keeping (`test_pre_q_slip_leaves_an_in_flight_
quarter_untouched`), because it is what proves the terms did not leak into the
in-flight path.

**These terms are correctness, not reconciliation.** The net effect on Q4 is
about **+$0.7M of bookings**, because existing pipe converts weakly — $211,460,105
of open pipe yields only ~$12.9M after the haircuts and the 0.158 `later` rate.
Do not present them as closing the gap to published; the `$0` sales cycle tail
(term 2, zero for Q3 because no earlier quarter is in the solve) is a separate
and much larger question.

### How the agent should handle this

1. **Never quote a Pre-Q rate and an In-Q rate as if they were interchangeable
   with the cohort figures.** Check which axis is being asked about.
2. **State which quarter is in flight and which is future** before quoting slip,
   because in-flight quarters carry only term 5 and future quarters carry 4 and 5.
3. **All six terms are present as of 2026-08-11.** When reporting a future
   quarter, say which Pre-Q rate and which destination share were used and which
   historic quarter they came from — `.attrs` on both functions carries it.
4. **Do not add term 6.** Loss is already in the win rate denominator.
5. Both assumptions for a quarter come from **the same quarter a year earlier** —
   rate and destination together, never pooled.

---

## What the model does with slip today, and what it ignores

**Uses:**
- `slip_rate` per grain key, as the `(1 - slip_rate)` haircut on open pipe
- measured per quarter on the same quarter a year earlier
  (`prior_year_quarter()`), anchored at the equivalent point in flight
- this is **In-Q slip only** — see the timing split above

**Ignores:**
- **Pre-Q slip.** A future quarter's pipe leaks before the quarter opens and
  nothing accounts for it. ~15.3% at 52 days out on Q4's prior-year analogue.
- **Destination / slip inflow.** Nothing receives the slipped pipe. The workbook's
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
3. **Pre-Q and In-Q are a timing split, not a create-date split**, and both act on
   existing open pipe. The sales cycle curve governs newly created pipe only.
4. Mid-quarter, the **equivalent point-in-time** is the like-for-like anchor, and
   an in-flight quarter carries **no Pre-Q slip** — it has already happened.
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
6. ~~Do the sales cycle curve and the slip rate double-count?~~ **Decided
   2026-08-11: no.** The curve applies to newly created pipe only; slip applies to
   existing open pipe. Different terms, no overlap.
7. ~~Are Pre-Q and In-Q create-date cohorts?~~ **Decided 2026-08-11: no, they are
   a timing split** — see the section above. The notebook's two sequential rounds
   are therefore correct in structure: round one is the leak before the quarter
   opens, round two is the leak during it.
8. ~~Should Pre-Q slip and slip inflow be wired into the solve?~~ **Decided
   2026-08-11: yes, both, and they are.** The model should be right on its own
   terms; agreement with the published total is a separate question and is not
   the criterion for whether a real mechanism gets built.
9. **Should slipped pipe carry its own win rate?** Inflow currently earns the
   general `later` rate (0.158), but once-slipped pipe was measured winning at
   13.1%. Applying the lower rate would reduce what inflow contributes.
10. ~~Unattributed pipe is dropped silently.~~ **Closed 2026-08-11 — working as
    intended.** It is entirely **`AMS Specialty`**: 5 opps, $892,135, all dated
    into Q4 FY26 ($248,899 of expected bookings). It is absent from `bts.parquet`
    because `BTS_SQL` filters `ActiveTeam = 'Active'`, and absent from
    `Target_Monthly` and `sku_nacv` too. **Confirmed with the model owner: it is
    an inactive team, along with `AMS DevOps`, `APAC DevOps` and `EMEA DevOps`
    (all three carry a zero Bookings target in Q3–Q4 FY26, so no target is
    lost).** Residual pipe on a disbanded team has no target and rightly earns no
    create — do not "fix" it by re-mapping the team.

    The derivation still reports it as `UNTARGETED PIPE EXCLUDED`, as information
    rather than a defect. **Only investigate if a currently selling team appears
    in that note** — the same path would otherwise silently swallow a live team
    missing from the mapping table, and a smaller existing-pipe term inflates
    required create with nothing in the output to reveal why.
11. **Does destination belong in the solve at all**, i.e. should slipped pipe feed
   the destination quarter's existing-pipe term? That is the workbook's
   inflow/outflow model, and it is deliberately **not** implemented yet — it
   would move every quarter after the first while the `$0` sales cycle tail is
   still unresolved.
