"""Regression tests for build_quarterly_table, build_product_table, build_deal_type_table.

These pin the current output against a small hand-crafted fixture. Used as the
safety net for Phase 3 (deduplicate table builds). If a future refactor changes
behavior here, these tests fail loudly.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.pipeline import (
    build_deal_type_table,
    build_product_table,
    build_quarterly_table,
)
from tests.fixtures.build_tables_fixture import (
    FIXTURE_DEAL_TYPE_TARGETS_M,
    FIXTURE_PRODUCT_TARGETS_M,
    FIXTURE_TARGETS_M,
    make_fixture_df,
)

TOL = 1e-4


@pytest.fixture
def df():
    return make_fixture_df()


class TestQuarterlyTable:
    def test_columns(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        assert list(tbl.columns) == [
            "Region",
            "Team",
            "Total Pipe",
            "LS Pipe",
            "QTD Booked",
            "Target",
            "LTB",
            "Total Pipe Cov LTB",
            "LS Pipe Cov LTB",
        ]

    def test_has_core_total(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        assert "Core Total" in tbl["Region"].values

    def test_has_ams_subtotal(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        assert "AMS Total" in tbl["Region"].values

    def test_core_total_is_sum_of_leaf_regions(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        leaf_rows = tbl[~tbl["Region"].isin(["AMS Total", "EMEA Total", "Core Total"])]
        core = tbl[tbl["Region"] == "Core Total"].iloc[0]
        assert math.isclose(core["Total Pipe"], leaf_rows["Total Pipe"].sum(), rel_tol=TOL)
        assert math.isclose(core["LS Pipe"], leaf_rows["LS Pipe"].sum(), rel_tol=TOL)

    def test_ams_subtotal_sums_correctly(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        ams_total = tbl[tbl["Region"] == "AMS Total"].iloc[0]
        ams_leaves = tbl[(tbl["Region"] == "AMS") & (tbl["Team"].isin(["AMS East", "AMS West"]))]
        assert math.isclose(ams_total["Total Pipe"], ams_leaves["Total Pipe"].sum(), rel_tol=TOL)

    def test_excludes_other_quarters_and_fys(self, df):
        tbl = build_quarterly_table(df, fy=2026, quarter=2, targets=FIXTURE_TARGETS_M)
        core = tbl[tbl["Region"] == "Core Total"].iloc[0]
        # Sum of Open Q2 FY26: 1.5 + 0.8 + 1.2 + 0.9 + 0.6 + 0.7 + 1.1 + 0.25 + 0.15 = 7.2M
        assert math.isclose(core["Total Pipe"], 7.2, rel_tol=TOL)


class TestProductTable:
    def test_columns(self, df):
        tbl = build_product_table(df, fy=2026, quarter=2, product_targets=FIXTURE_PRODUCT_TARGETS_M)
        assert list(tbl.columns) == [
            "Product",
            "Geo",
            "Total Pipe",
            "LS Pipe",
            "ACV",
            "Target",
            "Total Pipe Cov",
            "LS Pipe Cov",
        ]

    def test_has_grand_total(self, df):
        tbl = build_product_table(df, fy=2026, quarter=2, product_targets=FIXTURE_PRODUCT_TARGETS_M)
        assert "Grand Total" in tbl["Product"].values

    def test_grand_total_sums_correctly(self, df):
        tbl = build_product_table(df, fy=2026, quarter=2, product_targets=FIXTURE_PRODUCT_TARGETS_M)
        product_totals = tbl[(tbl["Geo"] == "") & (tbl["Product"] != "") & (tbl["Product"] != "Grand Total")]
        grand = tbl[tbl["Product"] == "Grand Total"].iloc[0]
        assert math.isclose(grand["Total Pipe"], product_totals["Total Pipe"].sum(), rel_tol=TOL)
        assert math.isclose(grand["ACV"], product_totals["ACV"].sum(), rel_tol=TOL)

    def test_unassigned_product_appears(self, df):
        tbl = build_product_table(df, fy=2026, quarter=2, product_targets=FIXTURE_PRODUCT_TARGETS_M)
        assert "Unassigned" in tbl["Product"].values


class TestDealTypeTable:
    def test_columns(self, df):
        tbl = build_deal_type_table(df, fy=2026, quarter=2, deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M)
        assert list(tbl.columns) == [
            "Geo",
            "Type",
            "Total Pipe",
            "LS Pipe",
            "ACV",
            "Target",
            "Total Pipe Cov",
            "LS Pipe Cov",
        ]

    def test_has_total_row(self, df):
        tbl = build_deal_type_table(df, fy=2026, quarter=2, deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M)
        assert "Total" in tbl["Geo"].values

    def test_pubsec_separated_from_ams(self, df):
        tbl = build_deal_type_table(df, fy=2026, quarter=2, deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M)
        pubsec_total = tbl[(tbl["Geo"] == "Pubsec") & (tbl["Type"] == "")].iloc[0]
        # Pubsec open: 1.1M New
        assert math.isclose(pubsec_total["Total Pipe"], 1.1, rel_tol=TOL)

    def test_total_is_sum_of_geos(self, df):
        tbl = build_deal_type_table(df, fy=2026, quarter=2, deal_type_targets=FIXTURE_DEAL_TYPE_TARGETS_M)
        geo_totals = tbl[(tbl["Type"] == "") & (tbl["Geo"] != "") & (tbl["Geo"] != "Total")]
        total = tbl[tbl["Geo"] == "Total"].iloc[0]
        assert math.isclose(total["Total Pipe"], geo_totals["Total Pipe"].sum(), rel_tol=TOL)
