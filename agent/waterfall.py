"""Pipe Create target derivation — the waterfall, computed from source.

Implements docs/analysis/pipe-create-waterfall.md. Nothing here is a stored
constant: every assumption is a function of a window, so "win rates for Q1 and Q2
2026" is an argument rather than a lookup.

DIRECTION OF FLOW (the thing that is easy to get backwards):
    bookings target (GIVEN)
      - expected bookings from existing pipe (slip- and win-rate-adjusted)
      = gap
      -> goal seek -> REQUIRED PIPE CREATE
      -> splits + renaming applied downstream -> Target_Monthly.csv
`Target_Monthly.csv` is a BY-PRODUCT. Compare to it at aggregate level; never join
to it on product name, and never rename either side to force agreement.

THE SOLVE IS LINEAR. From the workbook's own formulas:
    Pipe Won = S x (Q0_wt x in_q_rate + sum(Q+1..Q+8 wts) x pre_q_rate)
so the required create is a division, not an iteration. The Excel macro
(Module13) iterates only because GoalSeek is a generic 1-D solver — and it wraps
the call in `On Error Resume Next`, so a non-converging row silently keeps its old
value. A closed form cannot fail that way.

QUARTERS ARE COUPLED. Pipe created in Q_n matures into Q_n+1's bookings, so
quarters are solved in chronological order with the sales cycle tail carried
forward. Every link is linear, so the system is triangular and forward
substitution is exact.

Requires cached parquet from a pull: data/sku_nacv.parquet (with CreateDate) and
data/bts.parquet. Slip additionally needs data/snapshot.parquet.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field

import pandas as pd

from pipeline import config

# Stage values emitted by SKU_SQL's CASE. 'Closed' means Closed Lost/Deferred.
WON, LOST, OPEN = "Closed Won", "Closed", "Open"

# Raw_Stage -> outcome, taken verbatim from the model owner's `Slip Assumption`
# notebook (2026-08-11). This is the authoritative mapping; do NOT replace it with
# a substring rule. "Stage 4 - Closed Pending" is the case that proves the point —
# it contains "Closed" but is an OPEN stage, and a `contains("Closed")` test
# silently books it as lost.
WON_STAGES = frozenset({"6 - Closed/Pending", "Closed Won", "Stage 5 - Closed Won"})
LOST_STAGES = frozenset({"Closed Deferred", "Closed Lost"})
# Neither open nor decided — excluded from the population entirely. pipeline's
# EXCLUDED_STAGES already filters these at pull time; listed here so the rule
# survives a change to the pull.
OTHER_STAGES = frozenset({"Closed - Duplicate", "Stage 6 - Closed - Admin",
                          "Stage 7 - Churned", "Opportunity Rejected",
                          "0 - First Interaction"})
OPEN_STAGES = frozenset({
    "0. Meeting Set", "1 - Discovery", "2 - Qualification Status",
    "3 - Executive Presentation", "4 - Technical Evaluation",
    "5 - Negotiation / Business Procurement", "Stage 1 - Actively Seeking DIscussion",
    "Stage 2 - In Discussion", "Stage 3 - Expected", "Stage 4 - Closed Pending",
})
MAX_OFFSET = 8  # Q0..Q+8, matching the workbook's weight columns

GRAIN_COLS = {
    "Territory": "BTS_Territory",
    "Region": "BTS_Region",
    "Geo": "BTS_Geo",
    "All": None,
}


class MissingData(RuntimeError):
    """A required parquet has not been pulled."""


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=8)
def _read_parquet_cached(path: str, mtime: float, size: int) -> pd.DataFrame:
    """Read a parquet once per (path, mtime, size).

    snapshot_hist.parquet is 74 MB / 19.9M rows, and a single derivation calls
    slip(), pre_q_slip(), slip_destinations() and slip_inflow() across two
    quarters — a dozen reads of the same file, which is what made a derivation
    take minutes rather than seconds.

    Keyed on mtime and size, not just the name: tests monkeypatch config.DATA to
    a fresh tmp_path per test and reuse filenames like "s.parquet", so a
    name-only key would serve one test's fixture to another.
    """
    return pd.read_parquet(path)


def _require(name: str) -> pd.DataFrame:
    path = config.DATA / name
    if not path.exists():
        raise MissingData(
            f"{path} not found. Pull it first (run_pull), which needs VPN and a live "
            f"`az login`. Nothing here can be computed from the targets CSV alone."
        )
    st = path.stat()
    # Copy: callers mutate (assign columns, sort). Handing out the cached frame
    # would let one caller corrupt the next one's input.
    return _read_parquet_cached(str(path), st.st_mtime, st.st_size).copy()


def load_sku(grain: str = "Territory") -> pd.DataFrame:
    """sku_nacv joined to the territory mapping, with quarter labels derived.

    Invariant 5 in spirit: geo/region/territory come from the BTS mapping, never a
    CASE statement.
    """
    sku = _require("sku_nacv.parquet")
    if "CreateDate" not in sku.columns:
        raise MissingData(
            "sku_nacv.parquet has no CreateDate column — it predates the query change. "
            "Re-pull with force=true; sales cycle and floors are uncomputable without it."
        )
    bts = _require("bts.parquet")

    sku = sku.copy()
    sku["_key"] = sku["Booking_Team_Static"].astype(str).str.strip().str.lower()
    b = bts.copy()
    b["_key"] = b["Bookings_Team_Static"].astype(str).str.strip().str.lower()
    b = b[["_key", "BTS_Geo", "BTS_Region", "BTS_Territory"]].drop_duplicates("_key")

    sku = sku.merge(b, on="_key", how="left")
    for c in ("BTS_Geo", "BTS_Region", "BTS_Territory"):
        sku[c] = sku[c].fillna("Unassigned")

    sku["CreateDate"] = pd.to_datetime(sku["CreateDate"], errors="coerce")
    sku["CloseDate"] = pd.to_datetime(sku["CloseDate"], errors="coerce")
    sku["create_q"] = _qindex(sku["CreateDate"])
    sku["close_q"] = _qindex(sku["CloseDate"])
    sku["value"] = pd.to_numeric(sku["Product_NACV"], errors="coerce").fillna(0.0)
    return sku


def _qindex(s: pd.Series) -> pd.Series:
    """Absolute quarter index: year*4 + (quarter-1). Differences give offsets."""
    return (s.dt.year * 4 + (s.dt.month - 1) // 3).astype("Int64")


def quarter_index(quarter_start) -> int:
    t = pd.Timestamp(quarter_start)
    return t.year * 4 + (t.month - 1) // 3


def _window_mask(dates: pd.Series, window) -> pd.Series:
    if window is None:
        return pd.Series(True, index=dates.index)
    lo, hi = window
    return (dates >= pd.Timestamp(lo)) & (dates <= pd.Timestamp(hi))


def _grain_key(df: pd.DataFrame, grain: str):
    col = GRAIN_COLS.get(grain)
    if grain not in GRAIN_COLS:
        raise ValueError(f"grain must be one of {list(GRAIN_COLS)}, got {grain!r}")
    return pd.Series("All", index=df.index) if col is None else df[col]


# --------------------------------------------------------------------------
# Step 1 — sales cycle -> sales cycle curve
# --------------------------------------------------------------------------

def sales_cycle(sku: pd.DataFrame, window=None, grain="Territory") -> pd.DataFrame:
    """Closed dollars bucketed by create->close QUARTER OFFSET, per grain key.

    Not an average duration — a distribution. A mean cycle length cannot allocate
    dollars across future quarters; this can.

    `window` filters on CREATE date: it selects the creation cohort whose
    behaviour is being measured.
    """
    d = sku[sku["Stage"].isin([WON, LOST])].copy()
    d = d[_window_mask(d["CreateDate"], window)]
    d = d[d["create_q"].notna() & d["close_q"].notna()]

    d["offset"] = (d["close_q"] - d["create_q"]).astype(int)
    # Negative offsets are data errors (closed before created); >MAX_OFFSET is a
    # long tail the workbook does not model. Both are dropped, and the count is
    # reported so the loss is visible rather than silent.
    keep = d["offset"].between(0, MAX_OFFSET)
    dropped = int((~keep).sum())
    d = d[keep]
    d["_g"] = _grain_key(d, grain)

    out = (
        d.groupby(["_g", "offset"])["value"].sum()
        .unstack(fill_value=0.0)
        .reindex(columns=range(MAX_OFFSET + 1), fill_value=0.0)
    )
    out.index.name = grain
    out.attrs["dropped_rows"] = dropped
    out.attrs["window"] = window
    return out


def sales_cycle_weights(sku: pd.DataFrame, window=None, grain="Territory") -> pd.DataFrame:
    """Sales cycle normalised to shares summing to 1.0 per grain key.

    The workbook's stored vectors sum to 0.98-1.00 because they were rounded to two
    decimals, quietly losing 1-2% of created pipe. Normalising exactly avoids that.
    """
    raw = sales_cycle(sku, window, grain)
    totals = raw.sum(axis=1)
    curve = raw.div(totals.where(totals > 0), axis=0)
    curve = curve[totals > 0]
    curve.attrs.update(raw.attrs)
    curve.attrs["closed_value"] = totals
    return curve


# --------------------------------------------------------------------------
# win rates
# --------------------------------------------------------------------------

def win_rates(sku: pd.DataFrame, window=None, grain="Territory") -> pd.DataFrame:
    """Win rate per grain key, split In Q vs Pre Q.

    in_quarter — deals that closed in the SAME quarter they were created
    pre_q      — deals that closed in a subsequent quarter, i.e. pipe that
                 existed BEFORE the quarter it books in

    The names are the model owner's and are not negotiable: these are the
    In Q and Pre Q win rates as the business states them. "later" was an
    internal coinage for the same thing and has been retired — it invited the
    reading that it describes the deal's timing rather than the pipe's origin.

    Rate is won value / decided value (won + lost). Open deals are excluded: they
    have not decided, and counting them as losses understates the rate.
    """
    d = sku[sku["Stage"].isin([WON, LOST])].copy()
    d = d[_window_mask(d["CreateDate"], window)]
    d = d[d["create_q"].notna() & d["close_q"].notna()]
    d["offset"] = (d["close_q"] - d["create_q"]).astype(int)
    d = d[d["offset"].between(0, MAX_OFFSET)]
    d["_g"] = _grain_key(d, grain)
    d["_bucket"] = d["offset"].eq(0).map({True: "in_quarter", False: "pre_q"})
    d["_won"] = d["value"].where(d["Stage"].eq(WON), 0.0)

    g = d.groupby(["_g", "_bucket"])[["_won", "value"]].sum()
    rate = (g["_won"] / g["value"].where(g["value"] > 0)).unstack()
    rate = rate.reindex(columns=["in_quarter", "pre_q"])
    decided = g["value"].unstack().reindex(columns=["in_quarter", "pre_q"]).fillna(0.0)
    rate.attrs["decided_value"] = decided
    rate.attrs["window"] = window
    rate.index.name = grain
    return rate


# --------------------------------------------------------------------------
# Step 2b — splits
# --------------------------------------------------------------------------

def splits(sku: pd.DataFrame, window=None, dims=("Segment", "Source", "Deal_Type", "Product"),
           grain="Territory") -> dict[str, pd.DataFrame]:
    """Historical mix per dimension, as shares summing to 1.0 within each grain key.

    Splits are a derivation input, not decoration: product mix determines ASP, and
    ASP determines the opp-count target.
    """
    d = sku[_window_mask(sku["CreateDate"], window)].copy()
    d["_g"] = _grain_key(d, grain)
    out = {}
    for dim in dims:
        if dim not in d.columns:
            continue
        g = d.groupby(["_g", dim])["value"].sum().unstack(fill_value=0.0)
        tot = g.sum(axis=1)
        out[dim] = g.div(tot.where(tot > 0), axis=0)
    return out


# --------------------------------------------------------------------------
# Step 5 — historic floor
# --------------------------------------------------------------------------

def historic_floor(sku: pd.DataFrame, quarter_start, grain="Territory") -> pd.Series:
    """Pipe actually CREATED in the same quarter of the prior year, per grain key.

    A territory may not plan less creation than it demonstrated a year earlier.
    Same quarter, so seasonality is respected. Grain is Territory x Quarter by
    decision (2026-08-10) — looser than the workbook's per-product floor, so
    product mix can follow demand while the territory total cannot fall.

    The workbook's `Historic Floor` sheet is a cached artifact of this computation.
    Recompute; do not read a sheet that ages.
    """
    qi = quarter_index(quarter_start) - 4  # same quarter, prior year
    d = sku[sku["create_q"] == qi].copy()
    d["_g"] = _grain_key(d, grain)
    s = d.groupby("_g")["value"].sum()
    s.name = "floor"
    s.attrs["prior_quarter_index"] = qi
    return s


# --------------------------------------------------------------------------
# Step 3/4 — the solve
# --------------------------------------------------------------------------

def yield_per_dollar(curve_row: pd.Series, in_q_rate: float, pre_q_rate: float) -> float:
    """Bookings produced IN THE CREATING QUARTER per dollar of pipe created.

        Q0_wt x in_quarter_rate

    The later weights are deliberately absent. Verified against the workbook
    2026-08-11: on the `Pipeline Waterfall (Quarterly)` sheet only the Q0 slice
    depends on this row's Pipe Create —

        AC (Q0 close)  = $S*T
        AD (Q+1 close) = IF(AND($B{r-1}=$B{r},$C{r-1}=$C{r}), $S{r-1}*U{r-1}, 0)

    — so AD:AK are the tail ARRIVING from earlier quarters of the same
    Territory x Product, not this row's create spread forward. Since
    AO = AC x AM and AP = SUM(AD:AK) x AN, the goal seek's changing cell S moves
    only AO, giving the closed form

        S* = (Difference - AP) / (Q0_wt x in_quarter_rate)

    `pre_q_rate` stays in the signature because the caller applies it when
    propagating this quarter's tail forward. Counting it here as well would book
    the same dollars twice — once as this quarter's bookings and again as a later
    quarter's reduced gap. Doing so inflates yield ~3x on this data and
    understates required create by the same factor.
    """
    q0 = float(curve_row.get(0, 0.0))
    return q0 * float(in_q_rate or 0.0)


@dataclass
class DerivedQuarter:
    quarter: str
    quarter_start: str
    rows: pd.DataFrame
    assumptions: dict = field(default_factory=dict)


def _for_quarter(value, quarter_start: str) -> pd.Series | None:
    """Resolve a per-quarter input to the Series for one quarter.

    A bare Series means "the same for every quarter". A mapping keyed by quarter
    start means each quarter has its own. Bookings targets and open pipe are both
    quarter-specific facts, so a signature that only accepted one Series forced
    callers to pick a quarter and silently apply it to the rest.
    """
    if value is None:
        return None
    if isinstance(value, pd.Series):
        return value
    try:
        return value[quarter_start]
    except KeyError:
        raise KeyError(
            f"No entry for quarter {quarter_start!r}. A mapping input must cover every "
            f"quarter being solved; got keys {sorted(value)!r}."
        ) from None


def derive_targets(
    sku: pd.DataFrame,
    bookings_target,
    quarter_starts: list[str],
    grain: str = "Territory",
    window=None,
    existing_pipe_bookings=None,
    closed_won=None,
    overrides=None,
) -> pd.DataFrame:
    """Solve required pipe create per grain key, per quarter, in chronological order.

    `bookings_target` — the GIVEN input, indexed by grain key. Today it comes from
    Target_Monthly.csv's Bookings rows, but it is a parameter because it will be
    supplied directly. Either a Series (the same target for every quarter) or a
    mapping of quarter start -> Series. Q3 and Q4 carry genuinely different
    bookings targets, so the mapping form is the correct one for a multi-quarter
    solve; a single Series applied to both understates the larger quarter.

    `existing_pipe_bookings` — expected bookings from pipe already open, slip- and
    pre-Q-win-rate-adjusted. Same two forms as `bookings_target`. Omitted means
    zero, which OVERSTATES the required create; the result flags this so the number
    is never mistaken for complete.

    `closed_won` — bookings already banked in the quarter. Same two forms. Matters
    most for an in-flight quarter: half of Q3's bookings are already won by W7, and
    omitting them asks pipe create to cover ground already taken.

    The solve per key is:

        gap      = bookings_target - closed_won - expected_from_existing_pipe - tail
        required = gap / yield_per_dollar_of_new_pipe

    so each term is bookings already accounted for, and only the residue is asked
    of newly created pipe.

    Quarters are solved in order and each quarter's sales cycle tail is carried
    forward into later quarters, reducing their gap. Solving independently would
    overstate every quarter after the first.
    """
    quarter_starts = sorted(quarter_starts, key=lambda q: pd.Timestamp(q))
    curve = sales_cycle_weights(sku, window, grain)
    rates = win_rates(sku, window, grain)

    # Keys must span every quarter's targets — a territory carrying a target in Q4
    # but not Q3 still has to be solved.
    target_keys: set = set()
    for _qs in quarter_starts:
        target_keys |= set(_for_quarter(bookings_target, _qs).index)
    keys = sorted(set(curve.index) & target_keys)
    carried: dict[tuple[str, int], float] = {}  # (key, quarter_index) -> bookings already covered
    out = []

    for qs in quarter_starts:
        qi = quarter_index(qs)
        floor = historic_floor(sku, qs, grain)
        q_target = _for_quarter(bookings_target, qs)
        q_existing = _for_quarter(existing_pipe_bookings, qs)
        q_won = _for_quarter(closed_won, qs)

        for key in keys:
            crow = curve.loc[key]
            in_q = rates.loc[key, "in_quarter"] if key in rates.index else None
            pre_q = rates.loc[key, "pre_q"] if key in rates.index else None
            q0 = float(crow.get(0, 0.0))

            # An override replaces a measured assumption and then flows through
            # everything downstream — yield, gap, floor comparison, and the tail
            # this quarter pushes into the next. Substituting only in the headline
            # would give an answer that no longer reconciles with its own quarters.
            ov = _override_for(overrides, qs, key)
            in_q = ov.get("in_quarter_win_rate", in_q)
            pre_q = ov.get("pre_q_win_rate", pre_q)
            q0 = ov.get("q0_weight", q0)

            yld = q0 * float(in_q or 0.0)

            target = float(q_target.get(key, 0.0))
            # No `or {}` fallback: these are Series, and `or` would call bool() on
            # them. The `is not None` guard is the whole check.
            existing = float(q_existing.get(key, 0.0)) if q_existing is not None else 0.0
            won = float(q_won.get(key, 0.0)) if q_won is not None else 0.0
            existing = ov.get("expected_from_existing_pipe", existing)
            tail = carried.get((key, qi), 0.0)

            gap = target - won - existing - tail
            required = (gap / yld) if (yld > 0 and gap > 0) else 0.0

            fl = ov.get("historic_floor", float(floor.get(key, 0.0)))
            binding = "floor" if fl > required else ("gap" if required > 0 else "none")
            create = max(required, fl)

            # Propagate this quarter's sales cycle tail into later quarters.
            for off in range(1, MAX_OFFSET + 1):
                w = float(crow.get(off, 0.0))
                if w:
                    carried[(key, qi + off)] = carried.get((key, qi + off), 0.0) + create * w * float(pre_q or 0.0)

            out.append({
                "quarter": config.fq_label(qs),
                "quarter_start": qs,
                grain: key,
                "bookings_target": target,
                "closed_won": won,
                "expected_from_existing_pipe": existing,
                "sales_cycle_tail_from_earlier_quarters": tail,
                "gap": gap,
                "yield_per_dollar": yld,
                "required_by_gap": required,
                "historic_floor": fl,
                "pipe_create_target": create,
                "binding": binding,
                "in_quarter_win_rate": in_q,
                "pre_q_win_rate": pre_q,
                "q0_weight": q0,
                # A what-if row must never be mistaken for a measured one.
                "overridden": ",".join(sorted(ov)) if ov else "",
            })

    df = pd.DataFrame(out)
    df.attrs["existing_pipe_supplied"] = existing_pipe_bookings is not None
    df.attrs["window"] = window
    df.attrs["grain"] = grain
    return df


ASSUMPTIONS = ("in_quarter_win_rate", "pre_q_win_rate", "q0_weight",
               "expected_from_existing_pipe", "historic_floor",
               # Slip terms. These are consumed EARLIER than the others — in
               # existing_pipe_bookings(), before derive_targets() runs — because
               # they shape the existing-pipe input rather than the solve. See
               # SLIP_ASSUMPTIONS below and its use in agent/tools.py.
               "in_q_slip_rate", "pre_q_slip_rate", "slip_inflow")

# The subset applied before the solve, not inside it. Kept as its own tuple so a
# caller can ask "is this override mine to apply?" instead of hardcoding names.
SLIP_ASSUMPTIONS = ("in_q_slip_rate", "pre_q_slip_rate", "slip_inflow")


def _override_for(overrides, quarter_start, key) -> dict:
    """Resolve overrides for one cell.

    Accepts {key: {...}} to apply across every quarter, or
    {quarter_start: {key: {...}}} to target one. A key that looks like a date is
    read as a quarter; anything else is a grain key.
    """
    if not overrides:
        return {}
    out = {}
    for k, v in overrides.items():
        if not isinstance(v, dict):
            continue
        if k == quarter_start:                       # quarter-scoped block
            out.update(v.get(key, {}) if isinstance(v.get(key), dict) else {})
        elif k == key:                               # key-scoped, all quarters
            out.update(v)
    return {a: float(b) for a, b in out.items() if a in ASSUMPTIONS}


def flag_outliers(df: pd.DataFrame, grain: str = "Territory") -> pd.DataFrame:
    """Mark rows whose target is driven by a questionable assumption.

    A target can be large for a good reason (the bookings number demands it) or
    because an input is broken or extreme. Those look identical in a total, so
    they are separated here and surfaced per row.

    Each flag names the DRIVER, because the point is to let a reader challenge a
    specific assumption rather than distrust the whole number.

    Columns are `outlier_flags` / `outlier_reasons`, not `flags`: pandas already
    owns `DataFrame.flags`, so a column of that name is unreachable by attribute
    access and silently returns a pandas Flags object instead of the data.
    """
    df = df.copy()
    flags, reasons = [], []

    for q, g in df.groupby("quarter", sort=False):
        med_in_q = g.loc[g["in_quarter_win_rate"] > 0, "in_quarter_win_rate"].median()
        med_yield = g.loc[g["yield_per_dollar"] > 0, "yield_per_dollar"].median()
        med_target = g.loc[g["pipe_create_target"] > 0, "pipe_create_target"].median()

        for _, r in g.iterrows():
            f, why = [], []

            # No existing pipe at all, yet a target to hit — nothing is working for
            # this team, so the whole bookings number falls on new creation.
            if r["bookings_target"] > 0 and r["expected_from_existing_pipe"] <= 0:
                f.append("no_existing_pipe")
                why.append("No open pipe is expected to convert, so the entire bookings "
                           "target falls on newly created pipe.")

            # A low in-quarter win rate divides a small number into the gap, which
            # inflates required create without anyone asking for more pipe.
            if med_in_q and r["in_quarter_win_rate"] > 0 and r["in_quarter_win_rate"] < 0.5 * med_in_q:
                f.append("low_in_q_win_rate")
                why.append(f"In-quarter win rate {r['in_quarter_win_rate']:.1%} is well below "
                           f"the {grain} median {med_in_q:.1%}. It is the divisor, so a low "
                           f"rate raises this target.")

            if med_yield and r["yield_per_dollar"] > 0 and r["yield_per_dollar"] < 0.5 * med_yield:
                f.append("low_yield")
                why.append(f"Yield {r['yield_per_dollar']:.4f} per dollar created is below "
                           f"half the median {med_yield:.4f}, so every dollar of gap needs "
                           f"more pipe here than elsewhere.")

            if r["q0_weight"] <= 0:
                f.append("no_q0_weight")
                why.append("Nothing created here has ever closed in its own quarter, so the "
                           "in-quarter yield is zero and the gap cannot be solved.")

            if r["bookings_target"] <= 0 and r["pipe_create_target"] > 0:
                f.append("target_without_bookings")
                why.append("No bookings target, so this figure is the historic floor alone.")

            if r["historic_floor"] <= 0:
                f.append("no_floor")
                why.append("No prior-year creation to floor against.")

            if med_target and r["pipe_create_target"] > 5 * med_target:
                f.append("outsized_target")
                why.append(f"Target is more than 5x the {grain} median.")

            flags.append(",".join(f))
            reasons.append(" ".join(why))

    df["outlier_flags"] = flags
    df["outlier_reasons"] = reasons
    return df


def summarize(df: pd.DataFrame) -> dict:
    """Headline figures, including how much of the target is floor-driven."""
    by_q = df.groupby("quarter")["pipe_create_target"].sum()
    floor_driven = df[df["binding"] == "floor"]["pipe_create_target"].sum()
    total = float(df["pipe_create_target"].sum())
    return {
        "by_quarter": {k: float(v) for k, v in by_q.items()},
        "total": total,
        "floor_driven": float(floor_driven),
        "floor_driven_pct": (float(floor_driven) / total) if total else None,
        "rows_floor_bound": int((df["binding"] == "floor").sum()),
        "rows_gap_bound": int((df["binding"] == "gap").sum()),
        "existing_pipe_supplied": bool(df.attrs.get("existing_pipe_supplied")),
    }


# --------------------------------------------------------------------------
# Step 2 — slip
# --------------------------------------------------------------------------

def prior_year_quarter(quarter_start) -> str:
    """The same quarter one year earlier.

    Slip has a quarter-of-year shape — end-of-year pushes, budget cycles, holiday
    weeks — so the assumption for Q3 comes from Q3, not from whichever quarter
    happens to be most recent. Same reasoning as the historic floor, which also
    looks back exactly four quarters.
    """
    return (pd.Timestamp(quarter_start) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")


def slip_anchor(quarter_start, as_of, prior_quarter_start) -> pd.Timestamp:
    """Where to read the starting population in the historic quarter.

    Mid-quarter, the equivalent point-in-time. For a quarter that has not started
    yet there is no elapsed fraction to mirror, so the whole historic quarter is
    the like-for-like window.
    """
    if pd.Timestamp(as_of) <= pd.Timestamp(quarter_start):
        return pd.Timestamp(prior_quarter_start)
    return equivalent_point(quarter_start, as_of, prior_quarter_start)


def equivalent_point(quarter_start, as_of, prior_quarter_start) -> pd.Timestamp:
    """The same distance into `prior_quarter_start` as `as_of` is into `quarter_start`.

    Mid-quarter, slip must be measured from where we actually stand, not from the
    quarter's start: less of the quarter remains, so less can still slip. Comparing
    W7-to-quarter-end last year against W7-to-quarter-end this year is like for
    like; comparing a full quarter against a half quarter overstates slip.
    """
    offset = pd.Timestamp(as_of) - pd.Timestamp(quarter_start)
    return pd.Timestamp(prior_quarter_start) + offset


def _partition(j: pd.DataFrame, q_end) -> pd.Series:
    """won / lost / slipped / held, from an end stage and an end CloseDate.

    The single implementation of the outcome rule. classify_outcomes() (one anchor
    date for everybody) and slip_by_cohort() (a per-opp anchor, because in-quarter
    creates do not exist at the quarter start) both call this rather than keeping
    their own copy — the two would drift, exactly as the duplicated slip assembly
    in tools.py did.

    Expects `end_stage`, `end_close`, and optionally `end_mapped_stage`.
    """
    if "end_mapped_stage" in j.columns:
        # SQL already applied the CASE (snapshots pulled after 2026-08-11). One
        # mapping, shared with the SKU query, so the two cannot diverge.
        m = j["end_mapped_stage"].astype(str)
        won, closed = m.eq(WON), m.eq(LOST)
    else:
        # Cached parquet predating the Stage column — same CASE, in Python.
        st = j["end_stage"].astype(str)
        won = st.isin(WON_STAGES)
        closed = st.isin(LOST_STAGES)

        # ELSE 'Open' — exactly as the SQL CASE. An unrecognised stage is Open,
        # not re-derived from a substring: a substring rule is what mis-booked
        # "Stage 4 - Closed Pending" as a loss. Unmapped values are RECORDED so a
        # new stage is visible, but they do not change the classification.
        known = won | closed | st.isin(OTHER_STAGES) | st.isin(OPEN_STAGES)
        if (~known).any():
            j.attrs["unmapped_stages"] = sorted(st[~known].unique())

    moved = (~won) & (~closed) & (j["end_close"] > pd.Timestamp(q_end))

    out = pd.Series("held", index=j.index)
    out[won] = "won"
    out[closed] = "lost"
    out[moved] = "slipped"
    return out


def classify_outcomes(snap: pd.DataFrame, q_start, q_end, anchor) -> pd.DataFrame:
    """Partition the pipe open at `anchor` into won / lost / slipped / held.

    One opp per row, carrying its anchor value, its end stage and its end
    CloseDate — so a caller can aggregate a rate, follow where the slipped ones
    landed, or trace a single opp, all off the same classification.

    `held` is the residual: still open, but its CloseDate did NOT move past the
    quarter end. Slip is specifically the ones that MOVED. The won/lost exclusion
    matters — a won deal whose close date shifted would otherwise count as slip
    and badly overstate it.

    Shared by slip() and slip_destinations() on purpose. A second copy of this
    classification would drift, exactly as the duplicated slip assembly in
    tools.py did.
    """
    def state_at(cutoff):
        d = snap[snap["snapshot_date"] <= cutoff]
        return d.sort_values("snapshot_date").groupby("Opp_Id").last()

    start, end = state_at(anchor), state_at(q_end)

    open_start = start[
        start["Raw_Stage"].notna()
        & ~start["Raw_Stage"].astype(str).str.contains("Closed", case=False, na=False)
        & start["CloseDate"].between(q_start, q_end)
    ]

    cols = [c for c in ("value", "Bookings_Team_static", "CloseDate") if c in open_start.columns]
    end_cols = {"Raw_Stage": "end_stage", "CloseDate": "end_close"}
    if "Stage" in end.columns:                       # SQL-mapped, preferred
        end_cols["Stage"] = "end_mapped_stage"
    j = open_start[cols].join(
        end[list(end_cols)].rename(columns=end_cols), how="left")

    j["outcome"] = _partition(j, q_end)
    return j


def slip_destinations(quarter_start, from_point=None,
                      snapshot_file="snapshot_hist.parquet") -> pd.Series:
    """Of the pipe that slipped OUT of this quarter, which quarter did it land in.

    Returned as shares by quarter offset (1 = the next quarter), summing to 1.0,
    with the dollar total on `.attrs`.

    This is the half of slip the model does not have. `slip()` says how much
    left; this says where it went — and slipped pipe becomes the destination
    quarter's open pipe, which is the workbook's `In Q Inflow` / `Pre Q Inflow`.

    MEASUREMENT ONLY. Nothing in derive_targets() calls this, and no target
    moves because of it.
    """
    snap = _require(snapshot_file)
    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))
    anchor = pd.Timestamp(from_point) if from_point is not None else q_start

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    j = classify_outcomes(s, q_start, q_end, anchor)
    sl = j[j["outcome"] == "slipped"].copy()
    if sl.empty:
        out = pd.Series(dtype=float, name="share")
        out.attrs["slipped_value"] = 0.0
        return out

    base = quarter_index(quarter_start)
    # Offset by the quarter the new CloseDate falls in, not by days: the model
    # thinks in quarter offsets everywhere else.
    sl["offset"] = sl["end_close"].apply(
        lambda d: quarter_index(d.normalize().replace(day=1)) if pd.notna(d) else None) - base

    dollars = sl.groupby("offset")["value"].sum().sort_index()
    out = (dollars / dollars.sum()).rename("share")
    out.index.name = "quarter_offset"
    out.attrs["slipped_value"] = float(dollars.sum())
    out.attrs["dollars"] = dollars
    out.attrs["opps"] = int(len(sl))
    out.attrs["quarter"] = config.fq_label(quarter_start)
    out.attrs["from_point"] = str(anchor.date())
    return out


COHORTS = ("in_q", "pre_q", "pre_q_reslip")


def slip_by_cohort(quarter_start, snapshot_file="snapshot_hist.parquet",
                   by_create_month=False) -> pd.DataFrame:
    """Slip split by WHEN the pipe was created, testing the Pre-Q / In-Q assumption.

    The bookings-forecast notebook carries two push rates, PRE_Q and IN_Q, on the
    premise that pipe created inside the quarter behaves differently from pipe
    carried into it. This measures whether it does, on three cohorts:

        in_q          created inside the quarter, dated to close inside it
        pre_q         created before the quarter, first seen dated into it
        pre_q_reslip  created before the quarter AND already carrying a CloseDate
                      from an earlier quarter — i.e. it has slipped at least once

    Anchoring differs from slip() and it has to. slip() reads one population at
    one date; an in-quarter create does not exist at the quarter start, so each
    opp is anchored at its OWN first in-quarter observation. Both paths share
    _partition() for the outcome rule.

    `by_create_month` splits in_q by month-of-creation within the quarter, which
    is the exposure control: an opp created in month 2 has less of the quarter in
    which to slip, so a raw in_q rate is not directly comparable to pre_q.

    MEASUREMENT ONLY. derive_targets() does not call this and no target moves.

    Create dates come from sku_nacv (the only source that carries one — the
    snapshot table has no CreateDate), falling back to first-appearance in the
    snapshot feed. That fallback is left-censored at the feed's start, so it is
    trusted only strictly after it; opps with neither are excluded and reported
    on `.attrs["unknown_create"]`.
    """
    snap = _require(snapshot_file)
    sku = _require("sku_nacv.parquet")
    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)
    s = s.sort_values(["Opp_Id", "snapshot_date"])

    window_open = s["snapshot_date"].min()
    first_seen = s.groupby("Opp_Id")["snapshot_date"].min()
    # The earliest CloseDate the feed ever saw for this opp. If it sits in a
    # quarter before the one being measured, the opp has already moved once.
    first_close = s.groupby("Opp_Id")["CloseDate"].first()

    created = pd.to_datetime(sku["CreateDate"], errors="coerce").groupby(
        sku["Opportunity_Id"]).min()

    inq = s[s["snapshot_date"].between(q_start, q_end)]
    if inq.empty:
        raise MissingData(
            f"no snapshots inside {config.fq_label(quarter_start)} "
            f"({q_start:%Y-%m-%d}..{q_end:%Y-%m-%d}). Add it to "
            f"config.HIST_SNAP_WINDOWS and re-pull.")

    entry, exit_ = inq.groupby("Opp_Id").first(), inq.groupby("Opp_Id").last()
    st = entry["Raw_Stage"].astype(str)
    open_at_entry = ~(st.isin(WON_STAGES) | st.isin(LOST_STAGES) | st.isin(OTHER_STAGES))
    elig = entry[open_at_entry & entry["CloseDate"].between(q_start, q_end)]

    create = created.reindex(elig.index)
    seen = first_seen.reindex(elig.index)
    create = create.fillna(seen.where(seen > window_open))

    j = pd.DataFrame({
        "value": elig["value"],
        "create": create,
        "end_stage": exit_["Raw_Stage"].reindex(elig.index),
        "end_close": exit_["CloseDate"].reindex(elig.index),
    })
    if "Stage" in exit_.columns:
        j["end_mapped_stage"] = exit_["Stage"].reindex(elig.index)
    j["outcome"] = _partition(j, q_end)

    reslip = first_close.reindex(elig.index) < q_start
    born_in_q = j["create"].between(q_start, q_end)
    j["cohort"] = "pre_q"
    j.loc[reslip, "cohort"] = "pre_q_reslip"
    j.loc[born_in_q, "cohort"] = "in_q"
    unknown = j["create"].isna() & ~reslip
    j = j[~unknown]

    keys = ["cohort"]
    if by_create_month:
        j["create_month"] = (j["create"].dt.year * 12 + j["create"].dt.month) - (
            q_start.year * 12 + q_start.month)
        keys.append("create_month")

    g = j.groupby(keys + ["outcome"])["value"].sum().unstack(fill_value=0.0)
    for c in ("won", "lost", "slipped", "held"):
        if c not in g.columns:
            g[c] = 0.0
    g["starting_open_pipe"] = g[["won", "lost", "slipped", "held"]].sum(axis=1)
    g["opps"] = j.groupby(keys).size()
    g["avg_deal"] = g["starting_open_pipe"] / g["opps"]
    base = g["starting_open_pipe"].where(g["starting_open_pipe"] > 0)
    for c in ("slipped", "won", "lost", "held"):
        g[f"{c}_rate" if c != "slipped" else "slip_rate"] = g[c] / base

    g.attrs["quarter"] = config.fq_label(quarter_start)
    g.attrs["unknown_create"] = int(unknown.sum())
    g.attrs["create_from_sku"] = int(created.reindex(elig.index).notna().sum())
    # A prior slip is only detectable if the feed opened BEFORE this quarter. When
    # it did not, pre_q_reslip comes back empty — which means "not observable
    # here", NOT "no re-slipped pipe existed". Q3 FY25 sits exactly on the window
    # start, so it can never show the cohort.
    g.attrs["reslip_observable"] = bool(window_open < q_start)
    return g[["opps", "starting_open_pipe", "avg_deal", "won", "lost", "slipped",
              "held", "slip_rate", "won_rate", "lost_rate", "held_rate"]]


def pre_q_slip(quarter_start, as_of, grain="Territory",
               snapshot_file="snapshot_hist.parquet") -> pd.Series:
    """Share of pipe dated into a FUTURE quarter that leaks out before it opens.

    Pre-Q slip is the first half of the timing split (see docs/analysis/slip.md).
    `slip()` measures the second half — what pushes out once the quarter is
    running. Between a run date and the quarter start, pipe already dated into
    that quarter also pushes, and the model previously assumed all of it survived.

    Measured at the SAME LEAD TIME in the prior-year quarter: if Q4 FY26 is 52
    days away, this reads the pipe dated into Q4 FY25 as at 52 days before its
    start and asks how much had moved out by the time it opened. Lead time is the
    right control because the leak is a function of how long the pipe still has
    to sit, and seasonality is preserved by using the same quarter a year back.

    Returns 0.0 for a quarter already started or past: its Pre-Q slip has already
    happened and is baked into the observed balance. That is not a special case to
    remove — it is what makes an in-flight run correct.
    """
    keys_zero = pd.Series(dtype=float, name="pre_q_slip_rate")
    lead = (pd.Timestamp(quarter_start) - pd.Timestamp(as_of)).days
    if lead <= 0:
        keys_zero.attrs["lead_days"] = lead
        keys_zero.attrs["reason"] = "quarter already started — Pre-Q slip has happened"
        return keys_zero

    h_start = pd.Timestamp(prior_year_quarter(quarter_start))
    h_end = pd.Timestamp(config.q_end(str(h_start.date())))
    read_at = h_start - pd.Timedelta(days=lead)

    snap = _require(snapshot_file)
    bts = _require("bts.parquet")
    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    TOLERANCE = pd.Timedelta(days=7)
    dates = s["snapshot_date"].dropna()
    at_or_before = dates[dates <= read_at]
    if at_or_before.empty or (read_at - at_or_before.max()) > TOLERANCE:
        raise MissingData(
            f"no snapshot within {TOLERANCE.days} days of {read_at:%Y-%m-%d}, the "
            f"point {lead} days before {config.fq_label(str(h_start.date()))} opened. "
            f"Pre-Q slip cannot be read. Widen config.HIST_SNAP_WINDOWS to cover it "
            f"and re-pull snapshot_hist.")

    def state_at(cutoff):
        d = s[s["snapshot_date"] <= cutoff]
        return d.sort_values("snapshot_date").groupby("Opp_Id").last()

    a = state_at(read_at)
    st = a["Raw_Stage"].astype(str)
    open_then = ~(st.isin(WON_STAGES) | st.isin(LOST_STAGES) | st.isin(OTHER_STAGES))
    pop = a[open_then & a["CloseDate"].between(h_start, h_end)]
    if pop.empty:
        raise MissingData(
            f"no open pipe dated into {config.fq_label(str(h_start.date()))} as at "
            f"{read_at:%Y-%m-%d}; Pre-Q slip would be 0/0.")

    b = state_at(h_start - pd.Timedelta(days=1)).reindex(pop.index)
    j = pd.DataFrame({
        "value": pop["value"],
        "Bookings_Team_static": pop["Bookings_Team_static"],
        "end_stage": b["Raw_Stage"],
        "end_close": b["CloseDate"],
    })
    if "Stage" in b.columns:
        j["end_mapped_stage"] = b["Stage"]
    # Same partition rule as everywhere else, evaluated at the quarter START
    # rather than its end: "slipped" here means it had already pushed past the
    # quarter before the quarter began.
    j["outcome"] = _partition(j, h_end)

    bt = bts.copy()
    bt["_key"] = bt["Bookings_Team_Static"].astype(str).str.strip().str.lower()
    bt = bt[["_key", "BTS_Geo", "BTS_Region", "BTS_Territory"]].drop_duplicates("_key")
    j["_key"] = j["Bookings_Team_static"].astype(str).str.strip().str.lower()
    j = j.merge(bt, on="_key", how="left")
    for c in ("BTS_Geo", "BTS_Region", "BTS_Territory"):
        j[c] = j[c].fillna("Unassigned")
    j["_g"] = _grain_key(j, grain)

    tot = j.groupby("_g")["value"].sum()
    moved = j[j["outcome"] == "slipped"].groupby("_g")["value"].sum().reindex(tot.index).fillna(0.0)
    out = (moved / tot.where(tot > 0)).rename("pre_q_slip_rate")
    out.index.name = grain
    out.attrs["lead_days"] = lead
    out.attrs["measured_on"] = config.fq_label(str(h_start.date()))
    out.attrs["read_at"] = str(read_at.date())
    out.attrs["pooled_rate"] = float(moved.sum() / tot.sum()) if tot.sum() else 0.0
    return out


def slip_inflow(from_quarter, to_quarter, grain="Territory", as_of=None,
                snapshot_file="snapshot_hist.parquet") -> pd.Series:
    """Dollars of EXISTING open pipe expected to slip out of one quarter into another.

    The inflow half of slip — the workbook's `Pre Q Inflow` / `In Q Inflow`. Pipe
    that pushes out of Q3 does not vanish; it becomes Q4's open pipe.

        inflow = open_pipe(from) x slip_rate(from) x destination_share[offset]

    Both assumptions come from `from_quarter`'s own prior-year analogue, rate and
    destination together, never pooled across quarters.

    NO DOUBLE COUNT, two ways, and both matter:

    1. Against the source quarter. `existing_pipe_bookings` applies
       `(1 - slip_rate)` to the source, which removes exactly the dollars this
       function forwards. The slipped portion is claimed by neither quarter twice.
    2. Against the sales cycle tail. This acts on EXISTING open pipe only, never
       on `create`. Newly created pipe already reaches later quarters through the
       sales cycle curve; routing it through slip as well would count it twice.

    Returns an empty Series when the destination is not a later quarter.
    """
    out = pd.Series(dtype=float, name="slip_inflow")
    offset = quarter_index(to_quarter) - quarter_index(from_quarter)
    if offset <= 0:
        out.attrs["reason"] = f"{to_quarter} does not follow {from_quarter}"
        return out

    prior = prior_year_quarter(from_quarter)
    point = slip_anchor(from_quarter, as_of, prior) if as_of else None
    s = slip(prior, grain, from_point=point, snapshot_file=snapshot_file)
    dest = slip_destinations(prior, from_point=point, snapshot_file=snapshot_file)

    share = float(dest.get(offset, 0.0))
    open_pipe = open_pipe_at(from_quarter, grain, as_of=as_of)
    rate = s["slip_rate"].reindex(open_pipe.index)
    rate = rate.fillna(rate.mean() if rate.notna().any() else 0.0)

    out = (open_pipe * rate * share).rename("slip_inflow")
    out.index.name = grain
    out.attrs["from"] = config.fq_label(from_quarter)
    out.attrs["to"] = config.fq_label(to_quarter)
    out.attrs["offset"] = offset
    out.attrs["destination_share"] = share
    out.attrs["measured_on"] = config.fq_label(prior)
    out.attrs["slipping_value"] = float((open_pipe * rate).sum())
    return out


def slip_forecast(quarter_start, open_pipe=None, grain="Territory", as_of=None,
                  snapshot_file="snapshot_hist.parquet") -> pd.DataFrame:
    """Forecast this quarter's slip from the SAME QUARTER a year earlier.

    Both assumptions come from that one historic quarter — its slip rate and its
    destination curve. Never a pooled average across quarters: the shapes differ
    too much to blend. Q3 sends 80% of its slip to the next quarter; Q4 sends 41%
    and pushes 43% out two quarters across the calendar year boundary. An average
    of those describes neither, and applying it to Q4 would move roughly a third
    of Q4's slipped dollars into the wrong quarter.

    The mechanic, per grain key:

        slipped        = open_pipe x slip_rate          (from the prior-year quarter)
        to quarter n+k = slipped x destination_share[k] (same prior-year quarter)

    `open_pipe` is a Series per grain key. Omit it to get the assumptions alone;
    supply it to get the dollars landing in each future quarter.

    Returns one row per grain key: `slip_rate`, `slipped_value`, and `to_Q+1` …
    `to_Q+8`. Source quarter and destination curve are on `.attrs`.
    """
    source = prior_year_quarter(quarter_start)
    anchor = slip_anchor(quarter_start, as_of, source) if as_of else None

    rates = slip(source, grain, from_point=anchor, snapshot_file=snapshot_file)
    dest = slip_destinations(source, from_point=anchor, snapshot_file=snapshot_file)

    out = pd.DataFrame({"slip_rate": rates["slip_rate"].fillna(0.0)})
    if open_pipe is not None:
        out["open_pipe"] = pd.Series(open_pipe).reindex(out.index).fillna(0.0)
        out["slipped_value"] = out["open_pipe"] * out["slip_rate"]
    else:
        out["slipped_value"] = float("nan")

    for k in range(1, MAX_OFFSET + 1):
        share = float(dest.get(k, 0.0))
        out[f"to_Q+{k}"] = out["slipped_value"] * share if open_pipe is not None else share

    out.attrs["source_quarter"] = config.fq_label(source)
    out.attrs["target_quarter"] = config.fq_label(quarter_start)
    out.attrs["destination_curve"] = dest
    out.attrs["anchor"] = str(anchor.date()) if anchor is not None else str(pd.Timestamp(source).date())
    out.attrs["pooled"] = False
    return out


def slip(quarter_start, grain="Territory", from_point=None,
         snapshot_file="snapshot.parquet") -> pd.DataFrame:
    """Of the pipe OPEN at the start of a historic quarter, how much neither closed
    nor was won, and moved to a later quarter.

    Definition from the model owner (2026-08-10). Measured on a HISTORIC quarter so
    the outcome is known:
        1. open pipe at the beginning of the quarter, dated to close in it
        2. follow the same opps to quarter end
        3. partition: won / lost / SLIPPED (still open, CloseDate moved out) / held

    Slip is the residual — pipe that resolved neither way and simply moved. The
    won/lost exclusion matters: without it, a won deal whose close date shifted
    would count as slip and badly overstate it.

    ANCHORING CAVEAT: this uses the latest snapshot AT OR BEFORE the quarter start.
    Root CLAUDE.md invariant 5 has the Python pipeline anchor against a pre-quarter
    buffer because opps enter the feed 1-4 days late. Whether the Excel slip
    analysis used the same anchor is unknown and is a plausible source of
    disagreement.
    """
    snap = _require(snapshot_file)
    bts = _require("bts.parquet")

    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    # Where the starting population is read. Mid-quarter this is the equivalent
    # point in the historic quarter, not its start — see equivalent_point().
    anchor = pd.Timestamp(from_point) if from_point is not None else q_start
    if not (q_start <= anchor <= q_end):
        raise ValueError(f"from_point {anchor:%Y-%m-%d} is outside the quarter "
                         f"{q_start:%Y-%m-%d}..{q_end:%Y-%m-%d}")

    if s["snapshot_date"].max() < q_end:
        raise MissingData(
            f"snapshot only reaches {s['snapshot_date'].max():%Y-%m-%d}, before this "
            f"quarter ends ({q_end:%Y-%m-%d}). Slip needs a COMPLETED quarter."
        )
    # COVERAGE, not endpoints. The historic pull is several disjoint windows, so a
    # quarter can sit entirely in a gap between them while min < anchor and
    # max > q_end both hold. Checking only the extremes passes, state_at() then
    # anchors on a snapshot months stale or finds nothing, and slip comes back a
    # confident 0.0% — the exact failure this guard exists to prevent.
    TOLERANCE = pd.Timedelta(days=7)
    dates = s["snapshot_date"].dropna()
    at_or_before = dates[dates <= anchor]
    if at_or_before.empty or (anchor - at_or_before.max()) > TOLERANCE:
        nearest = f"{at_or_before.max():%Y-%m-%d}" if not at_or_before.empty else "none"
        raise MissingData(
            f"no snapshot within {TOLERANCE.days} days at or before the anchor "
            f"{anchor:%Y-%m-%d} (nearest: {nearest}). The starting open pipe cannot be "
            f"read, so slip would be 0/0. Add this quarter to config.HIST_SNAP_WINDOWS "
            f"and re-pull snapshot_hist."
        )
    at_or_before_end = dates[dates <= q_end]
    if (q_end - at_or_before_end.max()) > TOLERANCE:
        raise MissingData(
            f"no snapshot within {TOLERANCE.days} days of the quarter end "
            f"{q_end:%Y-%m-%d} (nearest: {at_or_before_end.max():%Y-%m-%d}). Outcomes "
            f"cannot be read, so every opp would look unresolved. Add this quarter to "
            f"config.HIST_SNAP_WINDOWS and re-pull snapshot_hist."
        )

    j = classify_outcomes(s, q_start, q_end, anchor)

    b = bts.copy()
    b["_key"] = b["Bookings_Team_Static"].astype(str).str.strip().str.lower()
    b = b[["_key", "BTS_Geo", "BTS_Region", "BTS_Territory"]].drop_duplicates("_key")
    j["_key"] = j["Bookings_Team_static"].astype(str).str.strip().str.lower()
    j = j.merge(b, on="_key", how="left")
    for c in ("BTS_Geo", "BTS_Region", "BTS_Territory"):
        j[c] = j[c].fillna("Unassigned")
    j["_g"] = _grain_key(j, grain)

    g = j.groupby(["_g", "outcome"])["value"].sum().unstack(fill_value=0.0)
    for c in ("won", "lost", "slipped", "held"):
        if c not in g.columns:
            g[c] = 0.0
    g["starting_open_pipe"] = g[["won", "lost", "slipped", "held"]].sum(axis=1)
    g["slip_rate"] = g["slipped"] / g["starting_open_pipe"].where(g["starting_open_pipe"] > 0)
    g.index.name = grain
    g.attrs["quarter"] = config.fq_label(quarter_start)
    g.attrs["from_point"] = str(anchor.date())
    g.attrs["days_remaining"] = int((q_end - anchor).days)
    g.attrs["anchor"] = str(q_start.date())
    return g[["starting_open_pipe", "won", "lost", "slipped", "held", "slip_rate"]]


def open_pipe_at(quarter_start, grain="Territory", as_of=None) -> pd.Series:
    """Open pipe currently dated to close IN the given quarter, per grain key.

    Anchored to the latest snapshot at or before `as_of` (default: the latest in
    the feed). This is the base that slip and win rates act on.
    """
    snap = _require("snapshot.parquet")
    bts = _require("bts.parquet")

    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    cutoff = pd.Timestamp(as_of) if as_of is not None else s["snapshot_date"].max()
    latest = s[s["snapshot_date"] <= cutoff].sort_values("snapshot_date").groupby("Opp_Id").last()

    live = latest[
        ~latest["Raw_Stage"].astype(str).str.contains("Closed", case=False, na=False)
        & latest["CloseDate"].between(q_start, q_end)
    ].copy()

    b = bts.copy()
    b["_key"] = b["Bookings_Team_Static"].astype(str).str.strip().str.lower()
    b = b[["_key", "BTS_Geo", "BTS_Region", "BTS_Territory"]].drop_duplicates("_key")
    live["_key"] = live["Bookings_Team_static"].astype(str).str.strip().str.lower()
    live = live.merge(b, on="_key", how="left")
    for c in ("BTS_Geo", "BTS_Region", "BTS_Territory"):
        live[c] = live[c].fillna("Unassigned")
    live["_g"] = _grain_key(live, grain)

    out = live.groupby("_g")["value"].sum()
    out.name = "open_pipe"
    out.attrs["as_of"] = str(pd.Timestamp(cutoff).date())
    return out


def closed_won_at(quarter_start, grain="Territory", as_of=None) -> pd.Series:
    """Bookings ALREADY WON in the given quarter, per grain key.

    For an in-flight quarter this is the largest term in what the bookings target
    has already been met by. It needs no win rate and no slip — it is banked. Pipe
    create only has to cover what is left after it.

    Counterpart to open_pipe_at: that function excludes every Closed stage, so
    without this the won half of an in-flight quarter is invisible to the solve.
    """
    snap = _require("snapshot.parquet")
    bts = _require("bts.parquet")

    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    cutoff = pd.Timestamp(as_of) if as_of is not None else s["snapshot_date"].max()
    latest = s[s["snapshot_date"] <= cutoff].sort_values("snapshot_date").groupby("Opp_Id").last()

    # "Closed Won" and "Stage 5 - Closed Won" both appear; "Closed Lost" and
    # "Closed Deferred" must not match, so require Won as well as Closed.
    won = latest[
        latest["Raw_Stage"].astype(str).str.contains(r"Closed.*Won", case=False,
                                                     na=False, regex=True)
        & latest["CloseDate"].between(q_start, q_end)
    ].copy()

    b = bts.copy()
    b["_key"] = b["Bookings_Team_Static"].astype(str).str.strip().str.lower()
    b = b[["_key", "BTS_Geo", "BTS_Region", "BTS_Territory"]].drop_duplicates("_key")
    won["_key"] = won["Bookings_Team_static"].astype(str).str.strip().str.lower()
    won = won.merge(b, on="_key", how="left")
    for c in ("BTS_Geo", "BTS_Region", "BTS_Territory"):
        won[c] = won[c].fillna("Unassigned")
    won["_g"] = _grain_key(won, grain)

    out = won.groupby("_g")["value"].sum()
    out.name = "closed_won"
    out.attrs["as_of"] = str(pd.Timestamp(cutoff).date())
    return out


def existing_pipe_bookings(quarter_start, slip_quarters, sku=None, grain="Territory",
                           window=None, as_of=None, slip_from_points=None,
                           slip_snapshot_file="snapshot.parquet",
                           pre_q_slip_rate=None, slip_inflow_pipe=None,
                           in_q_slip_rate=None) -> pd.Series:
    """Bookings expected from pipe that ALREADY exists, slip- and win-rate-adjusted.

        adjusted = open_pipe x (1 - pre_q_slip) + slip_inflow
        expected = adjusted x (1 - in_q_slip) x pre_q_win_rate

    `pre_q`, not `in_quarter` — see the comment on `wr` below. The sales cycle
    curve is NOT applied here: it governs newly created pipe only, so existing
    pipe and pipe create cannot double-count each other.

    `slip_quarters` are COMPLETED quarters whose observed slip supplies the rate;
    several are averaged so one unusual quarter does not set the assumption.

    This is what the goal seek subtracts from the bookings target. Without it the
    gap is the full target and required create is overstated — which is why
    derive_targets flags its absence rather than quietly defaulting to zero.
    """
    if sku is None:
        sku = load_sku(grain)

    rates = win_rates(sku, window, grain)
    open_pipe = open_pipe_at(quarter_start, grain, as_of=as_of)

    # slip_from_points maps a historic quarter -> the point to measure from, so a
    # mid-quarter run compares like with like (W7-to-end against W7-to-end). The
    # population still open at W7 is enriched in deals that do not close, so its
    # slip RATE is higher than the quarter-start rate even though fewer dollars
    # move. Applying a quarter-start rate to a W7 balance mismatches populations.
    points = slip_from_points or {}
    frames = []
    for q in slip_quarters:
        try:
            frames.append(slip(q, grain, from_point=points.get(q),
                               snapshot_file=slip_snapshot_file)["slip_rate"])
        except MissingData:
            raise
    if not frames:
        raise ValueError("at least one completed quarter is needed to measure slip")
    slip_rate = pd.concat(frames, axis=1).mean(axis=1)

    keys = open_pipe.index
    sr = slip_rate.reindex(keys).fillna(slip_rate.mean() if len(slip_rate) else 0.0)
    if in_q_slip_rate is not None:
        # A challenge to the measured In Q slip. Applied per key where a Series is
        # given, or across the board for a scalar — "I don't believe 64%, call it
        # 40%" has to be answerable without naming 27 territories.
        ov = (pd.Series(in_q_slip_rate).reindex(keys)
              if hasattr(in_q_slip_rate, "get") or isinstance(in_q_slip_rate, pd.Series)
              else pd.Series(float(in_q_slip_rate), index=keys))
        sr = ov.fillna(sr)
    # The PRE-Q win rate, not the In Q one. Pipe already open at quarter start was
    # created in an earlier quarter, so its analogue is `pre_q` — deals that closed
    # in a quarter after the one that created them. Using in_quarter here applies a
    # rate 3-4x too high (0.41-0.66 vs 0.11-0.21) and swamps the gap.
    wr = rates["pre_q"].reindex(keys).fillna(rates["pre_q"].mean() if len(rates) else 0.0)

    # The full timing sequence, in the order it happens in the world:
    #
    #   1. PRE-Q SLIP   pipe dated into the quarter leaks out before it opens.
    #                   Zero for an in-flight quarter — it has already happened
    #                   and is inside the observed balance.
    #   2. SLIP INFLOW  pipe pushed out of earlier quarters arrives, at the
    #                   boundary, so it is added AFTER the pre-Q haircut and is
    #                   not subject to it.
    #   3. IN-Q SLIP    the adjusted base pushes out during the quarter. The
    #                   inflow IS exposed to this — arriving pipe can slip again,
    #                   which the 55% serial re-slip rate says it often does.
    #   4. WIN RATE     acts on what survives.
    #
    # See docs/analysis/slip.md, "What supplies a future quarter, and what drains
    # it" — this covers terms 3, 4 and 5 of six.
    pq = (pd.Series(0.0, index=keys) if pre_q_slip_rate is None
          else pd.Series(pre_q_slip_rate).reindex(keys).fillna(0.0))
    inflow = (pd.Series(0.0, index=keys) if slip_inflow_pipe is None
              else pd.Series(slip_inflow_pipe).reindex(keys).fillna(0.0))

    adjusted = open_pipe * (1.0 - pq) + inflow
    out = adjusted * (1.0 - sr) * wr
    out.name = "expected_bookings_from_existing_pipe"
    out.attrs["open_pipe"] = open_pipe
    out.attrs["pre_q_slip_rate"] = pq
    out.attrs["slip_inflow"] = inflow
    out.attrs["adjusted_open_pipe"] = adjusted
    out.attrs["in_q_slip_rate"] = sr
    out.attrs["slip_quarters"] = list(slip_quarters)
    out.attrs["mean_slip_rate"] = float(sr.mean()) if len(sr) else None
    out.attrs["as_of"] = open_pipe.attrs.get("as_of")
    return out
