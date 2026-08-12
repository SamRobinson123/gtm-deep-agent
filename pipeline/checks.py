"""Internal consistency checks — verification that needs no external truth.

Established 2026-08-11: there are no verified golden output figures for this
instance of Pipe Create. So verification is process conformance, and this module
is its cheapest layer — assertions any output must satisfy on its own terms,
whatever the figures turn out to be.

    from pipeline import checks
    failures = checks.run_all(df, quarter_total=total["pipe_target"])

FAILURES ARE RETURNED, NEVER RAISED. A bare assert ends the turn with a
traceback and no number; a structured failure is something the agent can put in
front of the user, with the grain and the size of the discrepancy. Each is:

    {"check": str, "grain": str, "key": str|None, "delta": float|None, "message": str}

Every check is defensive about columns: `run_all` is given whatever frame the
caller has, and a check whose inputs are absent stays SILENT rather than
inventing a failure. A check that fires spuriously stops being run at all.

The operating rules require this before any figure is reported.
"""
from __future__ import annotations

import pandas as pd

# Day-weighting produces float dust. A check that fires on a cent gets ignored,
# and an ignored check is worse than no check.
DOLLAR_TOL = 1.0
SHARE_TOL = 1e-6


def _fail(check, message, grain="All", key=None, delta=None):
    return {"check": check, "grain": grain, "key": key,
            "delta": None if delta is None else float(delta), "message": message}


# --- 1. the parts must equal the whole ----------------------------------------

def weekly_sums_to_quarter(df, quarter_total=None, col="target_created"):
    """The allocator spreads ONE quarter figure across weeks.

    The identity holds only for a COMPLETE quarter. Mid-quarter the allocation is
    prorated to elapsed days — Q3 FY26 at 45.2% elapsed allocates 45.2% of the
    total — and that is invariant 4 working, not a defect. Asserting equality
    unconditionally makes this fire on every real in-flight run, and a check that
    cries wolf gets switched off. (It did exactly that the first time it was run
    against real output.)

    So: equality when every week is fully counted; otherwise the weaker statement
    that is still true, which is that the part cannot exceed the whole.
    """
    if quarter_total is None or col not in df.columns:
        return []
    got = float(pd.to_numeric(df[col], errors="coerce").fillna(0.0).sum())
    total = float(quarter_total)
    delta = got - total

    complete = False
    if {"days_counted", "days_in_week"}.issubset(df.columns):
        complete = bool(
            (pd.to_numeric(df["days_counted"], errors="coerce")
             == pd.to_numeric(df["days_in_week"], errors="coerce")).all())

    if complete:
        if abs(delta) <= DOLLAR_TOL:
            return []
        return [_fail("weekly_sums_to_quarter",
                      f"the quarter is complete, so weekly {col} must equal the "
                      f"quarter total: sums to ${got:,.2f} against ${total:,.2f} "
                      f"(off by ${delta:,.2f})",
                      delta=delta)]

    if delta > DOLLAR_TOL:
        return [_fail("weekly_sums_to_quarter",
                      f"weekly {col} sums to ${got:,.2f}, MORE than the whole "
                      f"quarter's ${total:,.2f} (over by ${delta:,.2f}), while weeks "
                      f"remain unelapsed — the proration is allocating too much",
                      delta=delta)]
    if got < 0:
        return [_fail("weekly_sums_to_quarter",
                      f"weekly {col} sums to a negative ${got:,.2f}", delta=got)]
    return []


# --- 2. day-weight shares ------------------------------------------------------

def shares_sum_to_one(share, complete_months=None):
    """Shares of a COMPLETED month must sum to 1.0 across its weeks.

    Note the axis. `share[week, month]` is that week's fraction OF THAT MONTH, so
    the identity runs down the weeks of a month — not across a single week's
    months, where the sum is meaningless. The v2 spec phrases this as "for every
    completed week"; it is implemented on the axis where the identity actually
    holds, and the failure names the month and the weeks so either reading finds
    what it needs.

    A month still in flight legitimately sums to less than 1.0 — that proration
    is invariant 4 working, not a defect — so only completed months are checked.
    """
    if share is None or not len(share):
        return []
    out = []
    for month in (complete_months or []):
        if month not in share.columns:
            continue
        total = float(share[month].sum())
        delta = total - 1.0
        if abs(delta) <= SHARE_TOL:
            continue
        weeks = ", ".join(str(w) for w in share.index.tolist())
        out.append(_fail(
            "shares_sum_to_one",
            f"day-weight shares for {month} sum to {total:.6f}, not 1.0 "
            f"(off by {delta:+.6f}) across weeks {weeks}",
            delta=delta))
    return out


# --- 3. the hierarchy must reconcile -------------------------------------------

def rollup_consistency(df, col="target_created"):
    """Children must sum to their parent. A discrepancy is a FINDING, reported
    with its size — never silently absorbed.

    Follows the `parent` column rather than assuming a Territory -> Region -> Geo
    ladder. The hierarchy is not always fully populated — a Territory can point
    straight at a Geo where no Region row exists — and a check that assumed the
    full ladder simply stayed silent on exactly the frames it was meant to police.
    """
    if not {"grain", "key", "parent"} <= set(df.columns) or col not in df.columns:
        return []

    kids = df[df["parent"].notna()]
    if not len(kids):
        return []
    summed = kids.groupby("parent")[col].sum()

    out = []
    for _, prow in df.iterrows():
        key = prow["key"]
        if key not in summed.index or key == prow.get("parent"):
            continue
        child_total = float(summed[key])
        delta = float(prow[col]) - child_total
        if abs(delta) <= DOLLAR_TOL:
            continue
        child_grains = sorted(set(kids[kids["parent"] == key]["grain"].astype(str)))
        out.append(_fail(
            "rollup_consistency",
            f"{prow['grain']} {key} is ${float(prow[col]):,.2f} but its "
            f"{'/'.join(child_grains)} rows sum to ${child_total:,.2f} "
            f"(off by ${delta:,.2f}). NOTE invariant 7: these rollups use "
            f"Target_Monthly.csv's own hierarchy, not the BTS mapping, so the "
            f"gap may be the two hierarchies disagreeing rather than an "
            f"arithmetic error.",
            grain=str(prow["grain"]), key=key, delta=delta))
    return out


# --- 4. the invariants that hold row by row ------------------------------------

def no_negative_targets(df, col="target_created"):
    if col not in df.columns:
        return []
    bad = df[pd.to_numeric(df[col], errors="coerce") < 0]
    if not len(bad):
        return []
    return [_fail("no_negative_targets",
                  f"{len(bad)} row(s) carry a negative {col}; the smallest is "
                  f"${float(bad[col].min()):,.2f}",
                  delta=float(bad[col].min()))]


def unstarted_weeks_are_zero(df):
    """Invariant 4: a week with no elapsed days prorates to a zero target with no
    special-casing. A non-zero target there means the proration has been "fixed"
    — which is the change the invariant exists to prevent."""
    if not {"days_counted", "target_created"}.issubset(df.columns):
        return []
    bad = df[(pd.to_numeric(df["days_counted"], errors="coerce") == 0)
             & (pd.to_numeric(df["target_created"], errors="coerce").fillna(0) != 0)]
    if not len(bad):
        return []
    weeks = ", ".join(str(w) for w in bad.get("week_of_quarter", bad.index).tolist())
    return [_fail("unstarted_weeks_are_zero",
                  f"week(s) {weeks} have days_counted == 0 but a non-zero target; "
                  f"an unstarted week must prorate to zero (invariant 4)")]


def asp_null_where_no_opps(df):
    """0% ASP reads as a real and terrible figure. Null reads as "not
    applicable", which is the truth."""
    if not {"target_opps", "target_asp"}.issubset(df.columns):
        return []
    opps = pd.to_numeric(df["target_opps"], errors="coerce").fillna(0)
    asp = pd.to_numeric(df["target_asp"], errors="coerce")
    bad = df[(opps == 0) & asp.notna()]
    if not len(bad):
        return []
    weeks = ", ".join(str(w) for w in bad.get("week_of_quarter", bad.index).tolist())
    return [_fail("asp_null_where_no_opps",
                  f"week(s) {weeks} target zero opps but carry a non-null ASP; it "
                  f"must be null, not 0.0, or it renders as a real 0% figure")]


# --- 5. actuals ----------------------------------------------------------------

def opp_counted_once(df):
    """First-seen dedup means an opp belongs to exactly one week. Counting it
    twice inflates pipe create while the totals still look plausible."""
    if not {"Opp_Id", "week_of_quarter"}.issubset(df.columns):
        return []
    per = df.groupby("Opp_Id")["week_of_quarter"].nunique()
    dupes = per[per > 1]
    if not len(dupes):
        return []
    names = ", ".join(str(o) for o in dupes.index[:5])
    more = f" (+{len(dupes) - 5} more)" if len(dupes) > 5 else ""
    return [_fail("opp_counted_once",
                  f"{len(dupes)} opp(s) appear in more than one week: {names}{more}. "
                  f"First-seen dedup means each counts exactly once.",
                  delta=float(len(dupes)))]


def run_all(df, quarter_total=None, share=None, complete_months=None):
    """Every applicable check. Returns a list of failures; empty means clean."""
    return [
        *weekly_sums_to_quarter(df, quarter_total),
        *shares_sum_to_one(share, complete_months),
        *rollup_consistency(df),
        *no_negative_targets(df),
        *unstarted_weeks_are_zero(df),
        *asp_null_where_no_opps(df),
        *opp_counted_once(df),
    ]


def render(failures) -> str:
    """One line per failure, for putting in front of a person."""
    if not failures:
        return "checks: all passed"
    lines = [f"checks: {len(failures)} FAILURE(S)"]
    for f in failures:
        where = f["grain"] + (f" {f['key']}" if f.get("key") else "")
        lines.append(f"  [{f['check']}] {where}: {f['message']}")
    return "\n".join(lines)
