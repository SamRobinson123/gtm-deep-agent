# Pipe Create waterfall — how the target is derived

**Source:** `data/legacy/Pipeline Creation Quarter Product V20.xlsm`,
sheet `Pipeline Waterfall (Quarterly)` (1,785 x 49)
**Status:** **RECONSTRUCTED FROM FORMULAS — not confirmed by an owner.**
**Not implemented in Python.** Nothing in `pipeline/` reproduces this.

Read with [`../tables/target-monthly.md`](../tables/target-monthly.md) and
[`../reference/legacy-pipe-create-xlsm.md`](../reference/legacy-pipe-create-xlsm.md).

---

## What this is, and why it matters

The Python model in [`../models/pipe-create.md`](../models/pipe-create.md)
**measures** pipe create — first appearance in the snapshot feed — and compares
it to a target it reads from `Target_Monthly.csv`. It never explains **where that
target came from.**

This sheet is where. It runs in the *opposite* direction: it takes a pipe-create
figure as an **input**, spreads it across future close quarters by maturation
weights, applies win rates, and lands on expected bookings — which is then
compared to the bookings target. Solving that backwards is how a pipe create
target is set.

```
Pipe Create (input)
  → spread across close quarters Q0…Q+8 by maturation weights
  → win rates applied (in-quarter rate vs pre-quarter rate)
  → Pipe Won
  → + pre-existing pipe and flows → Adj Bookings
  → compared against the bookings Target
```

> **The column contract below is reconstructed by reading cell formulas and
> profiling 1,528 data rows on 2026-08-10.** No owner has confirmed those
> details. The derivation chain in the next section **was explained by the
> Strategic Analytics lead** on 2026-08-10 and is marked separately.
> Where any of it disagrees with the Python model, surface the discrepancy —
> do not reconcile.

---

## The derivation chain

**Source: explained by the Strategic Analytics lead, 2026-08-10.** Partially
verified against the workbook — verification status noted per step.

The waterfall is the middle of a four-step system. Reading the sheet alone shows
*what* is computed but not *where the assumptions come from* or *what question is
being solved*.

### Step 1 — Sales cycle sets the maturation curve

**Source: `[src].[sku_nacv_fact]`** — see
[`../tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md).
Definition given by the Strategic Analytics lead, 2026-08-10.

**Sales cycle = the number of QUARTERS from opportunity creation to close.**
Count from `Stage_1_Start_Date_Corrected` (the create / discovery date — the file
uses it as `Create_Month`) to `Opp_Closed_Date`, for deals that reached `Closed`
or `Closed Won`. Bucket the offset: in-quarter, Q+1, Q+2, … That distribution of
closed dollars across offsets **is** the `Q0 wt` … `Q+8 wt` vector.

It is a **quarter-offset distribution, not an average duration.** A mean number
of days cannot allocate dollars across future quarters; the bucketed distribution
can.

*Why this table and not `opportunity_live`:* `sku_nacv_fact` is **product-grain**,
which is what allows maturation to be fitted at Territory x Product — matching the
10 distinct weight vectors observed for a single territory in the workbook.
Opportunity-grain data cannot produce a per-product curve.
**This resolves the earlier open question about where per-product fitting happens.**

*Cross-check:* the workbook's `Create VS Close` sheet (3,541 x 5) computes the
same shape at territory grain — closed dollars bucketed `Q0 (close)` …
`Q+6 (close)`. For `AMS Core East Canada`: `0.132, 0.191, 0.364, 0.206, 0.099,
0.008`. It is the blended view of the per-product curves, useful for validating a
derivation rather than as its source.

**`SKU_SQL` does not currently select `Stage_1_Start_Date_Corrected`.** Adding it
is what makes sales cycle computable from a standard pull.

This is why sales cycle is load-bearing rather than descriptive: a territory with
a longer cycle pushes weight into later quarters, so more pipe must be created
*earlier* to land the same bookings.

### Step 2 — Slip analysis sets the movement assumptions

**Source: `[rep].[trf_opp_daily_snapshot_new]`** — see
[`../tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md).
Definition given by the Strategic Analytics lead, 2026-08-10.

**Slip = open pipe that neither closed nor was won, and moved to a different
quarter.** Measured on a *historic* quarter so the outcome is known:

1. Take the **open pipe at the beginning of the quarter** (e.g. Q1) from the
   snapshot feed.
2. Follow that same pipe forward to the end of the quarter.
3. Partition it: reached `Closed`, reached `Closed Won`, or **neither** — still
   open with its `CloseDate` moved into a later quarter. **That last bucket is
   slip**, and which quarter it landed in matters.

This is what populates `In Q Inflow`, `In Q Outflow`, `Pre Q Inflow`,
`Pre Q Outflow`.

**Slip and maturation are different measurements, not two names for one thing:**

| | Maturation curve | Slip |
|---|---|---|
| Population | Newly *created* pipe | Pipe already open at quarter start |
| Question | When will new pipe close? | Where did existing pipe move to? |
| Source | `sku_nacv_fact` (create → close) | `trf_opp_daily_snapshot_new` (point-in-time) |
| Shape | Distribution across Q0…Q+8 | Share that pushed out, and to where |
| Acts on | `Pipe Create` (col S) | `Pre Q Bookings` (col G) |

*Anchoring caveat:* "beginning of the quarter" is a point-in-time anchor, and root
`CLAUDE.md` invariant 5 has the Python pipeline anchor week 1 against a
**pre-quarter buffer** precisely because opps enter the snapshot feed 1–4 days
late. Whether the Excel slip analysis uses the same buffered anchor is
**unknown**, and is a plausible source of disagreement between the two.

*Not verified:* no code implementing slip exists in the corpus; the workbook
carries results rather than the calculation.

### Step 3 — Goal seek solves for required pipe create

The bookings target is the constraint. Expected bookings from pipe that already
exists — adjusted for slip and multiplied by win rates — is subtracted from it.
The remainder is the gap, and **goal seek solves for the `Pipe Create` figure
that closes it.**

This is why `Pipe Create` (col S) is an *input* to this sheet: it is the solved
variable, not a measurement. It is the number that ends up in
`Target_Monthly.csv` as the `Pipeline` target.

*Verified structurally:* `Adj Difference` (`=Targets − Adj Bookings`) is the gap
being closed, and `Pipe Won` is the lever. *Not verified:* the goal-seek
mechanism itself — no solver, macro, or iterative formula was located.

### Step 4 — Each quarter's pipe must support itself and future quarters

Because only ~10–14% of created pipe closes in its own quarter, the pipe created
in any quarter is mostly serving **later** quarters. So the required create for a
quarter is not a single-quarter solve: it must cover that quarter's own residual
need *and* seed the maturation tail that subsequent quarters depend on.

**This makes the system multi-quarter and coupled**, not a per-quarter
calculation repeated four times. A change to one quarter's create target
propagates forward through the maturation curve into every subsequent quarter's
starting position.

*Not verified.* How the coupling is solved — sequential, simultaneous, or by
hand — is **not established** and is the largest remaining gap.

### Chain summary

```
sales cycle (create → close, per territory)   → maturation weights  Q0…Q+8
slip analysis (first snapshot → movement)     → inflow / outflow columns
        ↓
bookings target − expected bookings from existing pipe
        ↓  goal seek
required Pipe Create  ──→ Target_Monthly.csv  `Pipeline` target
        ↓  maturation curve
supports this quarter (~10-14%) + all subsequent quarters (~86-90%)
        ↓
couples every quarter to every later quarter
```

---

## Column contract

Grain: **Geo x Territory x Product x Quarter Date** (calendar quarter-end).

### Dimensions — A–F

| Col | Header | Notes |
|---|---|---|
| A | `Geo` | |
| B | `Territory` | **Uses stale names** — `New Logo`, not `AMS Corporate`. See [`../tables/headcount.md`](../tables/headcount.md) |
| C | `Product` | Product grain — relevant to root `CLAUDE.md` invariant 10 |
| D | `Quarter Date` | Calendar quarter-end date |
| E | `Year` | `=YEAR(D)` |
| F | `Quarter` | `="Q"&ROUNDUP(MONTH(D)/3,0)` — **calendar quarter, not fiscal.** Do not assume it aligns to `Q3 FY26` |

### Bookings roll-up — G–K

| Col | Header | Formula |
|---|---|---|
| G | `Pre Q Bookings` | `=$L+($N+$O+$Q+$R)*$AN` |
| H | `Adj Bookings` | `=G+AP` |
| I | `Targets` | Hardcoded value — the bookings target |
| J | `Difference` | `=I-G` |
| K | `Adj Difference` | `=I-H` |

`Pre Q Bookings` = closed won, plus the pre-quarter win rate applied to existing
pipe and flows. `Adj Bookings` adds bookings expected from pipe won in prior
quarters. `Adj Difference` is the gap the new pipe create must fill.

### Pipeline movement — L–S

| Col | Header | Sign | Non-zero rows |
|---|---|---|---|
| L | `Closed Won` | + | |
| M | `Closed Lost` | + | |
| N | `Existing Pipe` | + | 522 |
| O | `In Q Inflow` | + | 448 |
| P | `In Q Outflow` | **negative** | 595 |
| Q | `Pre Q Inflow` | + | 298 |
| R | `Pre Q Outflow` | **negative** | 378 |
| S | `Pipe Create` | + | 640 |

**Outflows are stored negative** (verified: `In Q Outflow` min −$2,363,586,
`Pre Q Outflow` min −$2,901,699, never positive). So the `+$R` in the
`Pre Q Bookings` formula subtracts correctly. **Never negate them again.**

### Maturation weights — T–AB

`Q0 wt` … `Q+8 wt`. The share of pipe created in this quarter expected to close
in each subsequent quarter.

**16 distinct weight vectors** across the sheet — these are empirical maturation
curves per Geo/product, not one global assumption. The most common:

| Vector (Q0 → Q+8) | Rows |
|---|---:|
| `0.13, 0.23, 0.36, 0.13, 0.13, 0.02, 0, 0, 0` | 340 |
| `0.14, 0.20, 0.35, 0.14, 0.15, 0.02, 0, 0, 0` | 332 |
| `0.10, 0.19, 0.27, 0.35, 0.05, 0.01, 0.01, 0.01, 0` | 170 |
| `0.10, 0.23, 0.44, 0.13, 0.08, 0.01, 0, 0, 0` | 170 |

Shape: only ~10–14% of created pipe closes in the quarter it was created; the
peak lands at Q+2 or Q+3. **Any mental model that treats pipe create as
same-quarter revenue is wrong by roughly an order of magnitude.**

### Close distribution and win rates — AC–AQ

| Col | Header | Formula |
|---|---|---|
| AC | `Q0 (close)` | `=$S*T` — pipe create x Q0 weight |
| AD–AK | `Q+1 (close)` … `Q+8 (close)` | Same pattern per quarter |
| AL | `Pipe Transposed (Q Waterfall)` | `=SUM(AC:AK)` |
| AM | `In Q Win Rate` | Applied to the in-quarter slice only |
| AN | `Pre Q Win Rate` | Applied to all future-quarter slices |
| AO | `Pipe Won In Q` | `=AC*$AM` |
| AP | `Pipe Won Pre Q` | `=SUM(AD:AK)*$AN` |
| AQ | `Pipe Won` | `=AO+AP` |

Win rates are **granular and derived**, not round numbers — observed pairs
include `(0.43393573125, 0.159109768125)` and `(0.4821508125,
0.159109768125)`, with dozens of distinct combinations. The `0.7 / 0.35` visible
in the first row is not representative.

The in-quarter win rate is consistently **~2.5–3x the pre-quarter rate**,
which is the sheet's core assumption: pipe closing in its creation quarter
converts far better than pipe that has to survive to a later quarter.

**Columns AV/AW carry a second `In Q Win Rate` / `Pre Q Win Rate` pair**
(`0.55` / `0.15` in row 2) that no formula in A–AQ references. Purpose unknown —
possibly a scenario or a superseded assumption. **Do not use.**

---

## Known issues — verified, unresolved

### 1. Weight vectors do not sum to 1.0

| Sum | Rows |
|---:|---:|
| 1.00 | 848 |
| 0.99 | 672 |
| 0.98 | 8 |

**680 of 1,528 rows lose 1–2% of created pipe**, which never lands in any close
quarter and so never converts to bookings. Consistent with weights rounded to
two decimals. Small per row, systematic in direction — it biases required pipe
create *upward*.

### 2. `In Q Outflow` is excluded from `Pre Q Bookings`

The formula is `=$L+($N+$O+$Q+$R)*$AN` — it includes `$O` (`In Q Inflow`) and
`$R` (`Pre Q Outflow`) but **omits `$P` (`In Q Outflow`) entirely.** 595 rows
carry a non-zero `In Q Outflow`, reaching −$2,363,586.

Counting in-quarter inflow while ignoring in-quarter outflow overstates
`Pre Q Bookings`. **Whether this is deliberate or an error cannot be determined
from the formulas.** It needs an owner's answer before anything downstream
relies on `Pre Q Bookings`.

### 3. Calendar quarters, not fiscal

Column F derives from `MONTH(D)/3`, giving calendar quarters. Tricentis FY26
Q3 has 14 weeks and does not align to a calendar quarter — root `CLAUDE.md`
invariant 3. **Do not join this sheet to fiscal-quarter output without an
explicit mapping.** The workbook's `Quarter Matrix` sheet (36 x 10) may hold
that mapping; unaudited.

### 4. Stale territory names

Column B uses `New Logo`, superseded by `AMS Corporate`. Assume other names are
stale too and join via `Target_Monthly.csv` as canonical.

---

## Open questions

1. **Is this still how targets are set?** The workbook is dated 2026-05-15 and
   `Target_Monthly.csv` 2026-07-27. Whether FY26 targets came from this sheet or
   a successor is **unknown**.
2. ~~Where do the maturation curves come from?~~ **Fully answered 2026-08-10:**
   from sales cycle computed on `[src].[sku_nacv_fact]` —
   `Stage_1_Start_Date_Corrected` → `Opp_Closed_Date`, bucketed by quarter offset.
   The per-product fitting question is resolved too: `sku_nacv_fact` is
   product-grain, which is exactly what yields Territory x Product curves.

2a. **Possible double-count — unresolved, and it biases pipe create upward.**
   The maturation curve is fitted on *actual historical close dates*, which
   already embed every slip those deals experienced: a deal created in Q1 that
   slipped twice and closed in Q4 appears in the curve at `Q+3`, not as `Q+1`
   plus two slips. So slip behaviour is **already inside the maturation curve**.
   Applying a separate slip term on top is only legitimate if the two act on
   genuinely disjoint populations — new creation vs the pre-existing open base.
   On the face of it they do, but an opp created *within* the quarter that then
   slips could fall into both. **Testable** once create dates are pulled: check
   whether the fitted curve already reproduces observed slip. If it does, the
   separate slip term is redundant and inflates required pipe create.
3. **What time window fits the curves?** Trailing how many quarters? A curve
   fitted on a period containing a pricing or packaging change would misstate
   maturation. Not established.
4. **Does the slip analysis use a pre-quarter buffer?** The Python pipeline does,
   per root `CLAUDE.md` invariant 5. If the Excel slip analysis anchors on the
   first in-quarter snapshot without a buffer, the two disagree on starting pipe
   by construction. **This is the most likely source of a reconciliation gap.**
5. **How is the multi-quarter coupling solved?** Sequentially, simultaneously, or
   by hand? Step 4 of the derivation chain is understood in principle but not in
   mechanism.
6. **Are win rates fitted or set?** Their precision (`0.43393573125`) suggests
   computed, but the computation is not in this sheet.
7. **What are AV/AW for?**
8. **Should this be reimplemented in Python?** It is the only artifact connecting
   bookings targets to pipe create targets. The Python pipeline currently
   consumes the target as a given and can neither explain nor re-derive it, so it
   cannot answer "is this target achievable?" — only "did we hit it?".
