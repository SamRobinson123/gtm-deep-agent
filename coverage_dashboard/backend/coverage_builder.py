"""Build the per-quarter, per-week coverage frame at GEO × Deal Type grain."""

import numpy as np
import pandas as pd

# Pre-quarter buffer: how far before a quarter's start we still pull snapshots,
# so "week 1" can anchor to the end-of-prior-quarter snapshot (PBI's "starting
# pipe"). Must comfortably exceed the snapshot cadence (~weekly) so the prior
# quarter's final snapshot is always captured.
ENTRY_BUFFER = pd.Timedelta(days=14)

LATE_STAGES = frozenset({
    "3 - Executive Presentation",
    "4 - Technical Evaluation",
    "5 - Negotiation / Business Procurement",
    "6 - Closed/Pending",
    "Stage 4 - Closed Pending",
})

# Historical-win-rate stage taxonomy. Locked from PLAN.md §4. `6 - Closed/Pending`
# counts as Won here (same treatment as elsewhere in the project), even though it's
# the source of the phantom-pending issue that drove `booked` to switch to the live
# SKU source (§3.5). Win-rate impact of the phantom effect is smaller than its
# impact on dollar bookings.
WON_STAGES = frozenset({
    "Closed Won",
    "Stage 5 - Closed Won",
    "6 - Closed/Pending",
})
LOST_STAGES = frozenset({
    "Closed Lost",
    "Closed Deferred",
})

# A quarter with fewer open-pipe weeks than this is "sparse" (FY24 Q1: 2 of 13)
# and is excluded from every needed-coverage training set — its mid-quarter
# conversions are computed against an artificial open_pipe. Matches the
# renderer's sparse-quarter flag.
SPARSE_WEEK_THRESHOLD = 8

_GEO_CANONICAL = {"PubSec": "Public Sector", "Pubsec": "Public Sector"}


def _bucket_region_family(rf):
    """Roll up the mapping's `BTS_RegionFamily` into the four canonical geos:
    AMS / APAC / EMEA / Public Sector.

    `BTS_Geo` alone is unreliable for Public Sector. Returns None for values
    that don't map cleanly (e.g., "DevOps", "AMS Sealights") — the team-name
    fallback in `_bucket_from_team_name` then handles those.
    """
    if rf is None or pd.isna(rf):
        return None
    s = str(rf).strip()
    if s in ("Pubsec", "PubSec"):
        return "Public Sector"
    if s.startswith("AMS"):
        return "AMS"
    if s.startswith("EMEA"):
        return "EMEA"
    if s.startswith("APAC"):
        return "APAC"
    return None  # let the team-name fallback handle DevOps / Sealights / etc.


def _bucket_from_team_name(team):
    """Geo bucket derived from the raw `Bookings_Team_static` name. Catches
    historic teams (e.g., "AMS Core North Central") and DevOps teams whose
    BTS_RegionFamily="DevOps" loses the geo info.

    Historic-data-inclusive: DevOps teams ARE bucketed (to their geo prefix),
    not dropped — because closed quarters from FY24/FY25 contain real DevOps
    bookings that the historic-coverage analysis needs counted.
    """
    if team is None or pd.isna(team):
        return None
    low = str(team).strip().lower()
    # Public Sector first — catches "AMS Public Sector", "AMS Public Sector - FED", etc.
    if "public sector" in low or "pubsec" in low:
        return "Public Sector"
    if low.startswith("ams"):
        return "AMS"
    if low.startswith("apac"):
        return "APAC"
    if low.startswith("emea"):
        return "EMEA"
    return None


def _assign_quarter(d: pd.Series) -> pd.Series:
    """Return 'FY{nn} Q{n}' for a Series of dates.

    Assumes Tricentis fiscal year = calendar year. See PLAN.md §18 Q8.
    """
    yy = d.dt.year % 100
    q = (d.dt.month - 1) // 3 + 1
    return "FY" + yy.astype(str).str.zfill(2) + " Q" + q.astype(str)


def _attach_geo(snapshot: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """Join the mapping's `BTS_RegionFamily` to each opportunity and bucket it
    to one of AMS / APAC / EMEA / Public Sector.

    Joining on `BTS_RegionFamily` (rather than `BTS_Geo`) is what fixes the
    Public Sector bug — see PLAN.md §5 and `_bucket_region_family()` above.
    """
    map_sub = mapping[["Bookings_Team_Static", "BTS_RegionFamily"]].copy()
    map_sub["_key"] = map_sub["Bookings_Team_Static"].str.lower()
    map_sub = map_sub[["_key", "BTS_RegionFamily"]]

    out = snapshot.copy()
    out["_key"] = out["Bookings_Team_static"].str.lower()
    out = out.merge(map_sub, on="_key", how="left").drop(columns=["_key"])
    # Primary: bucket via the live mapping's BTS_RegionFamily for active teams
    out["geo"] = out["BTS_RegionFamily"].apply(_bucket_region_family)
    # Fallback: name-pattern bucket for historic teams not in the live mapping
    # (e.g., "AMS Core North Central" — re-org'd away but still in old snapshots)
    out["geo"] = out["geo"].fillna(out["Bookings_Team_static"].apply(_bucket_from_team_name))
    out["geo"] = out["geo"].replace(_GEO_CANONICAL)  # belt-and-suspenders
    return out


def _week_of_quarter(d: pd.Series) -> pd.Series:
    """Calendar-aligned week_of_quarter (1..13) for a Series of dates.

    Day 0–6 of the quarter = week 1, ..., day 84+ = week 13. Matches the
    bucketing used for snapshot_date in `build_coverage()`.
    """
    q_start_month = ((d.dt.month - 1) // 3) * 3 + 1
    q_start = pd.to_datetime({
        "year":  d.dt.year,
        "month": q_start_month,
        "day":   1,
    })
    days_into_q = (d - q_start).dt.days
    return ((days_into_q // 7 + 1).clip(lower=1, upper=13).astype(int))


def _pin_to_week_date(snap: pd.DataFrame) -> pd.DataFrame:
    """Keep one snapshot_date per (quarter, week_of_quarter), so every opp in a
    week is observed on the same calendar date.

    Rule (Sam, 2026-06-01):
    - **Every week** pins to its **beginning-of-week** balance — the **earliest
      in-quarter snapshot** in that week (the standing pipe at the start of the
      week / its "Monday"). Week 1's earliest in-quarter is the start-of-quarter
      snapshot (Apr-1 for Q2, Jan-1 for Q1), so the PBI "starting pipe" falls out
      automatically (FY26 Q2 wk1 ≈ $106.1M vs PBI $106.18M; Q1 ≈ $104.8M).
    - **Exception — the in-flight current week** (the current quarter's latest
      week with snapshot data) pins to its **latest** snapshot (today's standing
      pipe), to tie to gtm-weekly-reporting's "latest as-of" weekly number
      (FY26 Q2 wk9 = $46.78M = GTM). Past weeks stay on their week-start balance.

    Requires `quarter`, `week_of_quarter`, `snapshot_date` columns.
    """
    # Restrict to in-quarter snapshots so the pre-quarter buffer never leaks in.
    q_start = snap["quarter"].map(_quarter_start)
    inq = snap[snap["snapshot_date"] >= q_start]

    # Default: beginning-of-week = earliest in-quarter snapshot per (quarter,
    # week). Reindexed to the full frame (buffer rows -> NaT, which never equals
    # a real snapshot_date below, so they drop out).
    earliest = inq.groupby(["quarter", "week_of_quarter"])["snapshot_date"].transform("min")
    pin = earliest.reindex(snap.index)

    # Exception: the in-flight current week (current quarter's latest week with
    # snapshot data) pins to its LATEST snapshot — today's standing pipe — to tie
    # to gtm-weekly-reporting. Only this one (quarter, week) cell switches.
    current_quarter = "FY%02d Q%d" % (
        pd.Timestamp.now().year % 100, (pd.Timestamp.now().month - 1) // 3 + 1
    )
    cur_inq = inq[inq["quarter"] == current_quarter]
    if not cur_inq.empty:
        current_week = int(cur_inq["week_of_quarter"].max())
        latest_cur = cur_inq.loc[cur_inq["week_of_quarter"] == current_week, "snapshot_date"].max()
        cur_week_mask = (snap["quarter"] == current_quarter) & (snap["week_of_quarter"] == current_week)
        pin = pin.where(~cur_week_mask, latest_cur)
    return snap[snap["snapshot_date"] == pin].copy()


def _live_booked_by_week(live: pd.DataFrame, mapping: pd.DataFrame, grain: str = "Deal_Type") -> pd.DataFrame:
    """Cumulative booked-by-week from live `sku_nacv_fact` data.

    For each (quarter, geo, <grain>), returns 13 rows with `booked` =
    cumulative sum of `Product_NACV` for opps whose `Opp_Closed_Date` falls
    in weeks 1..N of the quarter. Quarters with no live closures yield no
    rows.

    `grain` is "Deal_Type" (default) or "segment". The segment grain derives the
    account's INITIAL tier from the live pull's `Quarter_Start_Segment` (same
    earliest-quarter convention as the snapshot SQL; missing tiers bucket to
    "Unassigned"). (The product page no longer uses this — it attributes booked
    by the allocated-table shares via `_live_booked_product_by_share`.) Both
    grains keep the same opp population (geo + deal type present) so the pages
    tie to the dollar.
    """
    live = live.copy()
    live = live.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
    live = _attach_geo(live, mapping)
    live["Opp_Closed_Date"] = pd.to_datetime(live["Opp_Closed_Date"])
    live["quarter"] = _assign_quarter(live["Opp_Closed_Date"])
    live["week_of_quarter"] = _week_of_quarter(live["Opp_Closed_Date"])
    live = live.dropna(subset=["geo", "Deal_Type"])
    if grain == "segment":
        live["segment"] = _canon_segment(live["Quarter_Start_Segment"]).fillna("Unassigned")
    gc = ["quarter", "geo", grain]
    per_week = (
        live.groupby(gc + ["week_of_quarter"], as_index=False)["Product_NACV"]
        .sum()
    )

    # Reindex each (quarter, geo, <grain>) to all 13 weeks so cumulative is well-defined
    groups = per_week[gc].drop_duplicates()
    weeks = pd.DataFrame({"week_of_quarter": range(1, 14)})
    grid = groups.merge(weeks, how="cross")
    per_week = grid.merge(per_week, on=gc + ["week_of_quarter"], how="left").fillna({"Product_NACV": 0.0})

    per_week = per_week.sort_values(gc + ["week_of_quarter"])
    per_week["booked_live"] = (
        per_week.groupby(gc)["Product_NACV"].cumsum()
    )
    return per_week[gc + ["week_of_quarter", "booked_live"]]


def _override_inflight_latest_booked(
    out: pd.DataFrame,
    live_wk13: pd.DataFrame,
    key_cols: list[str],
) -> pd.DataFrame:
    """In-flight quarter: override its LATEST snapshot week's booked with the
    live total booked so far (Sam, 2026-06-05) — the same "what they ACTUALLY
    booked" source the wk-13 override gives completed quarters. Weeks before
    the latest keep the snapshot Closed-Won progression; the latest week jumps
    to the live number, so it intentionally no longer matches GTM's snapshot
    booked. `live_wk13` is the wk-13 cumulative slice of
    `_live_booked_by_week` (= the quarter's live total to date); `key_cols`
    is the frame's grain incl. "quarter" (e.g. ["quarter","geo","Deal_Type"]).
    """
    now = pd.Timestamp.now()
    cur_q = "FY%02d Q%d" % (now.year % 100, (now.month - 1) // 3 + 1)
    snap_rows = out[(out["quarter"] == cur_q) & out["open_pipe"].notna()]
    if snap_rows.empty:
        return out
    cur_week = int(snap_rows["week_of_quarter"].max())
    if cur_week >= 13:
        return out  # quarter at its final week — the wk-13 override owns it
    live_cur = live_wk13[live_wk13["quarter"] == cur_q][key_cols + ["booked_live"]]
    if live_cur.empty:
        return out
    out = out.merge(live_cur, on=key_cols, how="left")
    tgt = (out["quarter"] == cur_q) & (out["week_of_quarter"] == cur_week)
    out.loc[tgt, "booked"] = out.loc[tgt, "booked_live"].fillna(0.0)
    return out.drop(columns=["booked_live"])


def _quarter_start(quarter: str) -> pd.Timestamp:
    """'FY26 Q3' -> Timestamp('2026-07-01')."""
    fy = 2000 + int(quarter[2:4])
    qn = int(quarter[-1])
    return pd.Timestamp(year=fy, month=(qn - 1) * 3 + 1, day=1)


def _future_standing_frame(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    live_booked: pd.DataFrame | None,
    targets: pd.DataFrame,
    future_quarters: set[str],
) -> pd.DataFrame:
    """One 'current standing' row per (future quarter, geo, deal_type) at week 1.

    Future quarters (those starting after the run date) have no in-quarter
    snapshots, so the normal weekly build produces nothing for them. Instead we
    report them as a single week-1 point:

    - `open_pipe` / `ls_pipe` from the LATEST available snapshot — the open pipe
      that exists *today* for deals whose CloseDate falls in that quarter.
    - `booked` = total live Closed-Won so far for that quarter (not split by week).
    - every (geo, deal_type) target combo is included (zero-filled) so the
      quarter's full target still flows through the downstream target join.

    Returns the same column set the main frame carries pre-target-join.
    """
    cols = ["quarter", "geo", "Deal_Type", "week_of_quarter",
            "open_pipe", "ls_pipe", "booked"]
    if not future_quarters:
        return pd.DataFrame(columns=cols)

    gc = ["quarter", "geo", "Deal_Type"]

    # Current standing open / late-stage pipe from the latest snapshot.
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])
    snap = snap[snap["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
    if snap.empty:
        openp = pd.DataFrame(columns=gc + ["open_pipe"])
        lsp = pd.DataFrame(columns=gc + ["ls_pipe"])
    else:
        cur = snap[snap["snapshot_date"] == snap["snapshot_date"].max()]
        openp = (cur[cur["Stage"] == "Open"].groupby(gc, as_index=False)["Cal_IACV"].sum()
                 .rename(columns={"Cal_IACV": "open_pipe"}))
        lsp = (cur[(cur["Stage"] == "Open") & (cur["Raw_Stage"].isin(LATE_STAGES))]
               .groupby(gc, as_index=False)["Cal_IACV"].sum()
               .rename(columns={"Cal_IACV": "ls_pipe"}))

    # Total live booked so far for these future quarters.
    booked = pd.DataFrame(columns=gc + ["booked"])
    if live_booked is not None and not live_booked.empty:
        lb = live_booked.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
        lb = _attach_geo(lb, mapping)
        lb["Opp_Closed_Date"] = pd.to_datetime(lb["Opp_Closed_Date"])
        lb["quarter"] = _assign_quarter(lb["Opp_Closed_Date"])
        lb = lb[lb["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
        if not lb.empty:
            booked = (lb.groupby(gc, as_index=False)["Product_NACV"].sum()
                      .rename(columns={"Product_NACV": "booked"}))

    # Include all target combos so the quarter's full target flows through.
    t = targets.rename(columns={"deal_type": "Deal_Type"})
    tgt = t[t["quarter"].isin(future_quarters)][gc].drop_duplicates()

    combos = pd.concat([openp[gc], lsp[gc], booked[gc], tgt], ignore_index=True).drop_duplicates()
    if combos.empty:
        return pd.DataFrame(columns=cols)
    frame = (combos.merge(openp, on=gc, how="left")
                   .merge(lsp, on=gc, how="left")
                   .merge(booked, on=gc, how="left"))
    frame[["open_pipe", "ls_pipe", "booked"]] = frame[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)
    frame["week_of_quarter"] = 1
    return frame[cols]


def build_coverage(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    targets: pd.DataFrame,
    live_booked: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build the coverage frame.

    Inputs:
        snapshot:  output of `pull_snapshot()`
        mapping:   output of `load_booking_team_mapping()` (active rows)
        targets:   output of `load_quarter_targets()` (long format)

    Output (one row per (quarter, geo, deal_type, week_of_quarter)):
        fiscal_year, quarter, geo, deal_type,
        week_of_quarter,
        open_pipe, ls_pipe, booked,
        target_usd, ltb, total_cov, ls_cov
    """
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])

    snap["quarter"] = _assign_quarter(snap["CloseDate"])

    # A deal belongs to its CloseDate's quarter. Keep snapshots taken DURING that
    # quarter, PLUS a ~2-week pre-quarter buffer, so "week 1" can anchor to the
    # end-of-prior-quarter snapshot (PBI's "starting pipe" — see §6.2 and
    # _pin_to_week_date). q_start/q_end are that quarter's bounds.
    q_start_month = ((snap["CloseDate"].dt.month - 1) // 3) * 3 + 1
    q_start = pd.to_datetime({
        "year":  snap["CloseDate"].dt.year,
        "month": q_start_month,
        "day":   1,
    })
    q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    snap = snap[
        (snap["snapshot_date"] >= q_start - ENTRY_BUFFER)
        & (snap["snapshot_date"] <= q_end)
    ].copy()

    # Calendar-aligned week_of_quarter (1..13). Day 0–6 of quarter = week 1,
    # ..., day 84–90 = week 13. Pre-quarter buffer snapshots have negative
    # days-into-quarter and clip to week 1. Overflow at quarter end clips to 13.
    q_start = q_start[snap.index]  # realign after the filter
    days_into_q = (snap["snapshot_date"] - q_start).dt.days
    snap["week_of_quarter"] = (
        (days_into_q // 7 + 1).clip(lower=1, upper=13).astype(int)
    )

    # Pin each week to one snapshot_date: earliest in the week, latest for each
    # quarter's final week. See _pin_to_week_date() for the full rationale.
    snap = _pin_to_week_date(snap)

    snap = snap.dropna(subset=["geo", "Deal_Type"])

    group_cols = ["quarter", "geo", "Deal_Type", "week_of_quarter"]

    open_pipe = (
        snap[snap["Stage"] == "Open"]
        .groupby(group_cols, as_index=False)["Cal_IACV"].sum()
        .rename(columns={"Cal_IACV": "open_pipe"})
    )
    ls_pipe = (
        snap[(snap["Stage"] == "Open") & (snap["Raw_Stage"].isin(LATE_STAGES))]
        .groupby(group_cols, as_index=False)["Cal_IACV"].sum()
        .rename(columns={"Cal_IACV": "ls_pipe"})
    )
    # Match the cousin project's convention (`pipeline.py::build_deal_type_table`):
    # count any Closed Won row as booked, without filtering on `CloseDate <= snapshot_date`.
    # If Sales flagged the opp Closed Won, count it.
    booked = (
        snap[snap["Stage"] == "Closed Won"]
        .groupby(group_cols, as_index=False)["Cal_IACV"].sum()
        .rename(columns={"Cal_IACV": "booked"})
    )

    out = open_pipe.merge(ls_pipe, on=group_cols, how="outer")
    out = out.merge(booked, on=group_cols, how="outer")
    out[["open_pipe", "ls_pipe", "booked"]] = (
        out[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)
    )

    out = out.sort_values(["quarter", "geo", "Deal_Type", "week_of_quarter"])

    # ── BOOKED: snapshot Closed Won for weeks 1..12; live TOTAL at week 13.
    # (Sam, 2026-06-01.) Weeks 1..12 of every quarter use the snapshot `Closed
    # Won` `Cal_IACV` booked computed above — the same gross metric as open pipe
    # (consistent coverage), frozen, and matching gtm-weekly-reporting. Only
    # **week 13** (the final week, present once a quarter has completed) is
    # overridden with the live `sku_nacv_fact` TOTAL bookings for the quarter
    # (cumulative `Product_NACV` through week 13 = the true final number, ties to
    # Historic.xlsx/PBI) — snapshot Closed Won understates that final total. The
    # in-flight quarter has no real week 13 yet; its LATEST week gets the same
    # live treatment via `_override_inflight_latest_booked` below (weeks before
    # it stay snapshot); future quarters are handled separately below.
    if live_booked is not None and not live_booked.empty:
        live_by_week = _live_booked_by_week(live_booked, mapping)
        live_wk13 = live_by_week[live_by_week["week_of_quarter"] == 13]
        out = out.merge(
            live_wk13,
            on=["quarter", "geo", "Deal_Type", "week_of_quarter"],
            how="outer",
        )
        # Rows the live week-13 frame supplied but the snapshot didn't (e.g. the
        # in-flight quarter's not-yet-real week 13) keep open_pipe/ls_pipe NaN so
        # total_cov reads "no data" rather than a spurious 0×.
        live_only_mask = out["open_pipe"].isna() & out["ls_pipe"].isna()
        # Override booked with the live total ONLY at week 13; weeks 1..12 keep
        # their snapshot Closed Won booked.
        wk13_mask = out["week_of_quarter"] == 13
        out.loc[wk13_mask, "booked"] = out.loc[wk13_mask, "booked_live"].fillna(0.0)
        out = out.drop(columns=["booked_live"])
        not_live_only = ~live_only_mask
        out.loc[not_live_only, "open_pipe"] = out.loc[not_live_only, "open_pipe"].fillna(0.0)
        out.loc[not_live_only, "ls_pipe"]   = out.loc[not_live_only, "ls_pipe"].fillna(0.0)
        # In-flight quarter: its latest week's booked also jumps to the live
        # total so far (Sam 2026-06-05) — same source as wk 13, applied to
        # "today". Weeks before it keep the snapshot progression.
        out = _override_inflight_latest_booked(
            out, live_wk13, ["quarter", "geo", "Deal_Type"])

    # ── FUTURE QUARTERS: any quarter that starts after the run date has no
    # in-quarter snapshots, so the weekly build above left it empty (the
    # live-booked merge only scattered partial booked rows across weeks). Replace
    # those with a single week-1 "current standing" point — today's open pipe for
    # the quarter's close-dated deals + total live booked so far + full target —
    # so Q3/Q4 etc. show their current pipeline and bookings. (Sam, 2026-05-29)
    run_date = pd.Timestamp.now().normalize()
    candidate_qs = set(out["quarter"].dropna()) | set(targets["quarter"].dropna())
    future_quarters = {q for q in candidate_qs if _quarter_start(q) > run_date}
    if future_quarters:
        out = out[~out["quarter"].isin(future_quarters)].copy()
        fut = _future_standing_frame(snapshot, mapping, live_booked, targets, future_quarters)
        if not fut.empty:
            out = pd.concat([out, fut], ignore_index=True)

    targets_join = targets.rename(columns={"deal_type": "Deal_Type"})[
        ["quarter", "geo", "Deal_Type", "target_usd"]
    ]
    out = out.merge(targets_join, on=["quarter", "geo", "Deal_Type"], how="left")

    out["ltb"] = out["target_usd"] - out["booked"]
    safe_ltb = out["ltb"].where(out["ltb"] > 0)
    out["total_cov"] = out["open_pipe"] / safe_ltb
    out["ls_cov"] = out["ls_pipe"] / safe_ltb
    out.loc[out["ltb"] <= 0, ["total_cov", "ls_cov"]] = np.nan

    out["fiscal_year"] = out["quarter"].str.extract(r"(FY\d{2})")[0]

    cols = [
        "fiscal_year", "quarter", "geo", "Deal_Type",
        "week_of_quarter",
        "open_pipe", "ls_pipe", "booked",
        "target_usd", "ltb", "total_cov", "ls_cov",
    ]
    return (
        out[cols]
        .rename(columns={"Deal_Type": "deal_type"})
        .reset_index(drop=True)
    )


def _canon_segment(s: pd.Series) -> pd.Series:
    """Canonicalize the snapshot's `Quarter_Start_Segment` to `Tier 1/2/3`.

    Handles title/lower-case drift ('tier 1' → 'Tier 1') and leaves missing
    values as NA so they drop out of the segment grouping.
    """
    return s.astype("string").str.strip().str.title()


def _future_standing_segment_frame(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    segment_targets: pd.DataFrame,
    future_quarters: set[str],
    live_booked: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One week-1 'current standing' row per (future quarter, geo, segment).

    Mirrors `_future_standing_frame` at geo × segment grain: open / late-stage
    pipe from the latest snapshot for deals close-dated in the future quarter;
    booked = total live Closed-Won so far (the live pull carries the same
    initial-tier `Quarter_Start_Segment`), falling back to snapshot Closed-Won
    when no live frame is supplied. Every segment target combo is zero-filled
    so the quarter's full target still flows through.
    """
    cols = ["quarter", "geo", "segment", "week_of_quarter",
            "open_pipe", "ls_pipe", "booked"]
    if not future_quarters:
        return pd.DataFrame(columns=cols)

    gc = ["quarter", "geo", "segment"]
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])
    snap["segment"] = _canon_segment(snap["Quarter_Start_Segment"])
    # Same population as the deal-type frame (geo + deal type present); opps whose
    # account carries no tier bucket to "Unassigned" so the pipe ties to page 1.
    snap = snap[snap["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
    snap["segment"] = snap["segment"].fillna("Unassigned")
    if snap.empty:
        openp = pd.DataFrame(columns=gc + ["open_pipe"])
        lsp = pd.DataFrame(columns=gc + ["ls_pipe"])
        booked = pd.DataFrame(columns=gc + ["booked"])
    else:
        cur = snap[snap["snapshot_date"] == snap["snapshot_date"].max()]
        openp = (cur[cur["Stage"] == "Open"].groupby(gc, as_index=False)["Cal_IACV"].sum()
                 .rename(columns={"Cal_IACV": "open_pipe"}))
        lsp = (cur[(cur["Stage"] == "Open") & (cur["Raw_Stage"].isin(LATE_STAGES))]
               .groupby(gc, as_index=False)["Cal_IACV"].sum()
               .rename(columns={"Cal_IACV": "ls_pipe"}))
        booked = (cur[cur["Stage"] == "Closed Won"].groupby(gc, as_index=False)["Cal_IACV"].sum()
                  .rename(columns={"Cal_IACV": "booked"}))

    # Total live booked so far for these future quarters — same source as
    # `_future_standing_frame` so the segment page ties page 1 (snapshot
    # Closed-Won above stays as the fallback when no live frame is supplied).
    if live_booked is not None and not live_booked.empty:
        lb = live_booked.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
        lb = _attach_geo(lb, mapping)
        lb["Opp_Closed_Date"] = pd.to_datetime(lb["Opp_Closed_Date"])
        lb["quarter"] = _assign_quarter(lb["Opp_Closed_Date"])
        lb = lb[lb["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
        lb["segment"] = _canon_segment(lb["Quarter_Start_Segment"]).fillna("Unassigned")
        booked = (lb.groupby(gc, as_index=False)["Product_NACV"].sum()
                  .rename(columns={"Product_NACV": "booked"}))

    tgt = segment_targets[segment_targets["quarter"].isin(future_quarters)][gc].drop_duplicates()
    combos = pd.concat([openp[gc], lsp[gc], booked[gc], tgt], ignore_index=True).drop_duplicates()
    if combos.empty:
        return pd.DataFrame(columns=cols)
    frame = (combos.merge(openp, on=gc, how="left")
                   .merge(lsp, on=gc, how="left")
                   .merge(booked, on=gc, how="left"))
    frame[["open_pipe", "ls_pipe", "booked"]] = frame[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)
    frame["week_of_quarter"] = 1
    return frame[cols]


def build_segment_coverage(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    segment_targets: pd.DataFrame,
    live_booked: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Coverage frame at GEO × Segment (account tier) grain.

    Parallel to `build_coverage` but cut by the account's INITIAL tier
    (`Quarter_Start_Segment`, earliest-quarter value — deliberate: accounts
    graduate tiers over time; Historic.xlsx shows the current tier, we keep the
    starting one) instead of deal type. Booked follows the same convention as
    the deal-type page: snapshot Closed-Won `Cal_IACV` for weeks 1–12, live
    `Product_NACV` total at week 13 (the live pull now carries the same
    initial-tier `Quarter_Start_Segment`), so week-13 bookings tie page 1 /
    Historic.xlsx to the dollar — just cut by initial tier, not current.

    One deliberate difference remains:

    - **Segment targets exist only for FY25 / FY26** (FY24's target workbook has
      no tier column). Quarters without a segment target get NaN coverage.

    Output mirrors `build_coverage` with `segment` replacing `deal_type`:
        fiscal_year, quarter, geo, segment,
        week_of_quarter,
        open_pipe, ls_pipe, booked,
        target_usd, ltb, total_cov, ls_cov
    """
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])
    snap["segment"] = _canon_segment(snap["Quarter_Start_Segment"])

    # Same close-quarter window + pre-quarter buffer as build_coverage.
    q_start_month = ((snap["CloseDate"].dt.month - 1) // 3) * 3 + 1
    q_start = pd.to_datetime({
        "year": snap["CloseDate"].dt.year, "month": q_start_month, "day": 1,
    })
    q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    snap = snap[
        (snap["snapshot_date"] >= q_start - ENTRY_BUFFER)
        & (snap["snapshot_date"] <= q_end)
    ].copy()

    q_start = q_start[snap.index]
    days_into_q = (snap["snapshot_date"] - q_start).dt.days
    snap["week_of_quarter"] = (days_into_q // 7 + 1).clip(lower=1, upper=13).astype(int)
    snap = _pin_to_week_date(snap)
    # Keep the SAME opp population as build_coverage (drop only missing geo / deal
    # type) so segment open pipe reconciles to the deal-type page to the dollar.
    # Opps whose account has no tier in account_segment_quarterly bucket to
    # "Unassigned" (carried as pipe, but no segment target → no coverage).
    snap = snap.dropna(subset=["geo", "Deal_Type"])
    snap["segment"] = snap["segment"].fillna("Unassigned")

    gc = ["quarter", "geo", "segment", "week_of_quarter"]
    open_pipe = (snap[snap["Stage"] == "Open"]
                 .groupby(gc, as_index=False)["Cal_IACV"].sum()
                 .rename(columns={"Cal_IACV": "open_pipe"}))
    ls_pipe = (snap[(snap["Stage"] == "Open") & (snap["Raw_Stage"].isin(LATE_STAGES))]
               .groupby(gc, as_index=False)["Cal_IACV"].sum()
               .rename(columns={"Cal_IACV": "ls_pipe"}))
    booked = (snap[snap["Stage"] == "Closed Won"]
              .groupby(gc, as_index=False)["Cal_IACV"].sum()
              .rename(columns={"Cal_IACV": "booked"}))

    out = open_pipe.merge(ls_pipe, on=gc, how="outer").merge(booked, on=gc, how="outer")
    out[["open_pipe", "ls_pipe", "booked"]] = out[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)

    # ── BOOKED week-13 live override — same convention as build_coverage:
    # weeks 1..12 keep snapshot Closed-Won Cal_IACV; week 13 (completed
    # quarters) shows the live Product_NACV total (ties Historic.xlsx / page 1).
    # The live pull carries the SAME earliest-quarter Quarter_Start_Segment as
    # the snapshot, so the tier cut stays "initial tier" on both sources.
    if live_booked is not None and not live_booked.empty:
        live_by_week = _live_booked_by_week(live_booked, mapping, grain="segment")
        live_wk13 = live_by_week[live_by_week["week_of_quarter"] == 13]
        out = out.merge(live_wk13, on=gc, how="outer")  # gc already includes week_of_quarter
        # Rows only the live frame supplied (e.g. the in-flight quarter's
        # not-yet-real week 13) keep open_pipe/ls_pipe NaN so coverage reads
        # "no data" rather than a spurious 0×.
        live_only_mask = out["open_pipe"].isna() & out["ls_pipe"].isna()
        wk13_mask = out["week_of_quarter"] == 13
        out.loc[wk13_mask, "booked"] = out.loc[wk13_mask, "booked_live"].fillna(0.0)
        out = out.drop(columns=["booked_live"])
        not_live_only = ~live_only_mask
        out.loc[not_live_only, "open_pipe"] = out.loc[not_live_only, "open_pipe"].fillna(0.0)
        out.loc[not_live_only, "ls_pipe"]   = out.loc[not_live_only, "ls_pipe"].fillna(0.0)
        # In-flight quarter: latest week's booked = live total so far (Sam
        # 2026-06-05) — same convention as page 1 so the pages keep tying.
        out = _override_inflight_latest_booked(
            out, live_wk13, ["quarter", "geo", "segment"])

    # Future quarters (start after run date) have no in-quarter snapshots — show
    # them as a single week-1 current-standing point (same convention as the
    # deal-type frame, but snapshot-sourced).
    run_date = pd.Timestamp.now().normalize()
    candidate_qs = set(out["quarter"].dropna()) | set(segment_targets["quarter"].dropna())
    future_quarters = {q for q in candidate_qs if _quarter_start(q) > run_date}
    if future_quarters:
        out = out[~out["quarter"].isin(future_quarters)].copy()
        fut = _future_standing_segment_frame(snapshot, mapping, segment_targets, future_quarters, live_booked)
        if not fut.empty:
            out = pd.concat([out, fut], ignore_index=True)

    out = out.sort_values(["quarter", "geo", "segment", "week_of_quarter"])

    tj = segment_targets[["quarter", "geo", "segment", "target_usd"]]
    out = out.merge(tj, on=["quarter", "geo", "segment"], how="left")

    out["ltb"] = out["target_usd"] - out["booked"]
    safe_ltb = out["ltb"].where(out["ltb"] > 0)
    out["total_cov"] = out["open_pipe"] / safe_ltb
    out["ls_cov"] = out["ls_pipe"] / safe_ltb
    out.loc[out["ltb"] <= 0, ["total_cov", "ls_cov"]] = np.nan

    out["fiscal_year"] = out["quarter"].str.extract(r"(FY\d{2})")[0]

    cols = [
        "fiscal_year", "quarter", "geo", "segment",
        "week_of_quarter",
        "open_pipe", "ls_pipe", "booked",
        "target_usd", "ltb", "total_cov", "ls_cov",
    ]
    return out[cols].reset_index(drop=True)


def compute_segment_recommendations(
    coverage_segment: pd.DataFrame,
    weeks_in_quarter: int = 13,
) -> pd.DataFrame:
    """Loose-aggregate needed coverage at each (geo, segment, week_of_quarter).

    Same loose-conversion math as `compute_recommendations`, cut by account tier
    instead of deal type:
        conversion(N) = (final_booked − booked_at_N) / open_pipe_at_N
        rec_coverage  = 1 / median(conversion across closed quarters)

    Closed quarter = the segment frame reached week 13 for that quarter (FY24
    Q1–FY26 Q1). Conversion uses only open_pipe + booked, so quarters without a
    segment target (FY24) still contribute. Includes geo="All" and/or
    segment="All" aggregate rows (dollar-weighted, same as the deal-type recs).

    Returns one row per (geo, segment, week_of_quarter) with:
        n_quarters, conversion_median/min/max, rec_coverage
    """
    empty_cols = [
        "geo", "segment", "week_of_quarter",
        "n_quarters", "conversion_median", "conversion_min", "conversion_max",
        "rec_coverage",
    ]
    if coverage_segment is None or coverage_segment.empty:
        return pd.DataFrame(columns=empty_cols)

    cov = coverage_segment.copy()
    # Closed = the SNAPSHOT reached week 13 — filter to open_pipe.notna() rows
    # first (same guard as compute_recommendations) so the live-booked wk-13
    # reindex rows don't mark the in-flight quarter closed. Without it FY26 Q2
    # entered the training set at week 9 with a bogus ~7% "conversion" (its
    # final booked isn't final), dragging every segment median down.
    snap_max = (
        cov[cov["open_pipe"].notna()]
        .groupby("quarter")["week_of_quarter"].max()
    )
    closed_quarters = snap_max[snap_max >= weeks_in_quarter].index.tolist()
    # Sparse quarters (FY24 Q1) excluded from training — same rule as the
    # deal-type and product recs (Sam 2026-06-05).
    open_weeks = (
        cov[cov["open_pipe"] > 0]
        .groupby("quarter")["week_of_quarter"].nunique()
    )
    sparse = set(open_weeks[open_weeks < SPARSE_WEEK_THRESHOLD].index)
    closed_quarters = [q for q in closed_quarters if q not in sparse]
    hist = cov[cov["quarter"].isin(closed_quarters)].copy()
    if hist.empty:
        return pd.DataFrame(columns=empty_cols)

    def _loose_for(df, group_cols, qd_group):
        agg = (df.groupby(group_cols, as_index=False)
                 .agg(open_pipe=("open_pipe", "sum"), booked=("booked", "sum")))
        final = (agg.sort_values("week_of_quarter")
                    .groupby(qd_group, as_index=False)
                    .agg(final_booked=("booked", "last")))
        agg = agg.merge(final, on=qd_group, how="left")
        safe = agg["open_pipe"].where(agg["open_pipe"] > 0)
        agg["conv"] = (agg["final_booked"] - agg["booked"]) / safe
        return agg

    per_cell = _loose_for(hist, ["quarter", "geo", "segment", "week_of_quarter"], ["quarter", "geo", "segment"])
    per_seg = _loose_for(hist, ["quarter", "segment", "week_of_quarter"], ["quarter", "segment"])
    per_seg["geo"] = "All"
    per_geo = _loose_for(hist, ["quarter", "geo", "week_of_quarter"], ["quarter", "geo"])
    per_geo["segment"] = "All"
    per_all = _loose_for(hist, ["quarter", "week_of_quarter"], ["quarter"])
    per_all["geo"] = "All"
    per_all["segment"] = "All"

    per_quarter = pd.concat([per_cell, per_seg, per_geo, per_all], ignore_index=True)
    valid = per_quarter[per_quarter["conv"].notna() & (per_quarter["conv"] >= 0)].copy()
    if valid.empty:
        return pd.DataFrame(columns=empty_cols)

    recs = (valid.groupby(["geo", "segment", "week_of_quarter"], as_index=False)
                 .agg(
                     n_quarters=("conv", "size"),
                     conversion_median=("conv", "median"),
                     conversion_min=("conv", "min"),
                     conversion_max=("conv", "max"),
                 ))
    recs["rec_coverage"] = np.where(
        recs["conversion_median"] > 0, 1.0 / recs["conversion_median"], np.nan
    )
    return recs


# Canonical snapshot products (everything else / null → "Other"). Matches the
# CASE mapping in sql/snapshot.sql plus the loader's product-target mapping.
_PRODUCT_CANONICAL = frozenset({
    "Tosca", "Testim", "Data Integrity", "Vera", "qTest",
    "LiveCompare", "NeoLoad", "Sealights", "Recurring Services",
})


def _canon_product(s: pd.Series) -> pd.Series:
    """Map the `Product`/`Family` to the canonical set; unknown / null → 'Other'.
    AI Credits / Agentic and other unmapped families roll into 'Other' (Sam,
    2026-06-22)."""
    return s.where(s.isin(_PRODUCT_CANONICAL), other="Other")


def _live_booked_product_by_share(
    live: pd.DataFrame, mapping: pd.DataFrame, shares: pd.DataFrame | None,
) -> pd.DataFrame:
    """Cumulative live booked at PRODUCT grain, attributed by the allocated-table
    product `shares` (Sam, 2026-06-22) — the SAME shares that split open pipe, so
    pipe and booked use one consistent product mix and the product page no longer
    depends on the live SKU-line `Family`. Collapses the live pull to opp-level
    booked, fans each opp's total across products by share (`_attach_product_weight`;
    opps absent from `shares` → Other), then makes it cumulative by close week.
    Σ over products == the opp-level live booked per (quarter, week), so product
    booked ties the page-1 booked to the dollar. Returns
    (quarter, product, week_of_quarter, booked_live)."""
    live = live.copy()
    live = live.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
    live = _attach_geo(live, mapping)
    live["Opp_Closed_Date"] = pd.to_datetime(live["Opp_Closed_Date"])
    live["quarter"] = _assign_quarter(live["Opp_Closed_Date"])
    live["week_of_quarter"] = _week_of_quarter(live["Opp_Closed_Date"])
    live = live.dropna(subset=["geo", "Deal_Type"])

    # Opp-level booked (sum its SKU-line dollars), then fan across products by
    # share. `Product` (first) is only a fallback label for the shares=None path.
    opp_lvl = live.groupby(
        ["Opportunity_ID", "quarter", "week_of_quarter"], as_index=False
    ).agg(Product_NACV=("Product_NACV", "sum"), Product=("Product", "first"))
    opp_lvl = _attach_product_weight(opp_lvl, shares, value_col="Product_NACV")

    gc = ["quarter", "product"]
    per_week = (opp_lvl.groupby(gc + ["week_of_quarter"], as_index=False)["w"].sum()
                .rename(columns={"w": "Product_NACV"}))
    # Reindex each (quarter, product) to all 13 weeks so cumulative is well-defined.
    groups = per_week[gc].drop_duplicates()
    weeks = pd.DataFrame({"week_of_quarter": range(1, 14)})
    grid = groups.merge(weeks, how="cross")
    per_week = grid.merge(per_week, on=gc + ["week_of_quarter"], how="left").fillna(
        {"Product_NACV": 0.0})
    per_week = per_week.sort_values(gc + ["week_of_quarter"])
    per_week["booked_live"] = per_week.groupby(gc)["Product_NACV"].cumsum()
    return per_week[gc + ["week_of_quarter", "booked_live"]]


def _product_share_map(opp_products: pd.DataFrame | None) -> pd.DataFrame | None:
    """Per-opp product MIX: one row per (Opportunity_ID, product, share), shares
    summing to 1 per opp. Drives pro-rated product open-pipe / booked (Sam,
    2026-06-08) so a multi-product opp's whole-opp Cal_IACV splits across its
    families by SKU NACV share instead of dumping entirely under the
    alphabetically-first family (the old MIN() attribution).

    Basis = POSITIVE NACV per family (`nacv` from opp_products.sql), so NPA
    credit lines can't produce negative shares. The source table
    (`trf_sku_nacv_npa_allocated`) already allocates most no-product NACV to real
    families; whatever it could NOT place stays null / No_Product_Assigned →
    canonical 'Other' and keeps its own share (Sam, 2026-06-22: do not default to
    Tosca or spread it — leave the leftover in 'Other'). Returns None when
    `opp_products` lacks the `nacv` weight column — callers then fall back to
    single attribution (whole Cal_IACV under the MIN() family).
    """
    if (opp_products is None or opp_products.empty
            or "nacv" not in opp_products.columns):
        return None
    op = opp_products[["Opportunity_ID", "Product", "nacv"]].copy()
    op["product"] = _canon_product(op["Product"])  # null / No_Product_Assigned → 'Other'
    op["pos"] = op["nacv"].clip(lower=0)
    g = op.groupby(["Opportunity_ID", "product"], as_index=False)["pos"].sum()
    tot = g.groupby("Opportunity_ID")["pos"].transform("sum")
    g = g[tot > 0].copy()
    g["share"] = g["pos"] / tot[tot > 0]
    return g[["Opportunity_ID", "product", "share"]]


def _attach_product_weight(
    snap: pd.DataFrame,
    shares: pd.DataFrame | None,
    value_col: str = "Cal_IACV",
) -> pd.DataFrame:
    """Attribute an opp-grain snapshot frame to product families and return it
    with `product` and a pro-rated weight column `w`.

    With `shares` (from `_product_share_map`): fan each opp row out to one row
    per family it carries and set `w = value_col * share`. Opps with no share
    (no SKU rows at all) keep their whole value under 'Other' (Sam, 2026-06-22:
    leave anything we can't attribute in 'Other' rather than defaulting to a real
    product). Because shares sum to 1 per opp, Σ w == Σ value_col — the product
    rows still total the page-1 figure. With `shares=None`: fall back to single
    MIN() attribution (`product = _canon_product(Product)`, `w = value_col`).
    """
    snap = snap.copy()
    if shares is None:
        snap["product"] = _canon_product(snap["Product"])
        snap["w"] = snap[value_col]
        return snap
    snap = snap.drop(columns=["product", "share"], errors="ignore").merge(
        shares, on="Opportunity_ID", how="left")
    snap["product"] = snap["product"].fillna("Other")
    snap["share"] = snap["share"].fillna(1.0)
    snap["w"] = snap[value_col] * snap["share"]
    return snap


def _future_standing_product_frame(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    product_targets: pd.DataFrame,
    future_quarters: set[str],
    live_booked: pd.DataFrame | None = None,
    opp_products: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Week-1 'current standing' row per (future quarter, product). Snapshot-
    sourced, mirroring `_future_standing_segment_frame` but with no geo. Open /
    LS pipe are pro-rated across product families (same SKU-share basis as
    `build_product_coverage`); booked comes from the live SKU-line pull."""
    cols = ["quarter", "product", "week_of_quarter",
            "open_pipe", "ls_pipe", "booked"]
    if not future_quarters:
        return pd.DataFrame(columns=cols)

    gc = ["quarter", "product"]
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])
    # Same population as the deal-type frame (geo + deal type present) so the
    # product pipe ties to the overall total.
    snap = snap[snap["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
    shares = _product_share_map(opp_products)
    if snap.empty:
        openp = pd.DataFrame(columns=gc + ["open_pipe"])
        lsp = pd.DataFrame(columns=gc + ["ls_pipe"])
        booked = pd.DataFrame(columns=gc + ["booked"])
    else:
        cur = snap[snap["snapshot_date"] == snap["snapshot_date"].max()]
        cur = _attach_product_weight(cur, shares)
        openp = (cur[cur["Stage"] == "Open"].groupby(gc, as_index=False)["w"].sum()
                 .rename(columns={"w": "open_pipe"}))
        lsp = (cur[(cur["Stage"] == "Open") & (cur["Raw_Stage"].isin(LATE_STAGES))]
               .groupby(gc, as_index=False)["w"].sum()
               .rename(columns={"w": "ls_pipe"}))
        booked = (cur[cur["Stage"] == "Closed Won"].groupby(gc, as_index=False)["w"].sum()
                  .rename(columns={"w": "booked"}))

    # Total live booked so far for these future quarters — same source as
    # `_future_standing_frame` / `_future_standing_segment_frame` so the
    # product page ties page 1 (snapshot Closed-Won above stays as the
    # fallback when no live frame is supplied).
    if live_booked is not None and not live_booked.empty:
        lb = live_booked.rename(columns={"Booking_Team_Static": "Bookings_Team_static"})
        lb = _attach_geo(lb, mapping)
        lb["Opp_Closed_Date"] = pd.to_datetime(lb["Opp_Closed_Date"])
        lb["quarter"] = _assign_quarter(lb["Opp_Closed_Date"])
        lb = lb[lb["quarter"].isin(future_quarters)].dropna(subset=["geo", "Deal_Type"])
        # Opp-level booked fanned across products by the SAME allocated shares as
        # open pipe (consistent attribution; ties the page-1 booked total).
        opp_lvl = lb.groupby(["Opportunity_ID", "quarter"], as_index=False).agg(
            Product_NACV=("Product_NACV", "sum"), Product=("Product", "first"))
        opp_lvl = _attach_product_weight(opp_lvl, shares, value_col="Product_NACV")
        booked = (opp_lvl.groupby(gc, as_index=False)["w"].sum()
                  .rename(columns={"w": "booked"}))

    tgt = product_targets[product_targets["quarter"].isin(future_quarters)][gc].drop_duplicates()
    combos = pd.concat([openp[gc], lsp[gc], booked[gc], tgt], ignore_index=True).drop_duplicates()
    if combos.empty:
        return pd.DataFrame(columns=cols)
    frame = (combos.merge(openp, on=gc, how="left")
                   .merge(lsp, on=gc, how="left")
                   .merge(booked, on=gc, how="left"))
    frame[["open_pipe", "ls_pipe", "booked"]] = frame[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)
    frame["week_of_quarter"] = 1
    return frame[cols]


def _prepare_product_snap(snapshot: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """Pinned, quarter-assigned, geo-attached opp-grain snap — the exact frame
    `build_product_coverage` aggregates from (entry-buffer window, beginning-of-
    week pin, geo + deal-type present). Shared with the allocation pressure-test
    so it sees the same rows the product page does."""
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])

    q_start_month = ((snap["CloseDate"].dt.month - 1) // 3) * 3 + 1
    q_start = pd.to_datetime({
        "year": snap["CloseDate"].dt.year, "month": q_start_month, "day": 1,
    })
    q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
    snap = snap[
        (snap["snapshot_date"] >= q_start - ENTRY_BUFFER)
        & (snap["snapshot_date"] <= q_end)
    ].copy()

    q_start = q_start[snap.index]
    days_into_q = (snap["snapshot_date"] - q_start).dt.days
    snap["week_of_quarter"] = (days_into_q // 7 + 1).clip(lower=1, upper=13).astype(int)
    snap = _pin_to_week_date(snap)
    # Same population as build_coverage so product pipe ties to the overall total.
    return snap.dropna(subset=["geo", "Deal_Type"])


def build_product_coverage(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    product_targets: pd.DataFrame,
    live_booked: pd.DataFrame | None = None,
    opp_products: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Coverage frame at PRODUCT grain (single dimension, no geo).

    Built from the snapshot exactly like `build_segment_coverage` — the page
    ties to page 1 (Σ products == page-1 total), not to any external file.

    **Open pipe, LS pipe, and weeks-1-12 booked are PRO-RATED across product
    families** (Sam, 2026-06-08) when `opp_products` (carrying the per-family
    `nacv` weight) is supplied: each opp's whole-opp snapshot `Cal_IACV` is
    split by SKU NACV share (`_product_share_map` / `_attach_product_weight`)
    instead of dumped entirely under the alphabetically-first `MIN()` family.
    That MIN() attribution badly skewed multi-product families — e.g. Sealights
    FY26 Q1 wk1 open pipe read $0.46M when its pro-rated share is ~$6.8M, and
    qTest/Tosca were under-counted while Data Integrity/Recurring Services were
    over-counted. Shares sum to 1 per opp, so the product rows still total the
    page-1 figure to the dollar. Without `opp_products`, falls back to single
    MIN() attribution.

    Week-13 / latest-week / future booked come from the live `Product_NACV`
    pull, already **SKU-LINE attributed** (one live row per opp × family,
    2026-06-07) — ties Historic.xlsx's product cut product-by-product. So
    booked is honest at every week (pro-rated snapshot for wks 1-12, live
    SKU-line at wk 13), and coverage = pipe / (target − booked) is accurate
    per product. Same opp population as the deal-type frame, so product open
    pipe reconciles to the overall total.

    Output:
        fiscal_year, quarter, product,
        week_of_quarter,
        open_pipe, ls_pipe, booked,
        target_usd, ltb, total_cov, ls_cov
    """
    snap = _prepare_product_snap(snapshot, mapping)

    # Pro-rate each opp's whole-opp Cal_IACV across its product families by SKU
    # NACV share (fans opp rows out to one per family; w = Cal_IACV × share).
    # Σ w == Σ Cal_IACV so the product rows still total page 1. Falls back to
    # single MIN() attribution when opp_products has no nacv weight.
    shares = _product_share_map(opp_products)
    snap = _attach_product_weight(snap, shares)

    gc = ["quarter", "product", "week_of_quarter"]
    open_pipe = (snap[snap["Stage"] == "Open"]
                 .groupby(gc, as_index=False)["w"].sum()
                 .rename(columns={"w": "open_pipe"}))
    ls_pipe = (snap[(snap["Stage"] == "Open") & (snap["Raw_Stage"].isin(LATE_STAGES))]
               .groupby(gc, as_index=False)["w"].sum()
               .rename(columns={"w": "ls_pipe"}))
    booked = (snap[snap["Stage"] == "Closed Won"]
              .groupby(gc, as_index=False)["w"].sum()
              .rename(columns={"w": "booked"}))

    out = open_pipe.merge(ls_pipe, on=gc, how="outer").merge(booked, on=gc, how="outer")
    out[["open_pipe", "ls_pipe", "booked"]] = out[["open_pipe", "ls_pipe", "booked"]].fillna(0.0)

    # ── BOOKED week-13 live override — same convention as build_coverage /
    # build_segment_coverage: weeks 1..12 keep snapshot Closed-Won Cal_IACV;
    # week 13 (completed quarters) shows the live Product_NACV total (ties
    # Historic.xlsx / page 1). The live pull carries the SAME opp-level
    # canonical Product as the snapshot, so the product cut matches.
    if live_booked is not None and not live_booked.empty:
        live_by_week = _live_booked_product_by_share(live_booked, mapping, shares)
        live_wk13 = live_by_week[live_by_week["week_of_quarter"] == 13]
        out = out.merge(live_wk13, on=gc, how="outer")  # gc already includes week_of_quarter
        # Rows only the live frame supplied (e.g. the in-flight quarter's
        # not-yet-real week 13) keep open_pipe/ls_pipe NaN so coverage reads
        # "no data" rather than a spurious 0×.
        live_only_mask = out["open_pipe"].isna() & out["ls_pipe"].isna()
        wk13_mask = out["week_of_quarter"] == 13
        out.loc[wk13_mask, "booked"] = out.loc[wk13_mask, "booked_live"].fillna(0.0)
        out = out.drop(columns=["booked_live"])
        not_live_only = ~live_only_mask
        out.loc[not_live_only, "open_pipe"] = out.loc[not_live_only, "open_pipe"].fillna(0.0)
        out.loc[not_live_only, "ls_pipe"]   = out.loc[not_live_only, "ls_pipe"].fillna(0.0)

    run_date = pd.Timestamp.now().normalize()
    candidate_qs = set(out["quarter"].dropna()) | set(product_targets["quarter"].dropna())
    future_quarters = {q for q in candidate_qs if _quarter_start(q) > run_date}
    if future_quarters:
        out = out[~out["quarter"].isin(future_quarters)].copy()
        fut = _future_standing_product_frame(snapshot, mapping, product_targets, future_quarters, live_booked, opp_products)
        if not fut.empty:
            out = pd.concat([out, fut], ignore_index=True)

    # ── Dense (product × week) grid per quarter. A product whose last open
    # opps die mid-quarter emits NO row from the groupbys (Sealights FY26 Q2
    # wk 9), silently dropping its target from that week's aggregate LTB —
    # the All-products KPI read 2.15× while the page-1 total read 2.00×.
    # Reindex each non-future quarter to the full product set (frame rows ∪
    # targets) at every week the quarter has data: snapshot weeks fill
    # open/ls/booked = 0 (a true zero at that pinned date); live-only weeks
    # (the in-flight quarter's not-yet-real wk 13, where every existing row
    # has open_pipe NaN) keep open/ls NaN so coverage reads "no data".
    # Target-only products get zero rows too, so the page's total target
    # equals page 1's to the dollar. (Future quarters are skipped —
    # `_future_standing_product_frame` already zero-fills every target combo.)
    hist = out[~out["quarter"].isin(future_quarters)]
    fut_rows = out[out["quarter"].isin(future_quarters)]
    grids = []
    for q, q_rows in hist.groupby("quarter"):
        prods = set(q_rows["product"]) | set(
            product_targets.loc[product_targets["quarter"] == q, "product"].dropna())
        weeks = sorted(q_rows["week_of_quarter"].unique())
        grid = pd.MultiIndex.from_product(
            [[q], sorted(prods), weeks], names=gc).to_frame(index=False)
        merged = grid.merge(q_rows, on=gc, how="left")
        snap_week = q_rows.groupby("week_of_quarter")["open_pipe"].agg(
            lambda s: s.notna().any())
        is_snap_week = merged["week_of_quarter"].map(snap_week).fillna(False).astype(bool)
        merged.loc[is_snap_week, ["open_pipe", "ls_pipe"]] = (
            merged.loc[is_snap_week, ["open_pipe", "ls_pipe"]].fillna(0.0))
        merged["booked"] = merged["booked"].fillna(0.0)
        grids.append(merged)
    if grids:
        out = pd.concat(grids + [fut_rows], ignore_index=True)

    # In-flight quarter: latest week's booked = live total so far (Sam
    # 2026-06-05) — same convention as page 1 / segment so the pages keep
    # tying. Applied AFTER the dense grid so every product has a row at the
    # current week (a live-booked product with zero standing pipe would
    # otherwise be missed).
    if live_booked is not None and not live_booked.empty:
        out = _override_inflight_latest_booked(
            out, live_wk13, ["quarter", "product"])

    out = out.sort_values(["quarter", "product", "week_of_quarter"])

    tj = product_targets[["quarter", "product", "target_usd"]]
    out = out.merge(tj, on=["quarter", "product"], how="left")

    out["ltb"] = out["target_usd"] - out["booked"]
    safe_ltb = out["ltb"].where(out["ltb"] > 0)
    out["total_cov"] = out["open_pipe"] / safe_ltb
    out["ls_cov"] = out["ls_pipe"] / safe_ltb
    out.loc[out["ltb"] <= 0, ["total_cov", "ls_cov"]] = np.nan

    out["fiscal_year"] = out["quarter"].str.extract(r"(FY\d{2})")[0]

    cols = [
        "fiscal_year", "quarter", "product",
        "week_of_quarter",
        "open_pipe", "ls_pipe", "booked",
        "target_usd", "ltb", "total_cov", "ls_cov",
    ]
    return out[cols].reset_index(drop=True)


def compute_product_recommendations(
    product_conversion: pd.DataFrame,
    weeks_in_quarter: int = 13,
) -> pd.DataFrame:
    """Loose-aggregate needed coverage at each (product, week_of_quarter),
    derived from the SKU-INCLUSIVE per-week conversion frame
    (`compute_product_conversion`) — NOT the page's single-attribution $
    frame. The MIN() attribution starved small products' pipe (Sealights'
    FY26 Q1 wk-1 conversion read 98.7% off a $465K pure-SL pipe), so
    1 ÷ median put its needed coverage at ~1.5× while its true win rate is
    ~4%; the SKU-inclusive medians give realistic needs (~7× for Sealights).
    Includes the product="All" aggregate rows the conversion frame carries
    (opp-deduped, matching the page total).

    Returns one row per (product, week_of_quarter) with:
        n_quarters, conversion_median/min/max, rec_coverage
    """
    empty_cols = [
        "product", "week_of_quarter",
        "n_quarters", "conversion_median", "conversion_min", "conversion_max",
        "rec_coverage",
    ]
    if product_conversion is None or product_conversion.empty:
        return pd.DataFrame(columns=empty_cols)

    valid = product_conversion[
        product_conversion["conversion"].notna() & (product_conversion["conversion"] >= 0)
    ].copy()
    # Sealights was freshly acquired in FY24 with negligible pipeline (~$50K in
    # FY24 Q2/Q3), so its FY24 wk-N conversions are astronomical outliers (FY24
    # Q3 wk1 ≈ 1389%) that drag the median up and crush its needed coverage to
    # ~3.8×. Exclude FY24 for Sealights ONLY (Sam 2026-06-23) — its real
    # training set is FY25+. Other products and the 'All' rollup keep FY24.
    valid = valid[~(
        (valid["product"] == "Sealights")
        & valid["quarter"].str.startswith("FY24")
    )]
    if valid.empty:
        return pd.DataFrame(columns=empty_cols)

    recs = (valid.groupby(["product", "week_of_quarter"], as_index=False)
                 .agg(
                     n_quarters=("conversion", "size"),
                     conversion_median=("conversion", "median"),
                     conversion_min=("conversion", "min"),
                     conversion_max=("conversion", "max"),
                 ))
    recs["rec_coverage"] = np.where(
        recs["conversion_median"] > 0, 1.0 / recs["conversion_median"], np.nan
    )
    return recs


def _prep_snapshot_for_recs(snapshot: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    """Apply the same preprocessing as `build_coverage` so the opp-level frame
    has `geo`, `quarter`, `week_of_quarter` and is pinned to one snapshot_date
    per ISO week. Returns one row per (Opportunity_ID, snapshot_date).
    """
    snap = _attach_geo(snapshot, mapping)
    snap["snapshot_date"] = pd.to_datetime(snap["snapshot_date"])
    snap["CloseDate"] = pd.to_datetime(snap["CloseDate"])
    snap["quarter"] = _assign_quarter(snap["CloseDate"])
    snap = snap[snap["quarter"] == _assign_quarter(snap["snapshot_date"])].copy()

    q_start_month = ((snap["CloseDate"].dt.month - 1) // 3) * 3 + 1
    q_start = pd.to_datetime({
        "year":  snap["CloseDate"].dt.year,
        "month": q_start_month,
        "day":   1,
    })
    days_into_q = (snap["snapshot_date"] - q_start).dt.days
    snap["week_of_quarter"] = (
        (days_into_q // 7 + 1).clip(lower=1, upper=13).astype(int)
    )
    # Same week-pinning convention as build_coverage (earliest in week; latest
    # for the final week of each quarter).
    snap = _pin_to_week_date(snap)
    return snap.dropna(subset=["geo", "Deal_Type"])


def compute_recommendations(
    coverage: pd.DataFrame,
    weeks_in_quarter: int = 13,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Loose-aggregate recommended coverage at each (geo, deal_type, week_of_quarter).

    The math:
        For each (quarter, geo, dt, week N):
            conversion(N) = (final_booked − booked_at_N) / open_pipe_at_N
        rec_coverage(geo, dt, N) = 1 / median(conversion across closed quarters)

    "Loose" because it credits all bookings between wk N and EOQ — including
    bookings from net-new opps that entered the funnel after wk N — against
    the wk-N standing pipe. For *operational coverage guidance* this is the
    right framing: it captures how teams historically performed (carry X pipe
    at wk N → book Y by EOQ, accounting for pipe-add in between) rather than
    asking the unrealistic strict question ("what if pipe were frozen?").

    Side effect: for short-cycle deal types (Upsell, Expansion) the
    conversion ratio can exceed 100% — interpret a `rec_coverage < 1×` as
    "this cell needs less standing pipe because new pipe lands and closes
    mid-quarter," not as a math error.

    Closed quarter = max(week_of_quarter) >= weeks_in_quarter in the
    coverage frame.

    Returns (recs_agg, recs_per_quarter):
        recs_agg: one row per (geo, deal_type, week_of_quarter) with
            n_quarters, conversion_median/min/max, rec_coverage
        recs_per_quarter: one row per (quarter, geo, deal_type, week_of_quarter)
            with open_pipe_usd, booked_at_week_usd, final_booked_usd,
            converted_usd, conv
    """
    cov = coverage.copy()
    # "Closed" = the SNAPSHOT (not the live-booked reindex) reached week 13.
    # Using `cov["open_pipe"].notna()` filters to rows the snapshot actually
    # produced, ignoring zero-fill rows the live-booked outer-merge introduced.
    # Otherwise FY26 Q2 (in-flight, currentWeek=8) gets counted as closed
    # because live_booked.reindex(...,1..13) created a row at week 13, even
    # though final_booked there is just cumulative-through-wk-8.
    snap_max = (
        cov[cov["open_pipe"].notna()]
        .groupby("quarter", observed=True)["week_of_quarter"].max()
    )
    closed_quarters = snap_max[snap_max >= weeks_in_quarter].index.tolist()
    # Sparse quarters (FY24 Q1: 2 of 13 weeks) are excluded from the training
    # set (Sam 2026-06-05; PLAN S33/S34) — their mid-quarter conversions are
    # computed against an artificial open_pipe. Same threshold as the product
    # recs and the renderer's sparse flag.
    open_weeks = (
        cov[cov["open_pipe"] > 0]
        .groupby("quarter", observed=True)["week_of_quarter"].nunique()
    )
    sparse = set(open_weeks[open_weeks < SPARSE_WEEK_THRESHOLD].index)
    closed_quarters = [q for q in closed_quarters if q not in sparse]
    hist = cov[cov["quarter"].isin(closed_quarters)].copy()

    empty_agg_cols = [
        "geo", "deal_type", "week_of_quarter",
        "n_quarters", "conversion_median", "conversion_min", "conversion_max",
        "rec_coverage",
    ]
    empty_pq_cols = [
        "quarter", "geo", "deal_type", "week_of_quarter",
        "open_pipe_usd", "booked_at_week_usd", "final_booked_usd",
        "converted_usd", "conv",
    ]
    if hist.empty:
        return (pd.DataFrame(columns=empty_agg_cols),
                pd.DataFrame(columns=empty_pq_cols))

    def _loose_for(df, group_cols, qd_group):
        """Compute loose conversion at the given aggregation level.
        `group_cols` is the per-week grouping; `qd_group` is the per-quarter
        grouping for the final_booked lookup. The returned frame has columns
        `<qd_group>, week_of_quarter, open_pipe_usd, booked_at_week_usd,
        final_booked_usd, converted_usd, conv`. """
        agg = (
            df.groupby(group_cols, as_index=False)
            .agg(open_pipe=("open_pipe", "sum"), booked=("booked", "sum"))
        )
        final = (
            agg.sort_values("week_of_quarter")
            .groupby(qd_group, as_index=False)
            .agg(final_booked=("booked", "last"))
        )
        agg = agg.merge(final, on=qd_group, how="left")
        safe = agg["open_pipe"].where(agg["open_pipe"] > 0)
        agg["conv"] = (agg["final_booked"] - agg["booked"]) / safe
        agg["converted_usd"] = agg["final_booked"] - agg["booked"]
        return agg.rename(columns={
            "open_pipe": "open_pipe_usd",
            "booked":    "booked_at_week_usd",
            "final_booked": "final_booked_usd",
        })

    per_cell = _loose_for(hist, ["quarter", "geo", "deal_type", "week_of_quarter"], ["quarter", "geo", "deal_type"])

    # "All geos" — aggregate across geos
    per_dt = _loose_for(hist, ["quarter", "deal_type", "week_of_quarter"], ["quarter", "deal_type"])
    per_dt["geo"] = "All"

    # "All deal types" — aggregate across deal types
    per_geo = _loose_for(hist, ["quarter", "geo", "week_of_quarter"], ["quarter", "geo"])
    per_geo["deal_type"] = "All"

    # "All × All" — full aggregate
    per_all = _loose_for(hist, ["quarter", "week_of_quarter"], ["quarter"])
    per_all["geo"] = "All"
    per_all["deal_type"] = "All"

    per_quarter = pd.concat([per_cell, per_dt, per_geo, per_all], ignore_index=True)
    per_quarter = per_quarter[[
        "quarter", "geo", "deal_type", "week_of_quarter",
        "open_pipe_usd", "booked_at_week_usd", "final_booked_usd",
        "converted_usd", "conv",
    ]]

    # Aggregate to recommendation — drop NaN and negative conversions
    valid = per_quarter[per_quarter["conv"].notna() & (per_quarter["conv"] >= 0)].copy()
    if valid.empty:
        recs_agg = pd.DataFrame(columns=empty_agg_cols)
    else:
        recs_agg = (
            valid.groupby(["geo", "deal_type", "week_of_quarter"], as_index=False)
            .agg(
                n_quarters=("conv", "size"),
                conversion_median=("conv", "median"),
                conversion_min=("conv", "min"),
                conversion_max=("conv", "max"),
            )
        )
        recs_agg["rec_coverage"] = np.where(
            recs_agg["conversion_median"] > 0,
            1.0 / recs_agg["conversion_median"],
            np.nan,
        )

    return recs_agg, per_quarter


def compute_product_conversion(
    snapshot: pd.DataFrame,
    mapping: pd.DataFrame,
    coverage_product: pd.DataFrame,
    weeks_in_quarter: int = 13,
) -> pd.DataFrame:
    """Loose wk-N → EOQ pipe conversion per (quarter, product, week).

    conversion(N) = (booked_eoq − booked_wkN) ÷ open_pipe_wkN, and every term is
    read straight off the **final `coverage_product` frame** — so the §02 needed
    coverage (1 ÷ median conversion) is built from the *exact same* pro-rated
    open pipe and bookings the §01 table displays.

    booked_eoq per (quarter, product) = the booking at that quarter's last week
    (week 13 for closed quarters — the live SKU-line total `build_product_coverage`
    pins there). 'All' = the per-week sum across products, matching the page total.

    Closed/non-sparse quarter scope (from the snapshot) is unchanged: in-flight
    and sparse quarters are excluded, the same as the rest of the rec engine.

    Returns one row per (quarter, product, week_of_quarter) plus product='All':
        quarter, product, week_of_quarter, open_pipe, booked, booked_eoq, conversion
    """
    snap = _prep_snapshot_for_recs(snapshot, mapping)
    open_weeks = snap[snap["Stage"] == "Open"].groupby("quarter")["week_of_quarter"].nunique()
    qmax = snap.groupby("quarter")["week_of_quarter"].max()
    closed = qmax[qmax >= weeks_in_quarter].index
    sparse = open_weeks[open_weeks < SPARSE_WEEK_THRESHOLD].index
    usable = [q for q in closed if q not in sparse]

    cols = ["quarter", "product", "week_of_quarter",
            "open_pipe", "booked", "booked_eoq", "conversion"]

    cov = coverage_product[coverage_product["quarter"].isin(usable)].copy()
    if cov.empty:
        return pd.DataFrame(columns=cols)

    per = cov[["quarter", "product", "week_of_quarter", "open_pipe", "booked"]].copy()
    # 'All' = the per-week product sum (ties the page total / other pages' glides).
    per_all = (cov.groupby(["quarter", "week_of_quarter"], as_index=False)
                  .agg(open_pipe=("open_pipe", "sum"), booked=("booked", "sum")))
    per_all["product"] = "All"
    out = pd.concat([per, per_all], ignore_index=True)

    # booked_eoq = booked at each (quarter, product)'s last week.
    last_wk = out.groupby(["quarter", "product"])["week_of_quarter"].transform("max")
    eoq = (out[out["week_of_quarter"] == last_wk][["quarter", "product", "booked"]]
           .rename(columns={"booked": "booked_eoq"}))
    out = out.merge(eoq, on=["quarter", "product"], how="left")

    safe = out["open_pipe"].where(out["open_pipe"] > 0)
    out["conversion"] = (out["booked_eoq"] - out["booked"]) / safe

    return out[cols].sort_values(["quarter", "product", "week_of_quarter"]).reset_index(drop=True)
