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
quarters are solved in chronological order with the maturation tail carried
forward. Every link is linear, so the system is triangular and forward
substitution is exact.

Requires cached parquet from a pull: data/sku_nacv.parquet (with CreateDate) and
data/bts.parquet. Slip additionally needs data/snapshot.parquet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from pipeline import config

# Stage values emitted by SKU_SQL's CASE. 'Closed' means Closed Lost/Deferred.
WON, LOST, OPEN = "Closed Won", "Closed", "Open"
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

def _require(name: str) -> pd.DataFrame:
    path = config.DATA / name
    if not path.exists():
        raise MissingData(
            f"{path} not found. Pull it first (run_pull), which needs VPN and a live "
            f"`az login`. Nothing here can be computed from the targets CSV alone."
        )
    return pd.read_parquet(path)


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
# Step 1 — sales cycle -> maturation curve
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


def maturation_curve(sku: pd.DataFrame, window=None, grain="Territory") -> pd.DataFrame:
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
    """Win rate per grain key, split in-quarter vs later.

    in_quarter — deals that closed in the SAME quarter they were created
    later      — deals that closed in a subsequent quarter

    Rate is won value / decided value (won + lost). Open deals are excluded: they
    have not decided, and counting them as losses understates the rate.
    """
    d = sku[sku["Stage"].isin([WON, LOST])].copy()
    d = d[_window_mask(d["CreateDate"], window)]
    d = d[d["create_q"].notna() & d["close_q"].notna()]
    d["offset"] = (d["close_q"] - d["create_q"]).astype(int)
    d = d[d["offset"].between(0, MAX_OFFSET)]
    d["_g"] = _grain_key(d, grain)
    d["_bucket"] = d["offset"].eq(0).map({True: "in_quarter", False: "later"})
    d["_won"] = d["value"].where(d["Stage"].eq(WON), 0.0)

    g = d.groupby(["_g", "_bucket"])[["_won", "value"]].sum()
    rate = (g["_won"] / g["value"].where(g["value"] > 0)).unstack()
    rate = rate.reindex(columns=["in_quarter", "later"])
    decided = g["value"].unstack().reindex(columns=["in_quarter", "later"]).fillna(0.0)
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

def yield_per_dollar(curve_row: pd.Series, in_q_rate: float, later_rate: float) -> float:
    """Bookings produced per dollar of pipe created.

        Q0_wt x in_quarter_rate  +  sum(Q+1..Q+8 wts) x later_rate

    This is the denominator of the closed-form solve, and a meaningful figure in
    its own right — report it.
    """
    q0 = float(curve_row.get(0, 0.0))
    later = float(curve_row.reindex(range(1, MAX_OFFSET + 1)).fillna(0.0).sum())
    return q0 * float(in_q_rate or 0.0) + later * float(later_rate or 0.0)


@dataclass
class DerivedQuarter:
    quarter: str
    quarter_start: str
    rows: pd.DataFrame
    assumptions: dict = field(default_factory=dict)


def derive_targets(
    sku: pd.DataFrame,
    bookings_target: pd.Series,
    quarter_starts: list[str],
    grain: str = "Territory",
    window=None,
    existing_pipe_bookings: pd.Series | None = None,
) -> pd.DataFrame:
    """Solve required pipe create per grain key, per quarter, in chronological order.

    `bookings_target` — the GIVEN input, indexed by grain key. Today it comes from
    Target_Monthly.csv's Bookings rows, but it is a parameter because it will be
    supplied directly.

    `existing_pipe_bookings` — expected bookings from pipe already open, slip- and
    win-rate-adjusted. Omitted means zero, which OVERSTATES the required create;
    the result flags this so the number is never mistaken for complete.

    Quarters are solved in order and each quarter's maturation tail is carried
    forward into later quarters, reducing their gap. Solving independently would
    overstate every quarter after the first.
    """
    quarter_starts = sorted(quarter_starts, key=lambda q: pd.Timestamp(q))
    curve = maturation_curve(sku, window, grain)
    rates = win_rates(sku, window, grain)

    keys = sorted(set(curve.index) & set(bookings_target.index))
    carried: dict[tuple[str, int], float] = {}  # (key, quarter_index) -> bookings already covered
    out = []

    for qs in quarter_starts:
        qi = quarter_index(qs)
        floor = historic_floor(sku, qs, grain)

        for key in keys:
            crow = curve.loc[key]
            in_q = rates.loc[key, "in_quarter"] if key in rates.index else None
            later = rates.loc[key, "later"] if key in rates.index else None
            yld = yield_per_dollar(crow, in_q, later)

            target = float(bookings_target.get(key, 0.0))
            existing = float((existing_pipe_bookings or {}).get(key, 0.0)) if existing_pipe_bookings is not None else 0.0
            tail = carried.get((key, qi), 0.0)

            gap = target - existing - tail
            required = (gap / yld) if (yld > 0 and gap > 0) else 0.0

            fl = float(floor.get(key, 0.0))
            binding = "floor" if fl > required else ("gap" if required > 0 else "none")
            create = max(required, fl)

            # Propagate this quarter's maturation tail into later quarters.
            for off in range(1, MAX_OFFSET + 1):
                w = float(crow.get(off, 0.0))
                if w:
                    carried[(key, qi + off)] = carried.get((key, qi + off), 0.0) + create * w * float(later or 0.0)

            out.append({
                "quarter": config.fq_label(qs),
                "quarter_start": qs,
                grain: key,
                "bookings_target": target,
                "expected_from_existing_pipe": existing,
                "maturation_tail_from_earlier_quarters": tail,
                "gap": gap,
                "yield_per_dollar": yld,
                "required_by_gap": required,
                "historic_floor": fl,
                "pipe_create_target": create,
                "binding": binding,
                "in_quarter_win_rate": in_q,
                "later_win_rate": later,
                "q0_weight": float(crow.get(0, 0.0)),
            })

    df = pd.DataFrame(out)
    df.attrs["existing_pipe_supplied"] = existing_pipe_bookings is not None
    df.attrs["window"] = window
    df.attrs["grain"] = grain
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

def slip(quarter_start, grain="Territory") -> pd.DataFrame:
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
    snap = _require("snapshot.parquet")
    bts = _require("bts.parquet")

    q_start = pd.Timestamp(quarter_start)
    q_end = pd.Timestamp(config.q_end(quarter_start))

    s = snap.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"], errors="coerce")
    s["CloseDate"] = pd.to_datetime(s["CloseDate"], errors="coerce")
    s["value"] = pd.to_numeric(s["Cal_IACV"], errors="coerce").fillna(0.0)

    if s["snapshot_date"].max() < q_end:
        raise MissingData(
            f"snapshot only reaches {s['snapshot_date'].max():%Y-%m-%d}, before this "
            f"quarter ends ({q_end:%Y-%m-%d}). Slip needs a COMPLETED quarter."
        )

    def state_at(cutoff):
        d = s[s["snapshot_date"] <= cutoff]
        return d.sort_values("snapshot_date").groupby("Opp_Id").last()

    start = state_at(q_start)
    end = state_at(q_end)

    open_start = start[
        start["Raw_Stage"].notna()
        & ~start["Raw_Stage"].astype(str).str.contains("Closed", case=False, na=False)
        & start["CloseDate"].between(q_start, q_end)
    ]

    j = open_start[["value", "Bookings_Team_static", "CloseDate"]].join(
        end[["Raw_Stage", "CloseDate"]].rename(
            columns={"Raw_Stage": "end_stage", "CloseDate": "end_close"}), how="left")

    st = j["end_stage"].astype(str)
    won = st.str.contains("Closed Won|Closed/Pending", case=False, na=False)
    closed = st.str.contains("Closed", case=False, na=False) & ~won
    moved = (~won) & (~closed) & (j["end_close"] > q_end)

    j["outcome"] = "held"
    j.loc[won, "outcome"] = "won"
    j.loc[closed, "outcome"] = "lost"
    j.loc[moved, "outcome"] = "slipped"

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
    g.attrs["anchor"] = str(q_start.date())
    return g[["starting_open_pipe", "won", "lost", "slipped", "held", "slip_rate"]]
