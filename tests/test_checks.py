"""Internal consistency checks — the verification layer that needs no truth.

v2 establishes that there are no golden output numbers for this model, so
verification is process conformance. `checks.run_all` is the cheapest layer:
assertions any output must satisfy on its own terms, whatever the figures are.

Failures are RETURNED, never raised. A bare assert kills the run and tells the
agent nothing it can report; a structured failure is something it can put in
front of the user with the grain and the size of the discrepancy.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pipeline import checks


def weekly(rows):
    """A minimal weekly-target frame in the shape weekly_target_rows() emits."""
    return pd.DataFrame(rows, columns=[
        "week_of_quarter", "week_start", "week_end", "days_in_week",
        "days_counted", "grain", "key", "target_created", "target_opps", "target_asp"])


def clean_frame():
    return weekly([
        [1, "2026-07-01", "2026-07-05", 5, 5, "All", "All", 400.0, 4.0, 100.0],
        [2, "2026-07-06", "2026-07-12", 7, 7, "All", "All", 600.0, 6.0, 100.0],
    ])


def failures_of(result, name):
    return [f for f in result if f["check"] == name]


# --- the structured-failure contract ------------------------------------------

def test_failures_are_returned_not_raised():
    """The agent has to be able to REPORT a failure. A raised assertion ends the
    turn with a traceback and no number, which is strictly worse than a number
    carrying a stated caveat."""
    bad = clean_frame()
    bad.loc[0, "target_created"] = -5.0
    result = checks.run_all(bad)
    assert isinstance(result, list)
    assert all(isinstance(f, dict) for f in result)


def test_every_failure_carries_check_grain_delta_and_message():
    bad = clean_frame()
    bad.loc[0, "target_created"] = -5.0
    for f in checks.run_all(bad):
        assert set(f) >= {"check", "grain", "delta", "message"}
        assert f["message"], "a failure with no message cannot be acted on"


def test_a_clean_frame_produces_no_failures():
    assert checks.run_all(clean_frame(), quarter_total=1000.0) == []


# --- 11. weeks must sum to the quarter ----------------------------------------

def test_weeks_not_summing_to_the_quarter_total_is_a_failure():
    """Spec test 11. In clean_frame() every week is fully counted, so the quarter
    is complete and the identity must hold exactly."""
    df = clean_frame()
    result = checks.run_all(df, quarter_total=1200.0)   # frame sums to 1000
    f = failures_of(result, "weekly_sums_to_quarter")
    assert f, "a 200 discrepancy must be reported"
    assert f[0]["delta"] == pytest.approx(-200.0)


def test_rounding_within_a_dollar_is_tolerated():
    """Day-weighting produces float dust. A check that fires on $0.01 gets
    ignored, and an ignored check is worse than no check."""
    df = clean_frame()
    assert not failures_of(checks.run_all(df, quarter_total=1000.004),
                           "weekly_sums_to_quarter")


def test_a_mid_quarter_frame_summing_to_less_than_the_total_is_not_a_failure():
    """The regression that matters. Mid-quarter the allocator prorates to elapsed
    days — Q3 FY26 at 45.2% elapsed allocates 45.2% of the total, which is
    invariant 4 working. The first version of this check asserted equality
    unconditionally and fired on real output immediately; a check that cries wolf
    on every in-flight run gets switched off."""
    df = clean_frame()
    df.loc[1, ["days_counted", "target_created"]] = [0, 0.0]   # week 2 unstarted
    df.loc[1, ["target_opps", "target_asp"]] = [0.0, float("nan")]
    assert failures_of(checks.run_all(df, quarter_total=1000.0),
                       "weekly_sums_to_quarter") == []


def test_allocating_more_than_the_whole_quarter_is_still_a_failure():
    """The weaker mid-quarter statement that remains true: the part cannot
    exceed the whole, however little of the quarter has elapsed."""
    df = clean_frame()
    df.loc[1, "days_counted"] = 0
    f = failures_of(checks.run_all(df, quarter_total=500.0), "weekly_sums_to_quarter")
    assert f and f[0]["delta"] == pytest.approx(500.0)


# --- 12. day-weight shares -----------------------------------------------------

def test_shares_summing_to_one_pass():
    """Spec test 12. NOTE the axis: share[week, month] is that week's fraction OF
    THAT MONTH, so the sum runs down the weeks of a month, not across a week's
    months. The spec phrases this as 'for every completed week'; implemented on
    the axis where the identity actually holds, and the failure names both."""
    share = pd.DataFrame({"M202607": [0.4, 0.6], "M202608": [0.5, 0.5]}, index=[1, 2])
    assert checks.shares_sum_to_one(share, complete_months=["M202607", "M202608"]) == []


def test_a_short_month_is_a_failure_naming_the_month_and_weeks():
    share = pd.DataFrame({"M202607": [0.4, 0.58]}, index=[1, 2])   # 0.98
    f = checks.shares_sum_to_one(share, complete_months=["M202607"])
    assert f, "0.98 must fail"
    assert f[0]["delta"] == pytest.approx(-0.02, abs=1e-9)
    assert "M202607" in f[0]["message"]
    assert "1" in f[0]["message"] and "2" in f[0]["message"], "name the weeks involved"


def test_an_incomplete_month_is_not_checked():
    """Mid-quarter, an unfinished month legitimately sums to less than 1.0 —
    that proration is invariant 4 working, not a defect."""
    share = pd.DataFrame({"M202609": [0.2, 0.1]}, index=[1, 2])
    assert checks.shares_sum_to_one(share, complete_months=[]) == []


# --- 13. rollup consistency ----------------------------------------------------

def test_a_rollup_discrepancy_is_reported_with_grain_and_delta():
    """Spec test 13. Territory sums to Region, Region to Geo, Geo to All. A
    mismatch is a FINDING, surfaced with its size — never silently absorbed."""
    rows = pd.DataFrame([
        {"grain": "Territory", "key": "AMS Core East Canada", "parent": "AMS", "target_created": 100.0},
        {"grain": "Territory", "key": "AMS Core LATAM", "parent": "AMS", "target_created": 100.0},
        {"grain": "Geo", "key": "AMS", "parent": None, "target_created": 250.0},
    ])
    f = failures_of(checks.run_all(rows), "rollup_consistency")
    assert f, "200 of children against a 250 parent must be reported"
    assert f[0]["grain"] == "Geo"
    assert f[0]["delta"] == pytest.approx(50.0)
    assert "AMS" in f[0]["message"]


def test_a_matching_rollup_passes():
    rows = pd.DataFrame([
        {"grain": "Territory", "key": "T1", "parent": "AMS", "target_created": 100.0},
        {"grain": "Territory", "key": "T2", "parent": "AMS", "target_created": 150.0},
        {"grain": "Geo", "key": "AMS", "parent": None, "target_created": 250.0},
    ])
    assert failures_of(checks.run_all(rows), "rollup_consistency") == []


def test_the_rollup_failure_carries_the_invariant_7_caveat():
    """These rollups use Target_Monthly.csv's own hierarchy, not the BTS mapping.
    A discrepancy may be the hierarchy disagreeing rather than an arithmetic
    error, and reporting it without that context sends someone hunting a bug
    that is not there."""
    rows = pd.DataFrame([
        {"grain": "Territory", "key": "T1", "parent": "AMS", "target_created": 100.0},
        {"grain": "Geo", "key": "AMS", "parent": None, "target_created": 250.0},
    ])
    f = failures_of(checks.run_all(rows), "rollup_consistency")
    assert "invariant 7" in f[0]["message"].lower() or "bts" in f[0]["message"].lower()


# --- the remaining invariants --------------------------------------------------

def test_a_negative_target_is_a_failure():
    df = clean_frame()
    df.loc[1, "target_created"] = -1.0
    assert failures_of(checks.run_all(df), "no_negative_targets")


def test_a_week_with_no_elapsed_days_must_have_a_zero_target():
    """Invariant 4: an unstarted week prorates to zero with no special-casing.
    A non-zero target there means the proration has been 'fixed'."""
    df = clean_frame()
    df.loc[1, ["days_counted", "target_created"]] = [0, 600.0]
    f = failures_of(checks.run_all(df), "unstarted_weeks_are_zero")
    assert f and "2" in f[0]["message"]


def test_a_zero_day_week_with_a_zero_target_passes():
    df = clean_frame()
    df.loc[1, ["days_counted", "target_created", "target_opps"]] = [0, 0.0, 0.0]
    df.loc[1, "target_asp"] = float("nan")
    assert failures_of(checks.run_all(df), "unstarted_weeks_are_zero") == []


def test_asp_must_be_null_not_zero_where_no_opps_are_targeted():
    """0% ASP reads as a real, terrible figure. Null reads as 'not applicable',
    which is the truth. Invariant 2 and the None-vs-zero rule in targets.py."""
    df = clean_frame()
    df.loc[1, ["days_counted", "target_created", "target_opps", "target_asp"]] = [0, 0.0, 0.0, 0.0]
    assert failures_of(checks.run_all(df), "asp_null_where_no_opps")


# --- actuals, ready before the path lands --------------------------------------

def test_an_opp_counted_in_two_weeks_is_a_failure():
    """First-seen dedup means an opp belongs to exactly one week. Counting it
    twice inflates pipe create, and the totals still look plausible."""
    actuals = pd.DataFrame([
        {"Opp_Id": "A", "week_of_quarter": 1, "value": 10.0},
        {"Opp_Id": "A", "week_of_quarter": 3, "value": 10.0},
        {"Opp_Id": "B", "week_of_quarter": 1, "value": 5.0},
    ])
    f = failures_of(checks.run_all(actuals), "opp_counted_once")
    assert f and "A" in f[0]["message"]


def test_checks_skip_what_the_frame_cannot_answer():
    """run_all takes whatever frame it is given. A check whose columns are
    absent must stay silent rather than inventing a failure — otherwise nobody
    runs it on anything."""
    assert checks.run_all(pd.DataFrame({"unrelated": [1, 2]})) == []
