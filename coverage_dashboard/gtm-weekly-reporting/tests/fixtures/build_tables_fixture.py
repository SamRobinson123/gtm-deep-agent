"""Fixture DataFrame for build_*_table tests.

Hand-crafted to cover every branch of the build functions:
- Multiple regions in AMS (East, West) → multi-team subtotal
- Single-region case (LATAM, APAC) → no subtotal row
- Late-stage and not-late-stage opportunities
- New Customer and Existing (Expansion) deal types
- All four geo views AMS / EMEA / APAC / Pubsec
- NULL Product (becomes "Unassigned")
- Public Sector mapping (special Geo_View = "Pubsec")
- Closed Won (ACV/QTD Booked) and Open (Pipe)

Rows are raw NACV dollars; build_*_table divides by 1e6.
"""

from __future__ import annotations

import pandas as pd


def make_fixture_df() -> pd.DataFrame:
    """Return a small enriched DataFrame matching the shape pull_snapshot_data emits."""
    rows = [
        # (FY, Q, Region, Region Family, Booking_Team_Static, Geo, Geo_View, Stage, Is_LS, Product, Deal_Class, Product_NACV)
        # AMS East (multi-team region)
        (2026, 2, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", True, "Tosca", "New", 1_500_000),
        (2026, 2, "AMS", "AMS East", "AMS Core East Northeast", "AMS", "AMS", "Open", False, "qTest", "Existing", 800_000),
        (2026, 2, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Closed Won", False, "Tosca", "New", 400_000),
        # AMS West (second team in AMS)
        (2026, 2, "AMS", "AMS West", "AMS Core West Central", "AMS", "AMS", "Open", True, "NeoLoad", "Existing", 1_200_000),
        (2026, 2, "AMS", "AMS West", "AMS Core West Pacific", "AMS", "AMS", "Closed Won", False, "qTest", "New", 500_000),
        # EMEA DACH
        (2026, 2, "EMEA", "EMEA DACH", "EMEA Core Germany", "EMEA", "EMEA", "Open", False, "Tosca", "Existing", 900_000),
        (2026, 2, "EMEA", "EMEA DACH", "EMEA Core Germany", "EMEA", "EMEA", "Open", True, "Data Integrity", "New", 600_000),
        # APAC (single Region Family)
        (2026, 2, "APAC", "APAC", "APAC Japan", "APAC", "APAC", "Open", True, "Tosca", "New", 700_000),
        (2026, 2, "APAC", "APAC", "APAC Japan", "APAC", "APAC", "Closed Won", False, "LiveCompare", "Existing", 300_000),
        # Public Sector (Geo_View = "Pubsec")
        (2026, 2, "Public Sector", "Public Sector", "AMS Public Sector - FED", "AMS", "Pubsec", "Open", False, "qTest", "New", 1_100_000),
        (2026, 2, "Public Sector", "Public Sector", "AMS Public Sector - FED", "AMS", "Pubsec", "Closed Won", False, "qTest", "New", 200_000),
        # LATAM (single region family)
        (2026, 2, "LATAM", "LATAM", "AMS Core LATAM", "AMS", "AMS", "Open", False, "Tosca", "Existing", 250_000),
        # Unassigned product
        (2026, 2, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", False, "Unassigned", "New", 150_000),
        # Q1 only (for dashboard all-quarters loop; excluded from Q2 tests)
        (2026, 1, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", True, "Tosca", "New", 999_999),
        # Q3 / Q4 minimal rows (dashboard builds all four quarters)
        (2026, 3, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", True, "Tosca", "New", 100_000),
        (2026, 4, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", True, "Tosca", "New", 100_000),
        # Different FY — should be excluded
        (2025, 2, "AMS", "AMS East", "AMS Core East Canada", "AMS", "AMS", "Open", True, "Tosca", "New", 888_888),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "FY",
            "Quarter",
            "Region",
            "Region Family",
            "Booking_Team_Static",
            "Geo",
            "Geo_View",
            "Stage",
            "Is_LS",
            "Product",
            "Deal_Class",
            "Product_NACV",
        ],
    )


FIXTURE_TARGETS_M = {
    (2026, 2, "AMS East"): 3.640,
    (2026, 2, "AMS West"): 4.228,
    (2026, 2, "EMEA DACH"): 3.011,
    (2026, 2, "APAC"): 4.290,
    (2026, 2, "Public Sector"): 2.168,
    (2026, 2, "LATAM"): 0.651,
}

FIXTURE_PRODUCT_TARGETS_M = {
    (2026, 2, "AMS", "Tosca"): 6.420,
    (2026, 2, "AMS", "qTest"): 2.651,
    (2026, 2, "AMS", "NeoLoad"): 1.777,
    (2026, 2, "EMEA", "Tosca"): 5.426,
    (2026, 2, "EMEA", "Data Integrity"): 0.469,
    (2026, 2, "APAC", "Tosca"): 2.554,
    (2026, 2, "APAC", "LiveCompare"): 0.148,
}

FIXTURE_DEAL_TYPE_TARGETS_M = {
    (2026, 2, "AMS", "New"): 4.977,
    (2026, 2, "AMS", "Existing"): 7.674,
    (2026, 2, "EMEA", "New"): 4.005,
    (2026, 2, "EMEA", "Existing"): 5.667,
    (2026, 2, "APAC", "New"): 1.751,
    (2026, 2, "APAC", "Existing"): 2.356,
    (2026, 2, "Pubsec", "New"): 1.594,
}
