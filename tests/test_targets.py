"""Target allocation against REAL data/Target_Monthly.csv. No fixtures.

These turn prose invariants into executable assertions. An invariant that lives
only in a markdown file degrades; one with a test fails loudly.
"""
from __future__ import annotations

import pandas as pd
import pytest

from agent import targets
from pipeline import config

Q3 = "2026-07-01"
ANCHOR = 201_789_918  # Q3 FY26 all-Geo Pipeline target, per root CLAUDE.md


@pytest.fixture(scope="module", autouse=True)
def _require_data():
    if not config.TARGET_MONTHLY_CSV.exists():
        pytest.skip(f"missing {config.TARGET_MONTHLY_CSV}")


def test_regression_anchor():
    """If this moves and the CSV did not change, the loader is wrong."""
    assert round(targets.quarter_total(Q3)["pipe_target"]) == ANCHOR


def test_month_columns_are_derived_not_hardcoded():
    """Invariant 1."""
    assert config.month_columns(Q3) == ["M202607", "M202608", "M202609"]
    assert config.month_columns("2026-10-01") == ["M202610", "M202611", "M202612"]


def test_q3_fy26_has_fourteen_weeks_with_two_partial():
    """Invariant 3: 14 weeks, not 13, and W1/W14 are partial."""
    cal = targets.week_calendar(Q3, as_of="2026-09-30")
    meta, _ = targets.week_shares(cal)
    assert len(meta) == 14
    assert meta[1]["days_in_week"] < 7
    assert meta[14]["days_in_week"] < 7
    for w in range(2, 14):
        assert meta[w]["days_in_week"] == 7


def test_future_week_collapses_to_zero_not_special_cased():
    """Invariant 4: day-weighted proration is what makes this work."""
    rows = targets.weekly_target_rows(as_of="2026-08-10", quarter_start=Q3)
    future = rows[rows.week_of_quarter == 10].iloc[0]
    assert future["days_counted"] == 0
    assert future["target_created"] == 0.0


def test_in_flight_week_is_prorated():
    rows = targets.weekly_target_rows(as_of="2026-08-10", quarter_start=Q3)
    w6 = rows[rows.week_of_quarter == 6].iloc[0]
    w7 = rows[rows.week_of_quarter == 7].iloc[0]
    assert w7["days_counted"] == 1 and w6["days_counted"] == 7
    assert w7["target_created"] == pytest.approx(w6["target_created"] / 7, rel=1e-6)


def test_missing_team_returns_none_not_zero():
    """Invariant 9: APAC Asia AGE/SEA carry no target. None != 0% attainment."""
    cal = targets.week_calendar(Q3, as_of="2026-08-10")
    _, share = targets.week_shares(cal)
    assert targets.week_target(None, 1, share) is None
    empty = pd.Series([float("nan")] * 3, index=config.month_columns(Q3))
    assert targets.week_target(empty, 1, share) is None


def test_asp_is_derived_never_a_row():
    """Invariant 2: there is no ASP row in Target_Monthly.csv."""
    raw = config.targets_raw()
    assert "ASP" not in set(raw["Target_Type"].unique())
    t = targets.quarter_total(Q3)
    assert t["asp"] == pytest.approx(t["pipe_target"] / t["opp_target"])


def test_opp_and_asp_figures_carry_the_caveat():
    """Invariant 10 travels with the number, emitted by code not prompt."""
    assert "invariant-10-opportunities-unit" in targets.quarter_total(Q3)["caveats"]


def test_whitespace_is_stripped_from_names_and_values():
    """Invariant 8: stripping names but not values creates blank-key rows."""
    raw = config.targets_raw()
    assert all(c == c.strip() for c in raw.columns)
    teams = raw[config.TEAM_COL].dropna().unique()
    assert all(t == t.strip() for t in teams)
    assert "" not in set(teams)
