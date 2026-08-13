"""Tests for build_dashboard_data live_tables/prior_tables parameters."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.dashboard_data import _build_all_quarter_tables, build_dashboard_data
from tests.fixtures.build_tables_fixture import (
    FIXTURE_DEAL_TYPE_TARGETS_M,
    FIXTURE_PRODUCT_TARGETS_M,
    FIXTURE_TARGETS_M,
    make_fixture_df,
)


@pytest.fixture
def df():
    return make_fixture_df()


def _minimal_kwargs(fy: int = 2026, quarter: int = 2) -> dict:
    as_of = pd.Timestamp("2026-05-18")
    return {
        "fy": fy,
        "current_quarter": quarter,
        "as_of": as_of,
        "live_date": as_of,
        "prior_date": as_of - pd.Timedelta(days=7),
        "run_at_utc": pd.Timestamp("2026-05-18 12:00:00", tz="UTC"),
        "targets": FIXTURE_TARGETS_M,
        "product_targets": FIXTURE_PRODUCT_TARGETS_M,
        "deal_type_targets": FIXTURE_DEAL_TYPE_TARGETS_M,
        "live_is_nearest": False,
        "prior_is_nearest": False,
    }


def test_helper_returns_all_four_quarters(df):
    tables = _build_all_quarter_tables(
        df,
        fy=2026,
        targets=FIXTURE_TARGETS_M,
        product_targets=FIXTURE_PRODUCT_TARGETS_M,
        deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M,
    )
    assert set(tables.keys()) == {1, 2, 3, 4}
    for by_type in tables.values():
        assert set(by_type.keys()) == {"quarterly", "product", "deal_type"}
        for t in by_type.values():
            assert isinstance(t, pd.DataFrame)


def test_passing_tables_matches_building_internally(df):
    prior_empty = df.iloc[0:0].copy()
    kwargs = _minimal_kwargs()

    payload_a = build_dashboard_data(df, prior_empty, **kwargs)

    live_tables = _build_all_quarter_tables(
        df,
        fy=2026,
        targets=FIXTURE_TARGETS_M,
        product_targets=FIXTURE_PRODUCT_TARGETS_M,
        deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M,
    )
    payload_b = build_dashboard_data(
        df,
        prior_empty,
        live_tables=live_tables,
        prior_tables=None,
        **kwargs,
    )

    assert json.dumps(payload_a, sort_keys=True, default=str) == json.dumps(
        payload_b, sort_keys=True, default=str
    )
