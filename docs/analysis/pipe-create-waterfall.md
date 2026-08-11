# Pipe Create waterfall — how the target is derived

**Source:** `data/legacy/Pipeline Creation Quarter Product V20.xlsm`,
sheet `Pipeline Waterfall (Quarterly)` (1,785 x 49), plus the Python rebuild.
**Status:** **IMPLEMENTED** in `agent/waterfall.py::derive_targets()`. The
workbook's *column contract* is still reconstructed from cell formulas and is
marked as such below; the *derivation logic* has been explained by the Strategic
Analytics lead and rebuilt.
**Derived totals are NOT reconciled to published.** Treat them as the model's own
answer under the stated assumptions, not as a replacement for a published target.

Read with [`slip.md`](slip.md) (the movement half — read it before touching
anything slip-related), [`../tables/target-monthly.md`](../tables/target-monthly.md)
and [`../reference/legacy-pipe-create-xlsm.md`](../reference/legacy-pipe-create-xlsm.md).

---

## What this is, and why it matters

The Python model in [`../models/pipe-create.md`](../models/pipe-create.md)
**measures** pipe create — first appearance in the snapshot feed — and compares
it to a target it reads from `Target_Monthly.csv`. It never explains **where that
target came from.**

This sheet is where. It runs in the *opposite* direction: it takes a pipe-create
figure as an **input**, spreads it across future close quarters by sales cycle
weights, applies win rates, and lands on expected bookings — which is then
compared to the bookings target. Solving that backwards is how a pipe create
target is set.

```
Pipe Create (input)
  → spread across close quarters Q0…Q+8 by sales cycle weights
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

## THE MODEL AS IT STANDS — 2026-08-11

The single place to read what the Python actually computes. Everything below this
section is either the workbook contract or the history of how each piece was
settled.

### The solve, per grain key, per quarter, in chronological order

```
  bookings target                                    GIVEN   Target_Monthly.csv
− closed won to date                              MEASURED   snapshot
− expected bookings from existing pipe            MODELLED   see below
− sales cycle tail from earlier quarters          MODELLED   earlier quarters in this solve
= gap
÷ yield per dollar created  = Q0 weight × In Q win rate
= required pipe create
→ pipe create target = max(required, historic floor)
```

### The existing-pipe term, in the order things happen in the world

```
adjusted = open_pipe × (1 − Pre Q slip) + slip_inflow
expected = adjusted  × (1 − In Q slip)  × Pre Q win rate
```

| # | Term | Where it comes from |
|---|---|---|
| 1 | `open_pipe` | `open_pipe_at()` — snapshot, open, `CloseDate` in the quarter |
| 2 | Pre Q slip | `pre_q_slip()` — prior-year quarter at the **same lead time**. **Zero for the quarter in flight**: it has already happened and is inside the observed balance |
| 3 | `slip_inflow` | `slip_inflow()` — existing open pipe pushed out of an earlier quarter in this solve, landing here. Added *after* the Pre Q haircut, so it escapes it; still exposed to In Q slip |
| 4 | In Q slip | `slip()` — prior-year quarter, anchored at the equivalent point in flight |
| 5 | Pre Q win rate | `win_rates()["pre_q"]` |

### Terminology — not negotiable

| Name | Meaning |
|---|---|
| **In Q win rate** | closed in the SAME quarter it was created |
| **Pre Q win rate** | closed in a LATER quarter than created — pipe that existed before the quarter it books in |
| **In Q slip** | slip occurring **during** the quarter |
| **Pre Q slip** | slip occurring **before** the quarter opens |

In Q / Pre Q is one axis applied to two quantities: win rates to *conversion*,
slip to *movement*. `later` / `later_win_rate` was an internal coinage for the
Pre Q win rate and was **retired 2026-08-11**.

### Two regimes — which one you are in changes what is correct

| | In flight / near term | Annual planning |
|---|---|---|
| Existing pipe | **observed** in the snapshot | nothing to observe |
| Pre Q slip | already happened → **zero** | must be modelled |
| Sales cycle tail into the first quarter | **zero, and correct** — prior-quarter creates due now are already in the observed open pipe | must be modelled from prior creates |
| Solve window | the quarters you care about | must run from Q1 forward |

**The workbook is built for the second regime**, which is why it reaches back 8
quarters. Applying its structure to an in-flight run double-counts.

### What is deliberately NOT done

- **The composition of the existing-pipe term is not "corrected".** Measured
  conversion of mid-quarter open pipe is 11.9–15.6%; the composed formula gives
  5.5–7.1%. **Decided 2026-08-11: leave it.** See "the composition stays as it
  is" below.
- **Slipped pipe earns the general Pre Q win rate**, not the 13.1% that
  once-slipped pipe was measured winning at.
- **Serial slip does not compound** — one hop only.
- **Value drift is not applied.** Pipe carries its anchor valuation.
- **Untargeted pipe is excluded**, correctly — inactive teams carry no target.
  Reported as a note, not silently dropped.

---

## The derivation chain

**Source: explained by the Strategic Analytics lead, 2026-08-10.** Partially
verified against the workbook — verification status noted per step.

The waterfall is the middle of a four-step system. Reading the sheet alone shows
*what* is computed but not *where the assumptions come from* or *what question is
being solved*.

### Step 1 — Sales cycle sets the quarter-offset weights

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
which is what allows sales cycle to be fitted at Territory x Product — matching the
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
quarter.** Measured on a *historic* quarter so the outcome is known.

> **Slip has its own context file: [`slip.md`](slip.md).** It holds the
> measurement recipe, the anchoring rules, the **destination curves** (where the
> slipped pipe lands, by quarter offset), serial slip, value drift, and how to
> trace a single opportunity. Read it before changing anything slip-related; only
> the summary needed to follow the waterfall is repeated here.

Three headlines from that file, because they bear directly on this derivation:

- **Slip is where the destination matters.** Q3 FY25 sent 80% of slipped dollars
  to Q+1; Q4 FY25 sent only 41%, pushing 43% out to Q+2 across the calendar year
  boundary. The destination is far more seasonal than the slip *rate*, which
  moved only 52.6%–58.4% over the same four quarters.
- **Slip is serial.** 55% of once-slipped pipe slips again in its destination
  quarter, and once-slipped pipe wins at only 13.1% — well under the 0.158 mean
  Pre Q win rate this model applies to all pre-existing pipe. Applying the lower
  rate to slipped pipe specifically is **open**, not done.
- **Both halves are implemented (2026-08-11).** These four columns are what slip
  populates in the workbook — `In Q Inflow`, `In Q Outflow`, `Pre Q Inflow`,
  `Pre Q Outflow`. `slip()` and `pre_q_slip()` are the outflows; `slip_inflow()`
  is the inflow, and it forwards **existing open pipe only, never `create`** —
  newly created pipe already reaches later quarters through the sales cycle
  curve, so routing it through slip as well would double-count.

**Slip and sales cycle are different measurements, not two names for one thing:**

| | Sales cycle weights | Slip |
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

*Implementation status (2026-08-11):* fully implemented — `slip()` for the In Q
rate, `pre_q_slip()` for the Pre Q rate, `slip_destinations()` for where it
lands, and `slip_inflow()` to deliver it. The workbook carries only results, not
its own calculation, so the two still cannot be reconciled line by line.

**Pre Q and In Q slip are a TIMING split, not a create-date split** — both act on
existing open pipe. The sales cycle curve is the separate mechanism and governs
newly created pipe only, which is exactly what keeps the two from
double-counting: `derive_targets()` multiplies the curve by `create` and never by
`existing`. See [`slip.md`](slip.md) for the full treatment.

### Step 2b — Splits come from `sku_nacv_fact`

**Source: `[src].[sku_nacv_fact]`.** Definition given by the Strategic Analytics
lead, 2026-08-10.

A pipe create target is not one number. It is split across dimensions, and the
split proportions are computed from historical mix in `sku_nacv_fact`:

| Split | `sku_nacv_fact` column | Values in `Target_Monthly.csv` |
|---|---|---|
| Segment | `Segment` | `Tier 1` / `Tier 2` / `Tier 3` |
| Source | `Opportunity_Source_Logic` | `Marketing Sourced` / `Partner Sourced` / `Sales Sourced` |
| Deal Type | `Deal_Type` | `New Customer` / `Expansion` / `Upsell` / `Professional Services` |
| Product | `Family` via `PRODUCT_CASE` | `Tosca`, `qTest`, `Neoload`, `LC`, `Sealights`, `DI`, … |
| Territory | `Booking_Team_Static` | 38 sub-territories |

This is what gives `Target_Monthly.csv` its shape: **63,171 rows across 12
dimensions** are the derived target pushed down a split hierarchy, not 63,171
independently-set numbers. It is also what the workbook's `Pipe Create By Split`
sheet (113,113 rows — the largest) holds.

**Splits are a derivation input, not a decoration.** Product mix determines ASP
(invariant 2: ASP is derived per product, $33,158–$165,255 in Q3 FY26), so the
opp-count target depends on the product split. Change the mix, change the count.

#### Product names differ between the files — expected, not a defect

**Direction of flow, per the model owner (2026-08-10): `Target_Monthly.csv` is a
BY-PRODUCT of this process, not an input to it.** Splits and renaming are applied
*after* the pipe create target is derived. So the naming difference is an artifact
of that downstream packaging step, not a join bug to fix.

Observed difference, recorded for reference only:

| `Target_Monthly.csv` | `PRODUCT_CASE` emits | Q3 FY26 Pipeline |
|---|---|---|
| `LC` | `LiveCompare` | $17,906,662 |
| `DI` | `Data Integrity` | $12,730,530 |
| `Neoload` | `NeoLoad` (case) | $22,634,232 |
| `Tosca` · `qTest` · `Sealights` · `Recurring Services` | match | — |

**Do NOT rename either side.** A derivation should compute splits from
`sku_nacv_fact` in its own vocabulary and compare to the published file at
**aggregate** level, not by joining on product name. Renaming to force a join
would fabricate agreement between two artifacts of different stages.

### Step 3 — Goal seek solves for required pipe create

**The bookings target is GIVEN.** Per the model owner (2026-08-10) it arrives as
an input from finance/planning — it is not derived here. Today it is readable from
`Target_Monthly.csv` as the `Target_Type = 'Bookings'` rows, and it has not
changed, so that is the usable source for now. **Treat it as a parameter with that
default, not as a fixed lookup** — it will be supplied directly.

| Quarter | Bookings target (given) | Published pipe create | Implied ratio |
|---|---:|---:|---:|
| Q3 FY26 | $38,448,676 | $201,789,918 | 5.25x |
| Q4 FY26 | $58,971,436 | $192,223,413 | 3.26x |

**The ratio is not constant, and that is the point.** A flat coverage multiple
would give the same figure both quarters. Note the direction: Q4 carries a *higher*
bookings target but a *lower* pipe create target, because Q4's bookings are largely
served by pipe created in Q2/Q3 that is now maturing. That is the multi-quarter
coupling of Step 4, visible in the published numbers.

Expected bookings from pipe that already exists — slip-adjusted and multiplied by
win rates — is subtracted from the given bookings target. The remainder is the gap,
and **goal seek solves for the `Pipe Create` figure that closes it.**

This is why `Pipe Create` (col S) is an *input* to the sheet: it is the solved
variable, not a measurement.

#### The mechanism — RESOLVED 2026-08-10 from the workbook's VBA

Extracted from `Module13.GoalSeek_AdjDifference_FloorSafe_FAST` (the latest of
five iterations of the same macro; Modules 1, 10, 11, 12 are predecessors).

| Phase | Action |
|---|---|
| A | Clamp each row's `Pipe Create` **up** to its floor |
| B | **Row-by-row Excel `GoalSeek`**: change **S** (`Pipe Create`) until **AQ** (`Pipe Won`) equals **J** (`Difference` = Target − Pre Q Bookings) |
| C | Clamp surplus **down** — if the gap ≤ 0, reset `Pipe Create` to the floor |
| D | Regroup by `Year\|Territory\|Quarter` and raise to a territory-level floor |

The macro also **pins** `New Logo` and `EMEA Corporate` — skipping the solve and
setting them to floor — and hard-locks Q4-2025 with a scale factor.
**Do not reproduce either.** Per the model owner (2026-08-10) these are
situational workarounds, not part of the method.

#### The solve is linear — no iteration is needed

**CORRECTED 2026-08-11 by reading the cell formulas directly.** An earlier
reading of this section had `AD:AK` as this row's create spread forward, giving a
denominator that included the later weights. That is wrong, and it inflates yield
by ~3x on FY26 data.

Tracing the sheet's own formulas — note where the row index moves:

```
AC (Q0 close)      = $S{r} × T{r}                        this row's create
AD (Q+1 close)     = IF(same terr+prod, $S{r-1} × U{r-1}, 0)   ← PRIOR row
AE (Q+2 close)     = IF(same terr+prod, $S{r-2} × V{r-2}, 0)   ← two rows back
…AK (Q+8 close)    = IF(same terr+prod, $S{r-8} × AB{r-8}, 0)

AO (Pipe Won In Q) = AC × AM                    (× in-quarter win rate)
AP (Pipe Won Pre Q)= SUM(AD:AK) × AN            (× pre-Q win rate)
AQ (Pipe Won)      = AO + AP
```

Rows are sorted Territory → Product → Quarter ascending, so row `r-k` is exactly
k quarters earlier. The `IF` guards are what stop the tail bleeding across a
Territory x Product boundary.

**So `AD:AK` is the sales cycle tail ARRIVING from earlier quarters — it does not
depend on this row's `S` at all.** Only `AC` does. `Pipe Won` is still linear in
`Pipe Create`, but with a much smaller coefficient:

```
AQ = S × (Q0_wt × in_quarter_win_rate)  +  AP        where AP is a constant w.r.t. S
```

**Goal seek confirmed from `Module13` VBA:** target cell `Cells(i, 43)` = **AQ**,
goal value `Cells(i, 10)` = **J** (`Difference` = Targets − Pre Q Bookings),
changing cell `Cells(i, 19)` = **S**. Solving `AQ = J` gives the closed form:

```
      Difference − AP        Adj Difference (col K)
S* = ──────────────────  =  ────────────────────────
      Q0_wt × in_q_rate       Q0_wt × in_q_rate
```

since `K = I − H = I − G − AP`. That identity is why the macro is named
`GoalSeek_AdjDifference`.

The denominator is **bookings yield per dollar of pipe created, IN THE CREATING
QUARTER ONLY** — on FY26 data it averages 0.074 (range 0.017–0.193). Report it.

**Do not add the later weights to this denominator.** The tail is already
accounted for by being propagated forward into later quarters' `AP`. Counting it
in both places books the same dollars twice and understates required create by
~3x.

Excel iterates because `GoalSeek` is a generic 1-D solver that cannot know the
function is linear. A Python implementation should divide: exact, instant, no
convergence tolerance, and no silent failures. **The macro wraps its GoalSeek in
`On Error Resume Next`, so a row that fails to converge is left at whatever value
it held** — a defect a closed form cannot have.

### Step 4 — Each quarter's pipe must support itself and future quarters

Because only ~10–14% of created pipe closes in its own quarter, the pipe created
in any quarter is mostly serving **later** quarters. So the required create for a
quarter is not a single-quarter solve: it must cover that quarter's own residual
need *and* seed the sales cycle tail that subsequent quarters depend on.

**This makes the system multi-quarter and coupled**, not a per-quarter
calculation repeated four times. A change to one quarter's create target
propagates forward through the sales cycle curve into every subsequent quarter's
starting position.

#### How to solve the coupling — RESOLVED 2026-08-10

Quarter N's created pipe flows into quarter N+1's `Pipe Won Pre Q`, which reduces
N+1's gap. So quarters must be solved **in chronological order**, each propagating
its sales cycle tail forward before the next is solved.

Because every link is linear (see Step 3), the system is **triangular** — earlier
quarters affect later ones but never the reverse. It therefore solves exactly by
**forward substitution**, with no iteration and no simultaneous solve:

```
for each quarter in chronological order:
    gap        = bookings_target − expected_from_existing_pipe
                                 − sales_cycle_tail_from_earlier_quarters
    S          = gap / yield_per_dollar          # closed form, exact
    S          = apply_floor(S)                  # constraint, see below
    propagate  S × sales_cycle_weights → later quarters
```

Solving quarters independently would be wrong: it would ignore the tail and
overstate every quarter after the first.

### Step 5 — The historic floor: a team cannot create less than last year

**Definition from the model owner, 2026-08-10.** The floor exists so a territory
is not permitted to plan less pipe creation than it demonstrated a year earlier.

| | |
|---|---|
| **Basis** | Pipe **actually created** in the **same quarter of the prior year** — Q3 FY26's floor is Q3 FY25 actual creation. Same quarter, so seasonality is respected. |
| **Grain** | **Territory × Quarter.** The territory total must not fall; product mix may move freely to follow demand. |
| **Source** | `sku_nacv_fact` — created pipe by create date, the same source as sales cycle |

**The `Historic Floor` sheet is a cached artifact, not a source.** Recompute the
floor from history each cycle; do not read a sheet that ages.

**Note the grain change from the workbook.** The macro floors at
Territory × Product × Quarter (Phase A/B/C) and *then* raises to a territory floor
(Phase D). The intended rule is Territory × Quarter only, which is looser and
lets product mix shift.

#### Floors change the answer's meaning

A floor is an inequality constraint on an otherwise exact linear solve. When it
binds, the derived target is **above** what the bookings math requires — the
number stops being "what we need" and becomes "what we need, or last year's level,
whichever is higher."

**Report which one is binding.** A floor-driven target is high because the team
did more last year; a gap-driven target is high because the bookings number
demands it. Those are different conversations. Any implementation should emit a
`binding: floor | gap` flag per row and a total of how much of the target is
floor-driven.

Floors also interact with the coupling: a floor-raised quarter pushes a larger
sales cycle tail forward, which **reduces** the next quarter's gap. Solving in
chronological order handles this; solving independently does not.

### Chain summary

```
GIVEN (input)                    COMPUTED FROM SOURCE
bookings target                  sku_nacv_fact
  from finance/planning            ├─ sales cycle (create→close, QUARTER offsets)
  today: Target_Monthly.csv        │    → sales cycle weights Q0…Q+8
  Target_Type = 'Bookings'         ├─ win rates (In Q vs Pre Q)      
                                   └─ historical mix → splits
                                 trf_opp_daily_snapshot_new
                                   ├─ In Q slip: moved out DURING the quarter
                                   ├─ Pre Q slip: moved out BEFORE it opened
                                   └─ destinations: which quarter it landed in
        ↓
bookings target
  − closed won to date
  − (open pipe x (1 - Pre Q slip) + slip inflow) x (1 - In Q slip) x Pre Q win rate
  − sales cycle tail from earlier quarters IN THIS SOLVE
        ↓  goal seek
REQUIRED PIPE CREATE   ← the answer
        ↓  splits + renaming applied downstream
Target_Monthly.csv — a BY-PRODUCT, 63,171 rows × 12 dimensions
        ↓  sales cycle curve
supports this quarter (~10-14%) + all subsequent quarters (~86-90%)
        ↓
couples every quarter to every later quarter
```

**Read the direction carefully.** `Target_Monthly.csv` sits *below* the answer,
not above it. It is where a previous cycle's derived target was published after
splits and renaming — a comparison point at aggregate level, never an input and
never a join partner.

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

### Sales cycle weights — T–AB

`Q0 wt` … `Q+8 wt`. The share of pipe created in this quarter expected to close
in each subsequent quarter.

**16 distinct weight vectors** across the sheet — these are empirical sales cycle
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
| AC | `Q0 (close)` | `=$S*T` — **this row's** pipe create x its Q0 weight |
| AD–AK | `Q+1 (close)` … `Q+8 (close)` | **`=IF(AND($B{r-k}=$B{r},$C{r-k}=$C{r}), $S{r-k}*<wt_k>{r-k}, 0)`** — the k-th slice reaches **BACK k rows**, to the same Territory x Product k quarters earlier. **Not** this row's create spread forward. |
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

## The starting tail — RESOLVED

**Found 2026-08-11 while rebuilding this in Python, and settled the same day.**

Every quarter's `AP` is fed by `S` from up to **8 preceding quarters** of the same
Territory x Product. A rebuild that solves only Q3 and Q4 FY26 starts with an
empty tail, so Q3 receives nothing from Q1/Q2 FY26 and its whole target falls on
its own in-quarter create at ~7% yield.

`agent/waterfall.py` supplies that starting tail through
`expected_from_existing_pipe` instead. **The two are not the same population —
and for an in-flight run the snapshot view is the better one, because it is
observed rather than modelled:**

| | Workbook `AP` | `expected_from_existing_pipe` |
|---|---|---|
| Population | Pipe *created* in the 8 prior quarters | Pipe *currently open* with `CloseDate` in this quarter |
| Selected by | Sales cycle curve of the creating quarter | Snapshot `CloseDate` |
| Misses | — | Prior-quarter creates not yet dated into this quarter |

### RESOLVED 2026-08-11: the `$0` tail is correct for an in-flight quarter

**Settled with the model owner.** The two populations are not a defect — they are
the same pipe observed two different ways, and which one is right depends on when
you run:

- **Running inside or near the quarter** (the normal case). Pipe created in Q1 or
  Q2 that is destined to close in Q3 **already exists and is already dated into
  Q3**, so it is sitting in the snapshot inside `open_pipe_at()`. It does not
  need to be modelled — it needs to be *observed*, which is what
  `expected_from_existing_pipe` does. Adding a modelled tail from Q1/Q2 on top
  would **double-count it**. A `$0` tail for the first quarter of the solve is
  therefore the correct answer, not a missing term.
- **Annual planning, run before Q1.** Nothing is observable — the quarters have
  not happened and there is no snapshot to read. There the tail *must* be
  modelled from prior creates, and the solve must run from Q1 forward so each
  quarter's create feeds the next. **This is the regime the workbook was built
  for**, and it is why it reaches back 8 quarters.

The one thing the snapshot view genuinely misses is **pull-in** — a prior-quarter
create currently dated into a later quarter that pulls forward. Measured at
0.1%–0.3% of the base, so it is immaterial.

**The earlier `+3.2%` agreement remains meaningless.** It came from an inflated
yield offsetting an understated existing-pipe term, and two errors cancelling is
not evidence of correctness. But the tail is not the missing piece — see the
conversion-rate issue below.

---

### Measured, and DECIDED: the composition stays as it is

The existing-pipe term composes two separately measured rates:

```
expected = open_pipe x (1 - slip_rate) x Pre Q win rate
```

A completed quarter lets that composition be checked against what the pipe open
mid-quarter *actually* converted to. Anchored at day 41 of each quarter and
followed to quarter end:

| Quarter | Open pipe at day 41 | Won by quarter end | Actual | Slip | Composed | Ratio |
|---|---:|---:|---:|---:|---:|---:|
| Q3 FY25 | $68,432,014 | $10,148,360 | 14.8% | 64.1% | 5.5% | 2.7x |
| Q4 FY25 | $155,612,390 | $24,246,208 | 15.6% | 54.7% | 6.9% | 2.3x |
| Q1 FY26 | $67,958,448 | $8,065,031 | 11.9% | 56.2% | 6.7% | 1.8x |
| Q2 FY26 | $79,366,795 | $11,884,609 | 15.0% | 53.3% | 7.1% | 2.1x |

> **DECIDED 2026-08-11 by the Strategic Analytics lead: leave it. The win rate
> assumptions are stated and the model sticks to them.** This table is recorded
> as a measurement, NOT as a defect to fix. Do not propose replacing the
> composition with a directly measured conversion rate, and do not "correct" the
> Pre Q win rate to close this gap — the assumptions are the owner's to set.

Kept in the file because the size of the difference is worth knowing when reading
a derived total: the existing-pipe term is conservative by construction, so
required create is correspondingly high. That is a property of the stated
assumptions, not an error in applying them.

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
2. ~~Where do the sales cycle curves come from?~~ **Fully answered 2026-08-10:**
   from sales cycle computed on `[src].[sku_nacv_fact]` —
   `Stage_1_Start_Date_Corrected` → `Opp_Closed_Date`, bucketed by quarter offset.
   The per-product fitting question is resolved too: `sku_nacv_fact` is
   product-grain, which is exactly what yields Territory x Product curves.

2a. ~~Possible double-count between the sales cycle curve and slip.~~
   **ANSWERED 2026-08-11.** The populations are disjoint by construction and the
   code enforces it: `derive_targets()` multiplies the sales cycle curve by
   `create` and **never** by `existing`, and `slip_inflow()` forwards `existing`
   and **never** `create`. The curve does embed historical slip — a deal created
   in Q1 that slipped twice and closed in Q4 sits at `Q+3` — but that is the
   correct behaviour for *newly created* pipe, which is the only thing the curve
   is applied to. Slip is applied to the pre-existing open base, which the curve
   never touches. **This constraint is the one to preserve** if either mechanism
   is ever extended.
3. **What time window fits the curves?** Trailing how many quarters? A curve
   fitted on a period containing a pricing or packaging change would misstate
   sales cycle. Not established.
4. **Does the slip analysis use a pre-quarter buffer?** Still **unknown** for the
   Excel side. Ours anchors at the latest snapshot at or before the quarter start
   (or the equivalent point in flight), not against invariant 5's buffer — see
   the anchoring caveat on `slip()`. A remaining candidate for a reconciliation
   gap, though a smaller one than it looked: the anchoring *point* was shown to
   matter more than the buffer (58.4% at quarter start vs 63.9% at W7).
5. ~~How is the multi-quarter coupling solved?~~ **Answered 2026-08-10:**
   chronological forward substitution. The system is triangular because every link
   is linear, so it solves exactly without iteration. See Step 4.
5a. ~~What is the goal-seek mechanism?~~ **Answered 2026-08-10** from the
   workbook's VBA (`Module13`). Row-by-row Excel `GoalSeek` of `Pipe Won` to
   `Difference` by changing `Pipe Create`, wrapped in floor clamps. The relation
   is linear, so a closed form replaces it exactly. See Step 3.
6. **Are win rates fitted or set?** Their precision (`0.43393573125`) suggests
   computed, but the computation is not in this sheet. We recompute them from
   `sku_nacv_fact` per window rather than copying, since a stored rate is stale
   the moment the period moves. **What is settled: the stated assumptions are the
   owner's and are not to be revised to close a gap** — see the DECIDED block
   above. Report a discrepancy; do not act on it.
7. **What are AV/AW for?**
8. **Should floors ever be overridden?** The macro pins `New Logo` and
   `EMEA Corporate` and hard-locks Q4-2025. These are situational workarounds and
   are **not** to be reproduced — but whether *some* override mechanism is needed
   is unresolved.
9. ~~Should this be reimplemented in Python?~~ **Done, 2026-08-10/11.**
   `agent/waterfall.py::derive_targets()`. It can now answer "what does this
   target require?" as well as "did we hit it?". **Still open is reconciliation:**
   derived totals sit well above published, and the terms that would explain it
   have been ruled out one at a time — yield (fixed), the `$0` sales cycle tail
   (correct as-is), Pre Q slip and slip inflow (built, and they roughly cancel),
   unmapped pipe (inactive teams, correctly excluded). The largest *measured*
   contributor is the conservatism of the existing-pipe composition, which is a
   stated assumption and stays. Treat the gap as unexplained rather than
   attributing it to any of the settled items.
