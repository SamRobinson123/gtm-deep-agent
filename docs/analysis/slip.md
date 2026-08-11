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
| **Pooled** | | | **54.9%** | **33.8%** | **7.8%** | **3.2%** |

**Q4 is the outlier and it is a real seasonal effect.** Pipe slipping out of Q4
skips Q1 and lands in Q2 — a calendar year-boundary push. Everywhere else the
next quarter dominates.

> **The pooled curve describes no actual quarter.** It is the average of an 80/11
> shape and a 41/43 shape. Use it only as a fallback, and never to reason about
> Q4.

Note this is a **stronger seasonality signal than the slip RATE shows.** The rate
varied only 52.6%–58.4% across these four quarters, while the destination split
ranges from 41% to 80% into Q+1. If slip is going to be seasonal anywhere, it is
here.

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

Slipped opps get re-scoped as they move. Any destination model has to state
**which valuation it carries forward** — the value at the anchor, or the value
at the moment it slipped. They differ by 3% in aggregate and far more per opp.

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

## Stated assumptions vs measured facts

**Measured** (reproducible from the functions above): every table in this file.

**Stated assumptions** — per the Strategic Analytics lead, 2026-08-11, these are
choices, not established facts:

1. **Slip is seasonal**, so a quarter's assumption comes from the same quarter a
   year earlier rather than the most recent completed quarter.
2. **Q1–Q2 FY26 are the recency alternative**, carried so the two readings can be
   compared. The docs flag both windows as unestablished.
3. Mid-quarter, the **equivalent point-in-time** is the like-for-like anchor.

---

## Open questions

1. **Should destination be fitted per quarter-of-year?** The Q4 year-boundary
   effect says yes; the sample size per quarter says be careful.
2. **Which valuation carries** — value at anchor, or value at the moment of slip?
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
