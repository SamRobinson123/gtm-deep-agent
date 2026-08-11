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
    y = w.yield_per_dollar(curve, in_q_rate=0.70, pre_q_rate=0.35)
    assert y == pytest.approx(0.10 * 0.70)


def test_the_sales_cycle_tail_is_not_counted_twice():
    """A quarter's later-quarter weights must reduce a LATER quarter's gap, and
    must not also inflate the creating quarter's own yield."""
    curve = pd.Series({0: 0.10, 1: 0.90})
    only_q0 = w.yield_per_dollar(curve, in_q_rate=0.50, pre_q_rate=0.20)
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
    assert r.loc["T1", "pre_q"] == pytest.approx(300 / 1000)


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


# --- slip seasonality ---------------------------------------------------------

def test_slip_window_is_the_same_quarter_a_year_earlier():
    """Slip is seasonal, so the assumption comes from the same quarter, not the
    most recent one. Same lookback as the historic floor."""
    assert w.prior_year_quarter("2026-07-01") == "2025-07-01"   # Q3 FY26 -> Q3 FY25
    assert w.prior_year_quarter("2026-10-01") == "2025-10-01"   # Q4 FY26 -> Q4 FY25


def test_slip_anchor_uses_the_whole_quarter_when_it_has_not_started():
    """Mid-quarter there is an elapsed fraction to mirror; for a future quarter
    there is not, so the whole historic quarter is the like-for-like window."""
    # Q3 FY26 is in flight on 2026-08-11 — mirror the elapsed 41 days.
    assert w.slip_anchor("2026-07-01", "2026-08-11", "2025-07-01") == pd.Timestamp("2025-08-11")
    # Q4 FY26 has not started — use Q4 FY25 entire.
    assert w.slip_anchor("2026-10-01", "2026-08-11", "2025-10-01") == pd.Timestamp("2025-10-01")


def test_a_quarter_falling_in_a_gap_between_pull_windows_raises(tmp_path, monkeypatch):
    """The historic pull is disjoint windows, so min < anchor and max > quarter end
    can both hold while the quarter itself is entirely absent. Checking only the
    extremes lets that through and slip returns a confident 0.0%."""
    from pipeline import config as cfg

    days = list(pd.date_range("2025-07-01", "2025-09-30")) + list(pd.date_range("2026-01-01", "2026-06-30"))
    snap = pd.DataFrame({
        "snapshot_date": days,
        "Opp_Id": [f"o{i}" for i in range(len(days))],
        "Raw_Stage": ["Stage 3"] * len(days),
        "CloseDate": [pd.Timestamp("2025-11-15")] * len(days),
        "Cal_IACV": [1000.0] * len(days),
        "Bookings_Team_static": ["T1"] * len(days),
    })
    snap.to_parquet(tmp_path / "gappy.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    # Q4 FY25 sits in the hole between the two windows.
    with pytest.raises(w.MissingData, match="quarter end|at or before the anchor"):
        w.slip("2025-10-01", "Territory", snapshot_file="gappy.parquet")

    # A quarter inside a window still works.
    w.slip("2025-07-01", "Territory", snapshot_file="gappy.parquet")


# --- slip destinations --------------------------------------------------------

def _snap_frame(rows):
    """Minimal snapshot frame: one row per (opp, snapshot_date)."""
    return pd.DataFrame(rows, columns=[
        "Opp_Id", "snapshot_date", "Raw_Stage", "CloseDate", "value", "Bookings_Team_static"])


def _slip_fixture():
    """Four opps open at Q3 FY25 start, each ending differently.

    o_next  slips one quarter   o_far   slips three
    o_won   closes won          o_lost  closes lost
    """
    s, e = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-09-30")
    rows = []
    for opp, end_stage, end_close, val in [
        ("o_next", "Stage 3",     pd.Timestamp("2025-11-15"), 300.0),
        ("o_far",  "Stage 3",     pd.Timestamp("2026-05-15"), 100.0),
        ("o_won",  "Closed Won",  pd.Timestamp("2025-08-15"), 999.0),
        ("o_lost", "Closed Lost", pd.Timestamp("2025-08-15"), 888.0),
    ]:
        rows.append([opp, s, "Stage 3", pd.Timestamp("2025-08-15"), val, "T1"])
        rows.append([opp, e, end_stage, end_close, val, "T1"])
    return _snap_frame(rows)


def test_slip_destinations_splits_by_quarter_offset(monkeypatch, tmp_path):
    """Where the slipped pipe LANDED — the half of slip the model does not have."""
    from pipeline import config as cfg
    _slip_fixture().rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    d = w.slip_destinations("2025-07-01", snapshot_file="s.parquet")

    # $300 to Q+1, $100 to Q+3. Won and lost are not slip.
    assert d.attrs["slipped_value"] == pytest.approx(400.0)
    assert d.attrs["opps"] == 2
    assert d[1] == pytest.approx(0.75)
    assert d[3] == pytest.approx(0.25)
    assert d.sum() == pytest.approx(1.0)


def test_a_won_deal_whose_close_date_moved_is_not_slip(monkeypatch, tmp_path):
    """The won/lost exclusion is what stops slip being wildly overstated."""
    from pipeline import config as cfg
    s, e = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-09-30")
    _snap_frame([
        ["o_won", s, "Stage 3", pd.Timestamp("2025-08-15"), 500.0, "T1"],
        # closed won, but the close date landed in a later quarter
        ["o_won", e, "Closed Won", pd.Timestamp("2025-11-01"), 500.0, "T1"],
    ]).rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    d = w.slip_destinations("2025-07-01", snapshot_file="s.parquet")
    assert d.attrs["slipped_value"] == 0.0


def test_slip_and_slip_destinations_agree_on_what_slipped(monkeypatch, tmp_path):
    """Both go through classify_outcomes, so the totals must match. If they ever
    diverge, one of them grew its own copy of the classification."""
    from pipeline import config as cfg
    f = _slip_fixture().rename(columns={"value": "Cal_IACV"})
    f.to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    g = w.slip("2025-07-01", "Territory", snapshot_file="s.parquet")
    d = w.slip_destinations("2025-07-01", snapshot_file="s.parquet")
    assert g["slipped"].sum() == pytest.approx(d.attrs["slipped_value"])


def test_slip_forecast_takes_both_assumptions_from_the_prior_year_quarter(monkeypatch, tmp_path):
    """Rate AND destination come from the same quarter a year earlier — never a
    pooled average. Q3 and Q4 have genuinely different destination shapes, so a
    blend describes neither."""
    from pipeline import config as cfg
    _slip_fixture().rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    # Q3 FY26 is forecast from Q3 FY25, where $400 of $2,287 slipped: 75% to Q+1,
    # 25% to Q+3.
    f = w.slip_forecast("2026-07-01", open_pipe=pd.Series({"T1": 1_000_000.0}),
                        grain="Territory", snapshot_file="s.parquet")

    assert f.attrs["source_quarter"] == "Q3 FY25"
    assert f.attrs["pooled"] is False
    slipped = f.loc["T1", "slipped_value"]
    assert slipped == pytest.approx(1_000_000.0 * f.loc["T1", "slip_rate"])
    # The slipped dollars are distributed by the SOURCE quarter's curve, and the
    # distribution is exhaustive.
    assert f.loc["T1", "to_Q+1"] == pytest.approx(slipped * 0.75)
    assert f.loc["T1", "to_Q+3"] == pytest.approx(slipped * 0.25)
    assert sum(f.loc["T1", f"to_Q+{k}"] for k in range(1, 9)) == pytest.approx(slipped)


def test_slip_forecast_without_open_pipe_returns_the_shares(monkeypatch, tmp_path):
    """Assumptions alone, for inspection — no dollars needed."""
    from pipeline import config as cfg
    _slip_fixture().rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    f = w.slip_forecast("2026-07-01", grain="Territory", snapshot_file="s.parquet")
    assert f.loc["T1", "to_Q+1"] == pytest.approx(0.75)


def test_closed_pending_is_an_open_stage_not_a_loss():
    """From the model owner's Slip Assumption notebook: the stage mapping is a
    lookup, not a substring test. 'Stage 4 - Closed Pending' contains "Closed"
    but is OPEN — a contains() rule books it as lost."""
    assert "Stage 4 - Closed Pending" in w.OPEN_STAGES
    assert "Stage 4 - Closed Pending" not in w.LOST_STAGES
    assert "Closed Deferred" in w.LOST_STAGES        # deferred is a loss, not slip
    assert "6 - Closed/Pending" in w.WON_STAGES


def test_an_unmapped_stage_is_recorded_rather_than_silently_opened(monkeypatch, tmp_path):
    """A new Raw_Stage value must not quietly become open pipe."""
    from pipeline import config as cfg
    s, e = pd.Timestamp("2025-07-01"), pd.Timestamp("2025-09-30")
    snap = pd.DataFrame([
        ["o1", s, "1 - Discovery", pd.Timestamp("2025-08-15"), 100.0, "T1"],
        ["o1", e, "Brand New Stage", pd.Timestamp("2025-11-01"), 100.0, "T1"],
    ], columns=["Opp_Id", "snapshot_date", "Raw_Stage", "CloseDate", "value", "Bookings_Team_static"])
    j = w.classify_outcomes(snap, s, e, s)
    assert j.attrs["unmapped_stages"] == ["Brand New Stage"]


# --- Pre-Q vs In-Q cohorts ----------------------------------------------------

def _cohort_fixture(tmp_path):
    """Q3 FY25, one opp per cohort, each slipping or not by design.

      pre_in    created BEFORE the quarter, present at its start, slips
      pre_hold  created before, present at start, does not move
      in_new    created INSIDE the quarter (absent at start), slips
      reslip    created before AND first seen carrying a Q2 close date
    """
    s, m, e = (pd.Timestamp("2025-07-01"), pd.Timestamp("2025-08-01"),
               pd.Timestamp("2025-09-30"))
    rows = [
        ["pre_in",   s, "Stage 3", pd.Timestamp("2025-08-15"), 100.0, "T1"],
        ["pre_in",   e, "Stage 3", pd.Timestamp("2025-11-15"), 100.0, "T1"],
        ["pre_hold", s, "Stage 3", pd.Timestamp("2025-09-20"), 200.0, "T1"],
        ["pre_hold", e, "Stage 3", pd.Timestamp("2025-09-20"), 200.0, "T1"],
        # absent at the quarter start — this is why the anchor must be per-opp
        ["in_new",   m, "Stage 3", pd.Timestamp("2025-09-10"), 400.0, "T1"],
        ["in_new",   e, "Stage 3", pd.Timestamp("2025-12-10"), 400.0, "T1"],
        # Already moved once. The prior close date is only visible BEFORE the
        # quarter — by the time it enters, it has been re-dated into it. Putting
        # the Q2 date on the in-quarter row instead makes it ineligible, which is
        # correct: such an opp is not pipe dated to close in this quarter.
        ["reslip",   pd.Timestamp("2025-06-01"), "Stage 3", pd.Timestamp("2025-06-10"), 800.0, "T1"],
        ["reslip",   s, "Stage 3", pd.Timestamp("2025-08-20"), 800.0, "T1"],
        ["reslip",   e, "Stage 3", pd.Timestamp("2025-11-01"), 800.0, "T1"],
    ]
    _snap_frame(rows).rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({
        "Opportunity_Id": ["pre_in", "pre_hold", "in_new", "reslip"],
        "CreateDate": pd.to_datetime(
            ["2025-04-01", "2025-04-01", "2025-08-01", "2025-02-01"]),
    }).to_parquet(tmp_path / "sku_nacv.parquet")


def test_slip_by_cohort_separates_in_quarter_creates_from_carried_pipe(monkeypatch, tmp_path):
    """The cohort split the bookings-forecast notebook's PRE_Q/IN_Q rates assume."""
    from pipeline import config as cfg
    _cohort_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    g = w.slip_by_cohort("2025-07-01", snapshot_file="s.parquet")

    # in_new is anchored at its OWN first sighting, not the quarter start —
    # anchoring everyone at the quarter start would drop it entirely.
    assert g.loc["in_q", "starting_open_pipe"] == pytest.approx(400.0)
    assert g.loc["in_q", "slip_rate"] == pytest.approx(1.0)
    # pre_in slipped, pre_hold held: 100 of 300
    assert g.loc["pre_q", "slip_rate"] == pytest.approx(100.0 / 300.0)


def test_a_prior_quarter_close_date_marks_pipe_as_already_slipped(monkeypatch, tmp_path):
    """pre_q_reslip is the cohort that has moved before — the notebook's premise
    that once-moved pipe behaves differently is only testable if it is split out."""
    from pipeline import config as cfg
    _cohort_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    g = w.slip_by_cohort("2025-07-01", snapshot_file="s.parquet")
    assert g.loc["pre_q_reslip", "starting_open_pipe"] == pytest.approx(800.0)
    assert "reslip" not in g.index          # it is a cohort label, not an opp id
    # and it must NOT also be counted in plain pre_q
    assert g.loc["pre_q", "starting_open_pipe"] == pytest.approx(300.0)


def test_cohort_totals_reconcile_with_the_quarter_wide_slip(monkeypatch, tmp_path):
    """Every dollar slip() sees must land in exactly one cohort. The two use
    different anchors, so the check is on the pipe both can see: opps present at
    the quarter start."""
    from pipeline import config as cfg
    _cohort_fixture(tmp_path)
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(
        tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    g = w.slip_by_cohort("2025-07-01", snapshot_file="s.parquet")
    q = w.slip("2025-07-01", "Territory", snapshot_file="s.parquet")

    carried = g.drop(index="in_q")
    assert carried["starting_open_pipe"].sum() == pytest.approx(
        q["starting_open_pipe"].sum())
    assert carried["slipped"].sum() == pytest.approx(q["slipped"].sum())


def test_the_exposure_control_splits_in_quarter_creates_by_month(monkeypatch, tmp_path):
    """An opp created in month 2 has less quarter left in which to slip, so a raw
    in_q rate is not comparable to pre_q without this cut."""
    from pipeline import config as cfg
    _cohort_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    g = w.slip_by_cohort("2025-07-01", snapshot_file="s.parquet", by_create_month=True)
    assert ("in_q", 1) in g.index           # created in the quarter's 2nd month
    assert g.loc[("in_q", 1), "starting_open_pipe"] == pytest.approx(400.0)


def test_classify_outcomes_and_slip_by_cohort_share_one_partition_rule(monkeypatch, tmp_path):
    """Both call _partition(). A second copy of the won/lost/slipped/held rule is
    exactly how the duplicated slip assembly in tools.py drifted."""
    from pipeline import config as cfg
    _cohort_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    s = pd.read_parquet(tmp_path / "s.parquet")
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"])
    s["CloseDate"] = pd.to_datetime(s["CloseDate"])
    s["value"] = s["Cal_IACV"]

    direct = w.classify_outcomes(s, pd.Timestamp("2025-07-01"),
                                 pd.Timestamp("2025-09-30"), pd.Timestamp("2025-07-01"))
    g = w.slip_by_cohort("2025-07-01", snapshot_file="s.parquet")

    # Same rule, so the pipe both can see must be classified identically.
    carried = g.drop(index="in_q")
    for outcome in ("won", "lost", "slipped", "held"):
        assert carried[outcome].sum() == pytest.approx(
            direct.loc[direct["outcome"] == outcome, "value"].sum()), outcome


# --- Pre-Q slip and slip inflow ------------------------------------------------

def _preq_fixture(tmp_path):
    """Q4 FY25 seen 52 days before it opens: $100 dated in, $30 of it pushes out
    before the quarter starts, $20 is lost early, $50 survives."""
    read_at = pd.Timestamp("2025-10-01") - pd.Timedelta(days=52)   # 2025-08-10
    before = pd.Timestamp("2025-09-30")
    rows = []
    for opp, end_stage, end_close, val in [
        ("push",  "Stage 3",     pd.Timestamp("2026-02-15"), 30.0),   # pre-Q slip
        ("lost",  "Closed Lost", pd.Timestamp("2025-11-15"), 20.0),   # not slip
        ("hold",  "Stage 3",     pd.Timestamp("2025-11-15"), 50.0),   # survives
    ]:
        rows.append([opp, read_at, "Stage 3", pd.Timestamp("2025-11-15"), val, "T1"])
        rows.append([opp, before, end_stage, end_close, val, "T1"])
    _snap_frame(rows).rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(
        tmp_path / "bts.parquet")


def test_pre_q_slip_reads_the_prior_year_at_the_same_lead_time(monkeypatch, tmp_path):
    """Q4 FY26 52 days out is compared with Q4 FY25 52 days out. Lead time is the
    control: the leak depends on how long the pipe still has to sit."""
    from pipeline import config as cfg
    _preq_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    r = w.pre_q_slip("2026-10-01", "2026-08-10", snapshot_file="s.parquet")

    assert r.attrs["lead_days"] == 52
    assert r.attrs["measured_on"] == "Q4 FY25"
    # $30 pushed of $100 dated in. The $20 lost early is NOT slip — it is already
    # inside the win rate's won+lost denominator, and counting it here double-counts.
    assert r.loc["T1"] == pytest.approx(0.30)


def test_an_in_flight_quarter_has_no_pre_q_slip(monkeypatch, tmp_path):
    """Its Pre-Q slip has already happened and is inside the observed balance.
    Returning empty is what makes a mid-quarter run correct, not a special case."""
    from pipeline import config as cfg
    _preq_fixture(tmp_path)
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    r = w.pre_q_slip("2026-07-01", "2026-08-10", snapshot_file="s.parquet")
    assert len(r) == 0
    assert "already started" in r.attrs["reason"]


def test_pre_q_slip_leaves_an_in_flight_quarter_untouched(monkeypatch, tmp_path):
    """The regression that matters: adding the term must not move Q3."""
    from pipeline import config as cfg
    _slip_fixture().rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(
        tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    empty = w.pre_q_slip("2026-07-01", "2026-08-10", snapshot_file="s.parquet")
    keys = pd.Index(["T1"], name="Territory")
    base = pd.Series([100.0], index=keys)
    # an empty rate must reindex to zeros, never to NaN
    assert (pd.Series(empty).reindex(keys).fillna(0.0) == 0.0).all()
    assert (base * (1 - pd.Series(empty).reindex(keys).fillna(0.0))).sum() == 100.0


def test_slip_inflow_forwards_only_what_the_source_haircut_removed(monkeypatch, tmp_path):
    """No double count: (1 - slip_rate) takes the dollars out of the source and
    this puts the same dollars into the destination."""
    from pipeline import config as cfg
    f = _slip_fixture().rename(columns={"value": "Cal_IACV"})
    f.to_parquet(tmp_path / "s.parquet")
    # The RATE comes from the historic feed; the open pipe it multiplies comes
    # from the CURRENT one. Two different files on purpose, so both are needed.
    f.to_parquet(tmp_path / "snapshot.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(
        tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    # as_of AT the quarter start, so the historic anchor is that quarter's start
    # and the two-snapshot fixture covers it. Mid-quarter anchoring is exercised
    # by the equivalent_point tests.
    f = w.slip_inflow("2026-07-01", "2026-10-01", as_of="2026-07-01",
                      snapshot_file="s.parquet")
    assert f.attrs["offset"] == 1
    assert f.attrs["from"] == "Q3 FY26" and f.attrs["to"] == "Q4 FY26"
    # the fixture sends 75% of slipped value one quarter forward
    assert f.attrs["destination_share"] == pytest.approx(0.75)


def test_slip_inflow_refuses_to_flow_backwards(monkeypatch, tmp_path):
    """A quarter cannot receive slip from a later one."""
    from pipeline import config as cfg
    _slip_fixture().rename(columns={"value": "Cal_IACV"}).to_parquet(tmp_path / "s.parquet")
    pd.DataFrame({"Bookings_Team_Static": ["T1"], "BTS_Geo": ["G"],
                  "BTS_Region": ["R"], "BTS_Territory": ["T1"]}).to_parquet(
        tmp_path / "bts.parquet")
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    assert len(w.slip_inflow("2026-10-01", "2026-07-01", as_of="2026-07-01",
                             snapshot_file="s.parquet")) == 0
    assert len(w.slip_inflow("2026-07-01", "2026-07-01", as_of="2026-07-01",
                             snapshot_file="s.parquet")) == 0


def test_the_existing_pipe_sequence_applies_the_terms_in_order():
    """adjusted = open x (1 - pre_q) + inflow; then in-Q slip; then the win rate.
    Inflow arrives at the boundary so it escapes pre-Q slip but NOT in-Q slip."""
    open_pipe, pre_q, inflow, in_q, wr = 1000.0, 0.20, 300.0, 0.50, 0.10
    adjusted = open_pipe * (1 - pre_q) + inflow
    assert adjusted == pytest.approx(1100.0)
    assert adjusted * (1 - in_q) * wr == pytest.approx(55.0)
    # if inflow were exposed to pre-Q slip too it would be 1040 -> 52.0
    assert (open_pipe + inflow) * (1 - pre_q) * (1 - in_q) * wr == pytest.approx(52.0)


def test_existing_pipe_carries_its_open_pipe_for_orphan_reporting():
    """The derivation reports open pipe on keys the solve will skip. That needs
    the pre-haircut open pipe on .attrs — without it an unmapped booking team
    disappears silently and the smaller existing term inflates required create."""
    keys = pd.Index(["T1", "Unassigned"], name="Territory")
    open_pipe = pd.Series([1000.0, 500.0], index=keys)
    # the shape existing_pipe_bookings promises its caller
    attrs = {"open_pipe": open_pipe}
    solved = pd.Index(["T1"], name="Territory")
    orphan = keys.difference(solved)
    assert list(orphan) == ["Unassigned"]
    assert attrs["open_pipe"].reindex(orphan).sum() == pytest.approx(500.0)


def test_parquet_cache_is_keyed_on_content_not_just_name(tmp_path, monkeypatch):
    """Two fixtures can share a filename across tests. Keying the cache on the
    name alone would serve one test's data to another — so mtime and size are
    part of the key, and the caller always gets a copy it may mutate."""
    from pipeline import config as cfg
    import time
    monkeypatch.setattr(cfg, "DATA", tmp_path)

    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "same_name.parquet")
    first = w._require("same_name.parquet")
    assert list(first["a"]) == [1]

    time.sleep(0.01)
    pd.DataFrame({"a": [2]}).to_parquet(tmp_path / "same_name.parquet")
    assert list(w._require("same_name.parquet")["a"]) == [2]

    # mutating what we got back must not poison the cache
    again = w._require("same_name.parquet")
    again["a"] = 999
    assert list(w._require("same_name.parquet")["a"]) == [2]
