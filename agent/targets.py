"""Day-weighted Pipe Create target allocation — the offline-computable half.

Allocation logic is materialized from docs/models/pipe-create.md (`_week_calendar`,
`_week_shares`, `_week_target`). Behaviour must not drift from that doc.

What this module does NOT do: actuals. Those need the snapshot feed from Synapse
(MIN(snapshot_date) over the buffered frame). Targets are computable from
data/Target_Monthly.csv alone, which is why they ship first.

GRAIN CAVEAT — read before trusting a Region or Geo number:
    pipe_create.py rolls Territory up on `BTS_Territory` from the live Synapse
    mapping table (invariant 7). That table is not available offline, so this
    module rolls up using Target_Monthly.csv's own Geo / GeoTerritory /
    GeoSubTerritory columns instead. The two agree for Territory-grain figures
    (same source rows) but MAY DIFFER at Region and Geo grain if the CSV
    hierarchy and the BTS mapping disagree. Every function here that returns a
    non-Territory grain flags this.
"""
from __future__ import annotations

import pandas as pd

from pipeline import config

GEO_COL = "Geo"
REGION_COL = "GeoTerritory"
TERRITORY_COL = config.TEAM_COL


def week_calendar(quarter_start=None, quarter_end=None, as_of=None):
    """One row per calendar day of the quarter, tagged with week-of-quarter, home
    month, and whether the day has actually been observed yet.

    `as_of` is the latest observed date. With real data this is the max snapshot
    date; offline it defaults to today. A day after `as_of` is not counted, which
    is what makes a not-yet-started week collapse to a zero target (invariant 4).
    """
    quarter_start = quarter_start or config.QUARTER_START
    quarter_end = quarter_end or config.q_end(quarter_start)
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.today().normalize()

    cal = pd.DataFrame({"date": pd.date_range(quarter_start, quarter_end)})
    cal["week"] = cal["date"].apply(lambda d: config.quarter_week(d, quarter_start))
    cal["month"] = cal["date"].dt.strftime("M%Y%m")
    cal["counted"] = cal["date"] <= as_of
    cal["days_in_month"] = cal["date"].dt.days_in_month
    return cal


def week_shares(cal):
    """Per-week metadata plus a week x month share table, day-weighted by days
    ACTUALLY OBSERVED (not the full week length).

    One rule handles all three week states (invariant 4): a completed past week has
    days_counted == days_in_week so its share is the full-week share; the in-flight
    week is prorated down to the days seen so far; a week that has not started has 0
    observed days, so its share — and therefore its target — is 0. That is exactly
    what lets attainment collapse to null with no special-casing. Do not "fix" it.
    """
    meta = (
        cal.groupby("week")
        .agg(
            week_start=("date", "min"),
            week_end=("date", "max"),
            days_in_week=("date", "size"),
            days_counted=("counted", "sum"),
        )
        .to_dict("index")
    )
    obs = cal[cal["counted"]]
    if not len(obs):
        return meta, pd.DataFrame(index=sorted(meta))
    days = obs.groupby(["week", "month"]).size()
    denom = obs.groupby(["week", "month"])["days_in_month"].first()
    share = (days / denom).unstack(fill_value=0.0)
    share = share.reindex(sorted(meta.keys()), fill_value=0.0)
    return meta, share


def week_target(monthly, week, share):
    """sum_month monthly[month] x share[week, month].

    Returns None only when the slice has no target row at all for any month — a team
    absent from Target_Monthly.csv, e.g. APAC Asia AGE/SEA (invariant 9). A real but
    not-yet-elapsed week/month combination is 0.0, not None.

    The None-vs-zero distinction matters: None means "no target exists" and must
    never be rendered as 0% attainment; 0.0 means "target exists, none allocated to
    this week yet".
    """
    if monthly is None or monthly.isna().all():
        return None
    row = share.loc[week] if week in share.index else pd.Series(0.0, index=monthly.index)
    vals = monthly.reindex(row.index).fillna(0.0)
    return float((vals * row).sum())


def _monthly_by_grain(target_type, grain_col, quarter_start):
    """Monthly targets for one Target_Type, grouped by one grain column."""
    cols = config.month_columns(quarter_start)
    df = config.targets_raw()
    df = df[df["Target_Type"] == target_type]
    return df.groupby(grain_col)[cols].sum(min_count=1)


def targets_by_grain(quarter_start=None):
    """Monthly pipe-$ and opp-count targets at every grain.

    Returns {grain: (pipe_monthly, opp_monthly)} for 'Territory', 'Region', 'Geo',
    and 'All'. See the module docstring's GRAIN CAVEAT for Region/Geo.
    """
    quarter_start = quarter_start or config.QUARTER_START
    cols = config.month_columns(quarter_start)
    out = {}
    for grain, col in (("Territory", TERRITORY_COL), ("Region", REGION_COL), ("Geo", GEO_COL)):
        out[grain] = (
            _monthly_by_grain("Pipeline", col, quarter_start),
            _monthly_by_grain("Opportunities", col, quarter_start),
        )
    df = config.targets_raw()
    p = df[df["Target_Type"] == "Pipeline"][cols].sum()
    o = df[df["Target_Type"] == "Opportunities"][cols].sum()
    p.index = o.index = cols
    out["All"] = (p, o)
    return out


def weekly_target_rows(grain="All", key=None, quarter_start=None, as_of=None):
    """One target row per week of the quarter, at the requested grain.

    `key` selects a slice within the grain (a territory or region name); it is
    ignored for grain 'All'. Actuals are absent by design — every row carries
    targets only, so `created`/`opps` are not present at all rather than being
    reported as zero.
    """
    quarter_start = quarter_start or config.QUARTER_START
    cal = week_calendar(quarter_start, as_of=as_of)
    meta, share = week_shares(cal)

    pipe_m, opp_m = targets_by_grain(quarter_start)[grain]
    if grain == "All":
        p_row, o_row = pipe_m, opp_m
    else:
        if key is None:
            raise ValueError(f"grain {grain!r} requires a key")
        p_row = pipe_m.loc[key] if key in pipe_m.index else None
        o_row = opp_m.loc[key] if key in opp_m.index else None

    rows = []
    for w, m in meta.items():
        tc = week_target(p_row, w, share)
        to = week_target(o_row, w, share)
        rows.append(
            {
                "week_of_quarter": w,
                "week_start": m["week_start"],
                "week_end": m["week_end"],
                "days_in_week": int(m["days_in_week"]),
                "days_counted": int(m["days_counted"]),
                "grain": grain,
                "key": key if grain != "All" else "All",
                "target_created": tc,
                "target_opps": to,
                # Invariant 2: ASP is derived, never read as a row.
                # Invariant 10: this figure counts opp-product-lines. Caveat required.
                "target_asp": (tc / to) if (tc and to) else None,
            }
        )
    return pd.DataFrame(rows)


def quarter_total(quarter_start=None):
    """All-Geo quarter totals. The regression anchor: Q3 FY26 Pipeline must be
    $201,789,918, matching the figure in the root CLAUDE.md output conventions."""
    quarter_start = quarter_start or config.QUARTER_START
    p, o = targets_by_grain(quarter_start)["All"]
    pipe, opps = float(p.sum()), float(o.sum())
    return {
        "quarter": config.fq_label(quarter_start),
        "pipe_target": pipe,
        "opp_target": opps,
        "asp": pipe / opps if opps else None,
        "caveats": ["invariant-10-opportunities-unit"],
    }
