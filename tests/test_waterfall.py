"""Waterfall math.

These test ARITHMETIC, not business figures. The inputs are chosen constants used
to verify that a division is a division and that quarters couple in the right
direction — they are not stand-ins for production data, and no number here is
reported as a GTM figure. The data path is verified against a real pull.
"""
from __future__ import annotations

import pandas as pd
import pytest

from agent import waterfall as w


# --- the closed form ---------------------------------------------------------

def test_yield_per_dollar_counts_only_the_in_quarter_slice():
    """Only Q0 x in-quarter rate. The later slices book in LATER quarters.

    Verified against the workbook 2026-08-11. AQ (Pipe Won) = AO + AP, and only
    AO depends on this row's S:

        AC (Q0 close)  = $S*T              <- this row's create
        AD (Q+1 close) = IF(same terr/prod, $S{r-1}*U{r-1}, 0)   <- PRIOR row's
        AO             = AC*$AM            <- so AO = S x Q0_wt x in_q_rate
        AP             = SUM(AD:AK)*$AN    <- independent of this row's S

    Crediting the later weights here would count the tail twice, since it is also
    propagated forward to reduce later quarters' gaps.
    """
    curve = pd.Series({0: 0.10, 1: 0.20, 2: 0.30, 3: 0.40})
    y = w.yield_per_dollar(curve, in_q_rate=0.70, later_rate=0.35)
    assert y == pytest.approx(0.10 * 0.70)


def test_the_sales_cycle_tail_is_not_counted_twice():
    """A quarter's later-quarter weights must reduce a LATER quarter's gap, and
    must not also inflate the creating quarter's own yield."""
    curve = pd.Series({0: 0.10, 1: 0.90})
    only_q0 = w.yield_per_dollar(curve, in_q_rate=0.50, later_rate=0.20)
    # If the Q+1 slice were credited here it would add 0.90 x 0.20 = 0.18.
    assert only_q0 == pytest.approx(0.05)
    assert only_q0 < 0.05 + 0.90 * 0.20


def test_closed_form_matches_iterative_goal_seek():
    """The replacement for Excel's GoalSeek must give the same answer.

    Excel iterates because GoalSeek is a generic 1-D solver. The relation is
    linear, so bisection and division must converge to the same S.
    """
    curve = pd.Series({0: 0.13, 1: 0.23, 2: 0.36, 3: 0.13, 4: 0.13, 5: 0.02})
    in_q, later, goal = 0.43393573125, 0.159109768125, 4_250_000.0

    def pipe_won(s):
        return s * w.yield_per_dollar(curve, in_q, later)

    lo, hi = 0.0, 1e12                       # bisection, as GoalSeek would
    for _ in range(200):
        mid = (lo + hi) / 2
        if pipe_won(mid) < goal:
            lo = mid
        else:
            hi = mid
    iterative = (lo + hi) / 2

    closed_form = goal / w.yield_per_dollar(curve, in_q, later)
    assert closed_form == pytest.approx(iterative, rel=1e-9)
    assert pipe_won(closed_form) == pytest.approx(goal, rel=1e-9)


def test_zero_yield_does_not_divide_by_zero():
    assert w.yield_per_dollar(pd.Series({0: 0.0}), 0.0, 0.0) == 0.0


# --- quarter offsets ---------------------------------------------------------

def test_quarter_index_differences_are_offsets():
    q3_26 = w.quarter_index("2026-07-01")
    assert w.quarter_index("2026-10-01") - q3_26 == 1     # Q+1
    assert w.quarter_index("2027-07-01") - q3_26 == 4     # a year later
    assert w.quarter_index("2025-07-01") - q3_26 == -4    # the floor lookback


def test_floor_looks_back_exactly_one_year():
    """Same quarter, prior year — seasonality is respected."""
    sku = pd.DataFrame({
        "create_q": [w.quarter_index("2025-07-01")] * 2 + [w.quarter_index("2025-10-01")],
        "value": [100.0, 50.0, 999.0],
        "BTS_Territory": ["T1", "T1", "T1"],
    })
    fl = w.historic_floor(sku, "2026-07-01", grain="Territory")
    assert fl["T1"] == 150.0          # Q3 FY25 only, not Q4


# --- the derivation ----------------------------------------------------------

def _tiny_sku():
    """Two decided deals per territory, enough to produce a curve and a rate."""
    q = w.quarter_index("2025-07-01")
    return pd.DataFrame({
        "Stage": [w.WON, w.LOST, w.WON, w.LOST],
        "CreateDate": pd.to_datetime(["2025-07-05"] * 4),
        "create_q": [q] * 4,
        "close_q": [q, q, q + 2, q + 2],
        "value": [400.0, 600.0, 300.0, 700.0],
        "BTS_Territory": ["T1"] * 4,
    })


def test_win_rates_split_in_quarter_from_later():
    r = w.win_rates(_tiny_sku(), grain="Territory")
    assert r.loc["T1", "in_quarter"] == pytest.approx(400 / 1000)
    assert r.loc["T1", "later"] == pytest.approx(300 / 1000)


def test_sales_cycle_weights_sums_to_one():
    """Normalised exactly — the workbook's stored vectors summed to 0.98-1.00
    because they were rounded to 2dp, quietly losing 1-2% of created pipe."""
    c = w.sales_cycle_weights(_tiny_sku(), grain="Territory")
    assert c.loc["T1"].sum() == pytest.approx(1.0)


def test_floor_binds_and_is_flagged():
    sku = _tiny_sku()
    sku = pd.concat([sku, pd.DataFrame({
        "Stage": [w.OPEN], "CreateDate": pd.to_datetime(["2025-07-05"]),
        "create_q": [w.quarter_index("2025-07-01")], "close_q": [pd.NA],
        "value": [10_000_000.0], "BTS_Territory": ["T1"],
    })], ignore_index=True)

    df = w.derive_targets(sku, pd.Series({"T1": 1.0}), ["2026-07-01"], grain="Territory")
    row = df.iloc[0]
    assert row["binding"] == "floor"
    assert row["pipe_create_target"] == row["historic_floor"] > row["required_by_gap"]


def test_quarters_are_solved_in_order_and_the_tail_reduces_the_later_gap():
    """The coupling: Q_n's created pipe matures into Q_n+1, shrinking its gap.

    Solving independently would ignore the tail and overstate every quarter after
    the first.
    """
    # Needs weight at offset 1 specifically — _tiny_sku closes at 0 and 2, so its
    # tail skips Q+1 entirely and the coupling would be invisible.
    q = w.quarter_index("2025-07-01")
    sku = pd.DataFrame({
        "Stage": [w.WON, w.LOST, w.WON, w.LOST],
        "CreateDate": pd.to_datetime(["2025-07-05"] * 4),
        "create_q": [q] * 4,
        "close_q": [q, q, q + 1, q + 1],
        "value": [400.0, 600.0, 500.0, 500.0],
        "BTS_Territory": ["T1"] * 4,
    })
    bt = pd.Series({"T1": 1_000_000.0})

    both = w.derive_targets(sku, bt, ["2026-07-01", "2026-10-01"], grain="Territory")
    q4_coupled = both[both.quarter_start == "2026-10-01"].iloc[0]
    q4_alone = w.derive_targets(sku, bt, ["2026-10-01"], grain="Territory").iloc[0]

    assert q4_coupled["sales_cycle_tail_from_earlier_quarters"] > 0
    assert q4_alone["sales_cycle_tail_from_earlier_quarters"] == 0
    assert q4_coupled["gap"] < q4_alone["gap"]


def test_existing_pipe_bookings_as_a_series_reduces_the_gap():
    """existing_pipe_bookings arrives from existing_pipe_bookings() as a Series.

    Every other test leaves it None, so the branch that consumes it was dead until
    snapshot.parquet existed. A Series must not be coerced to bool anywhere on that
    path — pandas raises rather than returning a truth value.
    """
    sku = _tiny_sku()
    bt = pd.Series({"T1": 1_000_000.0})

    without = w.derive_targets(sku, bt, ["2026-07-01"], grain="Territory").iloc[0]
    with_existing = w.derive_targets(
        sku, bt, ["2026-07-01"], grain="Territory",
        existing_pipe_bookings=pd.Series({"T1": 250_000.0}),
    ).iloc[0]

    assert with_existing["expected_from_existing_pipe"] == pytest.approx(250_000.0)
    assert with_existing["gap"] == pytest.approx(without["gap"] - 250_000.0)
    assert with_existing["pipe_create_target"] <= without["pipe_create_target"]


def test_existing_pipe_bookings_series_missing_key_falls_back_to_zero():
    """A territory absent from the Series must contribute 0, not NaN."""
    df = w.derive_targets(
        _tiny_sku(), pd.Series({"T1": 1_000_000.0}), ["2026-07-01"], grain="Territory",
        existing_pipe_bookings=pd.Series({"SOME_OTHER_TERRITORY": 999.0}),
    )
    assert df.iloc[0]["expected_from_existing_pipe"] == 0.0


def test_bookings_target_may_differ_per_quarter():
    """Q3 and Q4 carry different bookings targets; one Series for both understates
    whichever quarter is not qs[0]. A mapping keyed by quarter start expresses it."""
    sku = _tiny_sku()
    per_q = {
        "2026-07-01": pd.Series({"T1": 1_000_000.0}),
        "2026-10-01": pd.Series({"T1": 4_000_000.0}),
    }
    df = w.derive_targets(sku, per_q, ["2026-07-01", "2026-10-01"], grain="Territory")

    q3 = df[df.quarter_start == "2026-07-01"].iloc[0]
    q4 = df[df.quarter_start == "2026-10-01"].iloc[0]
    assert q3["bookings_target"] == pytest.approx(1_000_000.0)
    assert q4["bookings_target"] == pytest.approx(4_000_000.0)


def test_a_bare_series_bookings_target_still_applies_to_every_quarter():
    """Backward compatibility: the single-Series form must keep its meaning."""
    df = w.derive_targets(_tiny_sku(), pd.Series({"T1": 1_000_000.0}),
                          ["2026-07-01", "2026-10-01"], grain="Territory")
    assert df["bookings_target"].tolist() == pytest.approx([1_000_000.0] * len(df))


def test_existing_pipe_bookings_may_also_differ_per_quarter():
    sku = _tiny_sku()
    df = w.derive_targets(
        sku, pd.Series({"T1": 1_000_000.0}), ["2026-07-01", "2026-10-01"],
        grain="Territory",
        existing_pipe_bookings={
            "2026-07-01": pd.Series({"T1": 100_000.0}),
            "2026-10-01": pd.Series({"T1": 300_000.0}),
        },
    )
    got = dict(zip(df.quarter_start, df.expected_from_existing_pipe))
    assert got["2026-07-01"] == pytest.approx(100_000.0)
    assert got["2026-10-01"] == pytest.approx(300_000.0)


def test_closed_won_reduces_the_gap_it_does_not_need_creating_again():
    """An in-flight quarter has already banked bookings. Those are not a gap for
    pipe create to fill; leaving them out asks create to cover the whole target."""
    sku = _tiny_sku()
    bt = pd.Series({"T1": 1_000_000.0})

    without = w.derive_targets(sku, bt, ["2026-07-01"], grain="Territory").iloc[0]
    with_won = w.derive_targets(sku, bt, ["2026-07-01"], grain="Territory",
                                closed_won=pd.Series({"T1": 400_000.0})).iloc[0]

    assert with_won["closed_won"] == pytest.approx(400_000.0)
    assert with_won["gap"] == pytest.approx(without["gap"] - 400_000.0)
    assert with_won["required_by_gap"] < without["required_by_gap"]


def test_closed_won_may_differ_per_quarter():
    df = w.derive_targets(
        _tiny_sku(), pd.Series({"T1": 1_000_000.0}), ["2026-07-01", "2026-10-01"],
        grain="Territory",
        closed_won={"2026-07-01": pd.Series({"T1": 400_000.0}),
                    "2026-10-01": pd.Series({"T1": 0.0})},
    )
    got = dict(zip(df.quarter_start, df.closed_won))
    assert got["2026-07-01"] == pytest.approx(400_000.0)
    assert got["2026-10-01"] == pytest.approx(0.0)


def test_equivalent_point_lands_the_same_distance_into_the_prior_quarter():
    """Mid-quarter, slip must be measured like for like: W7-to-quarter-end last
    year, not the whole quarter. A full-quarter measurement overstates how much
    can still slip when half the quarter is already gone."""
    # 2026-08-11 is 41 days into Q3 FY26 (starts 2026-07-01).
    got = w.equivalent_point("2026-07-01", "2026-08-11", "2025-07-01")
    assert got == pd.Timestamp("2025-08-11")
    assert (got - pd.Timestamp("2025-07-01")).days == 41


def test_equivalent_point_at_quarter_start_is_the_prior_quarter_start():
    assert w.equivalent_point("2026-07-01", "2026-07-01", "2025-07-01") == pd.Timestamp("2025-07-01")


def test_summary_reports_how_much_is_floor_driven():
    df = w.derive_targets(_tiny_sku(), pd.Series({"T1": 1_000_000.0}),
                          ["2026-07-01"], grain="Territory")
    s = w.summarize(df)
    assert "floor_driven_pct" in s
    # Omitting existing pipe overstates the required create; it must be flagged.
    assert s["existing_pipe_supplied"] is False


def test_missing_parquet_says_what_to_do():
    with pytest.raises(w.MissingData, match="run_pull"):
        w._require("definitely_not_pulled.parquet")


# --- outlier flags and overrides ---------------------------------------------

def _two_territories():
    """T1 healthy; T2 wins almost nothing in-quarter — the Public Sector shape."""
    q = w.quarter_index("2025-07-01")
    rows = []
    for terr, won_in_q in (("T1", 500.0), ("T2", 30.0)):
        rows += [
            {"Stage": w.WON,  "close_q": q,     "value": won_in_q,          "BTS_Territory": terr},
            {"Stage": w.LOST, "close_q": q,     "value": 1000.0 - won_in_q, "BTS_Territory": terr},
            {"Stage": w.WON,  "close_q": q + 2, "value": 300.0,             "BTS_Territory": terr},
            {"Stage": w.LOST, "close_q": q + 2, "value": 700.0,             "BTS_Territory": terr},
        ]
    d = pd.DataFrame(rows)
    d["CreateDate"] = pd.to_datetime("2025-07-05")
    d["create_q"] = q
    return d


def test_a_low_in_quarter_win_rate_is_flagged_with_its_driver():
    """A target inflated by a 3% win rate must not look like one the bookings
    number demanded. The flag names the divisor so it can be challenged."""
    df = w.derive_targets(_two_territories(), pd.Series({"T1": 1e6, "T2": 1e6}),
                          ["2026-07-01"], grain="Territory")
    flagged = w.flag_outliers(df, "Territory").set_index("Territory")

    assert "low_in_q_win_rate" in flagged.loc["T2", "outlier_flags"]
    assert "low_in_q_win_rate" not in flagged.loc["T1", "outlier_flags"]
    assert "divisor" in flagged.loc["T2", "outlier_reasons"]


def test_zero_existing_pipe_is_flagged():
    """The AMS Corporate shape: a bookings target with no open pipe behind it."""
    df = w.derive_targets(_two_territories(), pd.Series({"T1": 1e6, "T2": 1e6}),
                          ["2026-07-01"], grain="Territory",
                          existing_pipe_bookings=pd.Series({"T1": 200_000.0, "T2": 0.0}))
    flagged = w.flag_outliers(df, "Territory").set_index("Territory")

    assert "no_existing_pipe" in flagged.loc["T2", "outlier_flags"]
    assert "no_existing_pipe" not in flagged.loc["T1", "outlier_flags"]


def test_overriding_a_win_rate_lowers_that_territorys_target_only():
    """'I do not believe 3%, I think 40%' — what does the target become?

    The override must flow through the yield, and must not touch anyone else.
    """
    sku, bt = _two_territories(), pd.Series({"T1": 1e6, "T2": 1e6})
    base = w.derive_targets(sku, bt, ["2026-07-01"], grain="Territory").set_index("Territory")
    what_if = w.derive_targets(sku, bt, ["2026-07-01"], grain="Territory",
                               overrides={"T2": {"in_quarter_win_rate": 0.40}}
                               ).set_index("Territory")

    assert what_if.loc["T2", "in_quarter_win_rate"] == pytest.approx(0.40)
    assert what_if.loc["T2", "required_by_gap"] < base.loc["T2", "required_by_gap"]
    assert what_if.loc["T1", "required_by_gap"] == pytest.approx(base.loc["T1", "required_by_gap"])
    assert what_if.loc["T2", "overridden"] == "in_quarter_win_rate"
    assert base.loc["T2", "overridden"] == ""


def test_an_override_can_target_one_quarter():
    sku, bt = _two_territories(), pd.Series({"T1": 1e6, "T2": 1e6})
    df = w.derive_targets(sku, bt, ["2026-07-01", "2026-10-01"], grain="Territory",
                          overrides={"2026-10-01": {"T2": {"in_quarter_win_rate": 0.40}}})
    got = df.set_index(["quarter_start", "Territory"])["in_quarter_win_rate"]
    assert got[("2026-10-01", "T2")] == pytest.approx(0.40)
    assert got[("2026-07-01", "T2")] != pytest.approx(0.40)


# --- column naming guard ------------------------------------------------------

def test_no_emitted_column_shadows_a_pandas_attribute():
    """A column named after a DataFrame attribute is silently unreachable.

    `df.flags` returns pandas' own Flags object, not the column, so
    `(df.flags != "")` compares an object to a string and yields a bare bool.
    Nothing raises — the filter just quietly does the wrong thing. This caught
    `flags` (now `outlier_flags`); it exists to catch the next one.
    """
    reserved = set(dir(pd.DataFrame))
    sku = _two_territories()
    frames = {
        "derive_targets": w.derive_targets(
            sku, pd.Series({"T1": 1e6, "T2": 1e6}), ["2026-07-01"], grain="Territory"),
        "win_rates": w.win_rates(sku, grain="Territory"),
        "sales_cycle_weights": w.sales_cycle_weights(sku, grain="Territory"),
    }
    frames["flag_outliers"] = w.flag_outliers(frames["derive_targets"], "Territory")

    for name, df in frames.items():
        clashes = [c for c in df.columns if isinstance(c, str) and c in reserved]
        assert not clashes, f"{name} emits column(s) shadowing pandas attributes: {clashes}"
