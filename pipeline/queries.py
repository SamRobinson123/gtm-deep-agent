"""The four named report queries — the complete set of statements the agent can
cause to run against Synapse.

THIS FILE IS THE SECURITY BOUNDARY. Reviewing the agent's warehouse capability
means reading this file. No tool accepts SQL text; the agent can only name one of
the entries in REGISTRY below.

Materialized verbatim from docs/analysis/gtm-dashboard.md (the `pipeline/pull.py`
section). Editing a template here changes what the agent can run — review
accordingly, and note that agent/sqlguard.py re-asserts read-only at call time.
"""
from __future__ import annotations

from pipeline.config import (
    EXCLUDED_STAGES,
    EXCLUDED_TEAMS,
    HIST_SNAP_END,
    HIST_SNAP_START,
    PRE_QUARTER_BUFFER_START,
    SNAP_END,
)

_TEAMS = ",".join(f"'{t}'" for t in EXCLUDED_TEAMS)
_STAGES = ",".join(f"'{s}'" for s in EXCLUDED_STAGES)

PRODUCT_CASE = """
    CASE
        WHEN N.Record_Type IN ('Service','Platinum Support')             THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')               THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                          'TTA for SNOW','TTA SNOW',
                          'Tricentis Device Cloud','Mobile')             THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI')                         THEN 'Data Integrity'
        WHEN N.Family IN ('Vera')                                        THEN 'Vera'
        WHEN N.Family IN ('qTest')                                       THEN 'qTest'
        WHEN N.Family IN ('LiveCompare')                                 THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad')                                     THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights')                         THEN 'Sealights'
        ELSE N.Family
    END
"""

SKU_SQL = f"""
SELECT
    N.Opportunity_id                                            AS Opportunity_Id,
    CASE WHEN N.Deal_Type = 'New Business' THEN 'New Customer'
         ELSE N.Deal_Type END                                   AS Deal_Type,
    CASE WHEN N.Opportunity_Source_Logic = 'Lead Sourced' THEN 'Marketing Sourced'
         ELSE N.Opportunity_Source_Logic END                    AS Source,
    N.Segment,
    {PRODUCT_CASE}                                             AS Product,
    N.NACV_USD - N.Uplift_USD                                  AS Product_NACV,
    CASE
        WHEN N.StageName IN ('Closed Deferred','Closed Lost')                          THEN 'Closed'
        WHEN N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
        WHEN N.StageName IN ('Closed - Duplicate','Stage 6 - Closed - Admin',
                             'Stage 7 - Churned','Opportunity Rejected',
                             '0 - First Interaction')                                  THEN 'Other'
        ELSE 'Open'
    END                                                        AS Stage,
    N.StageName                                                AS Raw_Stage,
    -- Create / discovery date. Required for sales cycle: the sales cycle curve is
    -- the distribution of CreateDate -> CloseDate bucketed by QUARTER OFFSET.
    -- sku_nacv_fact is product-grain, which is what makes Territory x Product
    -- curves possible — opportunity-grain data cannot produce them.
    N.Stage_1_Start_Date_Corrected                             AS CreateDate,
    N.Opp_Closed_Date                                          AS CloseDate,
    N.Booking_Team_Static
FROM [src].[sku_nacv_fact] N
WHERE N.Period                = 'Period_1'
  AND N.NACV_USD             != 0
  AND N.Record_Type          IN ('Product','Service','Platinum support')
  AND N.Deal_Type            IN ('New Business','Expansion','Upsell','Professional services')
  AND N.Booking_Team_Static  NOT IN ({_TEAMS})
  AND N.Booking_Team_Static  IS NOT NULL
  AND N.StageName            NOT IN ({_STAGES})
"""

# Snapshot pull — for the coverage curve, timepoint comparisons, and pipe create.
# Filtered on snapshot_date (the actual calendar day), not QuarterStartDate (an
# ETL-assigned tag reflecting when a row was recorded, not the deal's own CloseDate
# quarter). Range starts at the pre-quarter buffer: invariant 5 depends on it.
SNAP_SQL = f"""
SELECT
    snap.Opp_Id,
    snap.snapshot_date,
    snap.Raw_Stage,
    snap.Stage_Pipe_Category,
    snap.Cal_IACV,
    snap.Bookings_Team_static,
    snap.CloseDate,
    snap.QuarterWeek,
    snap.QuarterStartDate
FROM [rep].[trf_opp_daily_snapshot_new] snap
WHERE snap.snapshot_date     >= '{PRE_QUARTER_BUFFER_START}'
  AND snap.snapshot_date     <= '{SNAP_END}'
  AND snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ({_TEAMS})
  AND snap.Raw_Stage NOT IN ({_STAGES})
"""

# Latest-snapshot age features. No quarter filter: training spans every historical
# closed deal, so ages must reach all quarters.
AGE_SQL = """
WITH latest_snapshot AS (
    SELECT
        Opp_Id, Stage_Age, S1_Age, NextStep_Age,
        ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) AS rn
    FROM [rep].[trf_opp_daily_snapshot_new]
)
SELECT Opp_Id, Stage_Age, S1_Age, NextStep_Age
FROM latest_snapshot
WHERE rn = 1
"""

# Territory mapping — geo / region / territory come from here, never derived by CASE.
BTS_SQL = """
SELECT Bookings_Team_Static, BTS_Geo, BTS_Region, BTS_Territory, BTS_ProductFamily, BTS_RegionFamily
FROM [sharepoint].[Map_Booking_Team_Static_live]
WHERE ActiveTeam = 'Active'
"""

# name -> (sql, output parquet filename, human description)
# Same table, columns and filters as SNAP_SQL — ONLY the date window differs.
# Kept as a separate entry rather than widening SNAP_SQL because invariant 5's
# actuals anchoring depends on the in-flight window starting at the pre-quarter
# buffer, and a slip history needs a prior-year window.
SNAP_HIST_SQL = f"""
SELECT
    snap.Opp_Id,
    snap.snapshot_date,
    snap.Raw_Stage,
    snap.Stage_Pipe_Category,
    snap.Cal_IACV,
    snap.Bookings_Team_static,
    snap.CloseDate,
    snap.QuarterWeek,
    snap.QuarterStartDate
FROM [rep].[trf_opp_daily_snapshot_new] snap
WHERE snap.snapshot_date     >= '{HIST_SNAP_START}'
  AND snap.snapshot_date     <= '{HIST_SNAP_END}'
  AND snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ({_TEAMS})
  AND snap.Raw_Stage NOT IN ({_STAGES})
"""

REGISTRY = {
    "sku_nacv": (SKU_SQL, "sku_nacv.parquet", "Product-level bookings and pipeline from [src].[sku_nacv_fact]"),
    "snapshot_hist": (SNAP_HIST_SQL, "snapshot_hist.parquet",
                      f"Historic daily opp snapshots {HIST_SNAP_START}..{HIST_SNAP_END} — slip measurement only"),
    "snapshot": (SNAP_SQL, "snapshot.parquet", "Daily opp snapshots incl. pre-quarter buffer — powers pipe create and coverage"),
    "opp_ages": (AGE_SQL, "opp_ages.parquet", "Latest-snapshot age features, one row per opp"),
    "bts": (BTS_SQL, "bts.parquet", "Territory mapping — Geo/Region/Territory, active teams only"),
}

QUERY_NAMES = tuple(REGISTRY)


def get(name: str):
    """Look up a registry entry. Raises for anything not in the four."""
    if name not in REGISTRY:
        raise KeyError(f"unknown query {name!r}; the registry is {QUERY_NAMES}")
    return REGISTRY[name]
