"""Core GTM pipeline: snapshot pull from Synapse, enrichment, quarterly tables, and Styler renderers."""

from __future__ import annotations

import os
import textwrap
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from pandas.io.formats.style import Styler

import numpy as np
import pandas as pd
import pyodbc

REGION_FAMILY_MAP = {
    "AMS Core East Canada": "AMS East",
    "AMS Core East Northeast": "AMS East",
    "AMS Core LATAM": "LATAM",
    "AMS Core South Southeast": "AMS South",
    "AMS Core South TOLA": "AMS South",
    "AMS Core West Central": "AMS West",
    "AMS Core West Pacific": "AMS West",
    "AMS Corporate": "AMS Corporate",
    "AMS DevOps": "AMS Corporate",
    "AMS Public Sector - FED": "Public Sector",
    "AMS Public Sector - SLED": "Public Sector",
    "AMS Sealights": "AMS Sealights",
    "APAC ANZ": "APAC",
    "APAC Asia": "APAC",
    "APAC Asia AGE": "APAC",
    "APAC Asia SEA": "APAC",
    "APAC Japan": "APAC",
    "EMEA Core Alps CEE": "EMEA DACH",
    "EMEA Core BeNeLux Nordics": "EMEA North",
    "EMEA Core France Emerging": "EMEA South",
    "EMEA Core Germany": "EMEA DACH",
    "EMEA Core Middle East Africa": "EMEA South",
    "EMEA Core UKI": "EMEA North",
    "EMEA Corporate": "EMEA Corporate",
    "EMEA DevOps": "EMEA Corporate",
    "EMEA Core MEA North": "EMEA North",
    "EMEA Core MEA South": "EMEA South",
    "EMEA Core France": "EMEA South",
    "EMEA Core Emerging": "EMEA South",
    "EMEA Core Nordics": "EMEA North",
    "EMEA Core Benelux": "EMEA North",
    "EMEA Core BeNeLux": "EMEA North"
}

LATE_STAGES = {
    "3 - Executive Presentation",
    "4 - Technical Evaluation",
    "5 - Negotiation / Business Procurement",
    "6 - Closed/Pending",
    "Stage 4 - Closed Pending",
}

TARGETS_M = {
    # FY26 Q1
    (2026, 1, "AMS East"): 2.802,
    (2026, 1, "AMS West"): 3.255,
    (2026, 1, "AMS South"): 2.917,
    (2026, 1, "AMS Corporate"): 0.504,
    (2026, 1, "EMEA North"): 2.646,
    (2026, 1, "EMEA DACH"): 2.234,
    (2026, 1, "EMEA South"): 1.977,
    (2026, 1, "EMEA Corporate"): 0.707,
    (2026, 1, "APAC"): 3.106,
    (2026, 1, "Public Sector"): 2.118,
    (2026, 1, "LATAM"): 0.501,
    # FY26 Q2
    (2026, 2, "AMS East"): 3.640,
    (2026, 2, "AMS West"): 4.228,
    (2026, 2, "AMS South"): 3.788,
    (2026, 2, "AMS Corporate"): 0.655,
    (2026, 2, "EMEA North"): 3.567,
    (2026, 2, "EMEA DACH"): 3.011,
    (2026, 2, "EMEA South"): 2.665,
    (2026, 2, "EMEA Corporate"): 0.953,
    (2026, 2, "APAC"): 4.290,
    (2026, 2, "Public Sector"): 2.168,
    (2026, 2, "LATAM"): 0.651,
    # FY26 Q3
    (2026, 3, "AMS East"): 4.027,
    (2026, 3, "AMS West"): 4.678,
    (2026, 3, "AMS South"): 4.191,
    (2026, 3, "AMS Corporate"): 0.725,
    (2026, 3, "EMEA North"): 4.014,
    (2026, 3, "EMEA DACH"): 3.388,
    (2026, 3, "EMEA South"): 2.999,
    (2026, 3, "EMEA Corporate"): 1.073,
    (2026, 3, "APAC"): 4.480,
    (2026, 3, "Public Sector"): 7.999,
    (2026, 3, "LATAM"): 0.720,
    # FY26 Q4
    (2026, 4, "AMS East"): 7.862,
    (2026, 4, "AMS West"): 9.133,
    (2026, 4, "AMS South"): 8.182,
    (2026, 4, "AMS Corporate"): 1.415,
    (2026, 4, "EMEA North"): 7.209,
    (2026, 4, "EMEA DACH"): 6.085,
    (2026, 4, "EMEA South"): 5.387,
    (2026, 4, "EMEA Corporate"): 1.927,
    (2026, 4, "APAC"): 6.482,
    (2026, 4, "Public Sector"): 3.640,
    (2026, 4, "LATAM"): 1.407,
}

STATIC_TARGETS_M = {
    # FY26 Q1
    (2026, 1, "AMS Core East Canada"): 0.943,
    (2026, 1, "AMS Core East LATAM"): 0.501,
    (2026, 1, "AMS Core East Northeast"): 1.859,
    (2026, 1, "AMS Core South Southeast"): 1.714,
    (2026, 1, "AMS Core South TOLA"): 1.203,
    (2026, 1, "AMS Core West Central"): 1.735,
    (2026, 1, "AMS Core West Pacific"): 1.520,
    (2026, 1, "AMS Corporate"): 0.504,
    (2026, 1, "AMS Public Sector - FED"): 1.250,
    (2026, 1, "AMS Public Sector - SLED"): 0.868,
    (2026, 1, "APAC ANZ"): 1.004,
    (2026, 1, "APAC Asia"): 1.591,
    (2026, 1, "APAC Japan"): 0.511,
    (2026, 1, "EMEA Core Alps CEE"): 0.943,
    (2026, 1, "EMEA Core Benelux Nordics"): 1.218,
    (2026, 1, "EMEA Core France Emerging"): 1.173,
    (2026, 1, "EMEA Core Germany"): 1.291,
    (2026, 1, "EMEA Core Middle East"): 0.804,
    (2026, 1, "EMEA Core UKI"): 1.428,
    (2026, 1, "EMEA Corporate"): 0.707,
    # FY26 Q2
    (2026, 2, "AMS Core East Canada"): 1.225,
    (2026, 2, "AMS Core East LATAM"): 0.651,
    (2026, 2, "AMS Core East Northeast"): 2.415,
    (2026, 2, "AMS Core South Southeast"): 2.226,
    (2026, 2, "AMS Core South TOLA"): 1.562,
    (2026, 2, "AMS Core West Central"): 2.253,
    (2026, 2, "AMS Core West Pacific"): 1.975,
    (2026, 2, "AMS Corporate"): 0.655,
    (2026, 2, "AMS Public Sector - FED"): 1.279,
    (2026, 2, "AMS Public Sector - SLED"): 0.889,
    (2026, 2, "APAC ANZ"): 1.387,
    (2026, 2, "APAC Asia"): 2.198,
    (2026, 2, "APAC Japan"): 0.706,
    (2026, 2, "EMEA Core Alps CEE"): 1.271,
    (2026, 2, "EMEA Core Benelux Nordics"): 1.642,
    (2026, 2, "EMEA Core France Emerging"): 1.581,
    (2026, 2, "EMEA Core Germany"): 1.740,
    (2026, 2, "EMEA Core Middle East"): 1.084,
    (2026, 2, "EMEA Core UKI"): 1.925,
    (2026, 2, "EMEA Corporate"): 0.953,
    # FY26 Q3
    (2026, 3, "AMS Core East Canada"): 1.356,
    (2026, 3, "AMS Core East LATAM"): 0.720,
    (2026, 3, "AMS Core East Northeast"): 2.672,
    (2026, 3, "AMS Core South Southeast"): 2.463,
    (2026, 3, "AMS Core South TOLA"): 1.728,
    (2026, 3, "AMS Core West Central"): 2.493,
    (2026, 3, "AMS Core West Pacific"): 2.185,
    (2026, 3, "AMS Corporate"): 0.725,
    (2026, 3, "AMS Public Sector - FED"): 4.719,
    (2026, 3, "AMS Public Sector - SLED"): 3.280,
    (2026, 3, "APAC ANZ"): 1.448,
    (2026, 3, "APAC Asia"): 2.295,
    (2026, 3, "APAC Japan"): 0.737,
    (2026, 3, "EMEA Core Alps CEE"): 1.431,
    (2026, 3, "EMEA Core Benelux Nordics"): 1.848,
    (2026, 3, "EMEA Core France Emerging"): 1.779,
    (2026, 3, "EMEA Core Germany"): 1.958,
    (2026, 3, "EMEA Core Middle East"): 1.220,
    (2026, 3, "EMEA Core UKI"): 2.166,
    (2026, 3, "EMEA Corporate"): 1.073,
    # FY26 Q4
    (2026, 4, "AMS Core East Canada"): 2.647,
    (2026, 4, "AMS Core East LATAM"): 1.407,
    (2026, 4, "AMS Core East Northeast"): 5.216,
    (2026, 4, "AMS Core South Southeast"): 4.809,
    (2026, 4, "AMS Core South TOLA"): 3.374,
    (2026, 4, "AMS Core West Central"): 4.867,
    (2026, 4, "AMS Core West Pacific"): 4.266,
    (2026, 4, "AMS Corporate"): 1.415,
    (2026, 4, "AMS Public Sector - FED"): 2.147,
    (2026, 4, "AMS Public Sector - SLED"): 1.492,
    (2026, 4, "APAC ANZ"): 2.095,
    (2026, 4, "APAC Asia"): 3.320,
    (2026, 4, "APAC Japan"): 1.066,
    (2026, 4, "EMEA Core Alps CEE"): 2.569,
    (2026, 4, "EMEA Core Benelux Nordics"): 3.318,
    (2026, 4, "EMEA Core France Emerging"): 3.196,
    (2026, 4, "EMEA Core Germany"): 3.516,
    (2026, 4, "EMEA Core Middle East"): 2.191,
    (2026, 4, "EMEA Core UKI"): 3.891,
    (2026, 4, "EMEA Corporate"): 1.927,
}

PRODUCT_TARGETS_M = {
    # FY26 Q1
    (2026, 1, "AMS", "LiveCompare"): 0.753,
    (2026, 1, "AMS", "NeoLoad"): 1.423,
    (2026, 1, "AMS", "Recurring Services"): 1.368,
    (2026, 1, "AMS", "Sealights"): 0.924,
    (2026, 1, "AMS", "Tosca"): 5.104,
    (2026, 1, "AMS", "Data Integrity"): 0.445,
    (2026, 1, "AMS", "qTest"): 2.079,
    (2026, 1, "EMEA", "LiveCompare"): 0.447,
    (2026, 1, "EMEA", "NeoLoad"): 0.907,
    (2026, 1, "EMEA", "Recurring Services"): 0.206,
    (2026, 1, "EMEA", "Sealights"): 0.178,
    (2026, 1, "EMEA", "Tosca"): 4.026,
    (2026, 1, "EMEA", "Data Integrity"): 0.348,
    (2026, 1, "EMEA", "qTest"): 1.452,
    (2026, 1, "APAC", "LiveCompare"): 0.107,
    (2026, 1, "APAC", "NeoLoad"): 0.369,
    (2026, 1, "APAC", "Recurring Services"): 0.117,
    (2026, 1, "APAC", "Sealights"): 0.007,
    (2026, 1, "APAC", "Tosca"): 1.849,
    (2026, 1, "APAC", "Data Integrity"): 0.063,
    (2026, 1, "APAC", "qTest"): 0.594,
    # FY26 Q2
    (2026, 2, "AMS", "LiveCompare"): 0.926,
    (2026, 2, "AMS", "NeoLoad"): 1.777,
    (2026, 2, "AMS", "Recurring Services"): 1.620,
    (2026, 2, "AMS", "Sealights"): 1.185,
    (2026, 2, "AMS", "Tosca"): 6.420,
    (2026, 2, "AMS", "Data Integrity"): 0.548,
    (2026, 2, "AMS", "qTest"): 2.651,
    (2026, 2, "EMEA", "LiveCompare"): 0.603,
    (2026, 2, "EMEA", "NeoLoad"): 1.222,
    (2026, 2, "EMEA", "Recurring Services"): 0.279,
    (2026, 2, "EMEA", "Sealights"): 0.239,
    (2026, 2, "EMEA", "Tosca"): 5.426,
    (2026, 2, "EMEA", "Data Integrity"): 0.469,
    (2026, 2, "EMEA", "qTest"): 1.957,
    (2026, 2, "APAC", "LiveCompare"): 0.148,
    (2026, 2, "APAC", "NeoLoad"): 0.509,
    (2026, 2, "APAC", "Recurring Services"): 0.161,
    (2026, 2, "APAC", "Sealights"): 0.009,
    (2026, 2, "APAC", "Tosca"): 2.554,
    (2026, 2, "APAC", "Data Integrity"): 0.087,
    (2026, 2, "APAC", "qTest"): 0.821,
    # FY26 Q3
    (2026, 3, "AMS", "LiveCompare"): 1.520,
    (2026, 3, "AMS", "NeoLoad"): 2.642,
    (2026, 3, "AMS", "Recurring Services"): 3.303,
    (2026, 3, "AMS", "Sealights"): 1.457,
    (2026, 3, "AMS", "Tosca"): 9.115,
    (2026, 3, "AMS", "Data Integrity"): 0.897,
    (2026, 3, "AMS", "qTest"): 3.403,
    (2026, 3, "EMEA", "LiveCompare"): 0.678,
    (2026, 3, "EMEA", "NeoLoad"): 1.375,
    (2026, 3, "EMEA", "Recurring Services"): 0.313,
    (2026, 3, "EMEA", "Sealights"): 0.269,
    (2026, 3, "EMEA", "Tosca"): 6.106,
    (2026, 3, "EMEA", "Data Integrity"): 0.528,
    (2026, 3, "EMEA", "qTest"): 2.202,
    (2026, 3, "APAC", "LiveCompare"): 0.155,
    (2026, 3, "APAC", "NeoLoad"): 0.532,
    (2026, 3, "APAC", "Recurring Services"): 0.168,
    (2026, 3, "APAC", "Sealights"): 0.010,
    (2026, 3, "APAC", "Tosca"): 2.667,
    (2026, 3, "APAC", "Data Integrity"): 0.091,
    (2026, 3, "APAC", "qTest"): 0.857,
    # FY26 Q4
    (2026, 4, "AMS", "LiveCompare"): 1.908,
    (2026, 4, "AMS", "NeoLoad"): 3.713,
    (2026, 4, "AMS", "Recurring Services"): 3.217,
    (2026, 4, "AMS", "Sealights"): 2.531,
    (2026, 4, "AMS", "Tosca"): 13.493,
    (2026, 4, "AMS", "Data Integrity"): 1.129,
    (2026, 4, "AMS", "qTest"): 5.638,
    (2026, 4, "EMEA", "LiveCompare"): 1.218,
    (2026, 4, "EMEA", "NeoLoad"): 2.470,
    (2026, 4, "EMEA", "Recurring Services"): 0.564,
    (2026, 4, "EMEA", "Sealights"): 0.484,
    (2026, 4, "EMEA", "Tosca"): 10.967,
    (2026, 4, "EMEA", "Data Integrity"): 0.948,
    (2026, 4, "EMEA", "qTest"): 3.955,
    (2026, 4, "APAC", "LiveCompare"): 0.224,
    (2026, 4, "APAC", "NeoLoad"): 0.769,
    (2026, 4, "APAC", "Recurring Services"): 0.243,
    (2026, 4, "APAC", "Sealights"): 0.014,
    (2026, 4, "APAC", "Tosca"): 3.859,
    (2026, 4, "APAC", "Data Integrity"): 0.132,
    (2026, 4, "APAC", "qTest"): 1.240,
}

DEAL_TYPE_TARGETS_M = {
    # FY26 Q1
    (2026, 1, "AMS", "New"): 3.608,
    (2026, 1, "AMS", "Existing"): 5.563,
    (2026, 1, "EMEA", "New"): 2.991,
    (2026, 1, "EMEA", "Existing"): 4.232,
    (2026, 1, "APAC", "New"): 1.180,
    (2026, 1, "APAC", "Existing"): 1.587,
    (2026, 1, "Pubsec", "New"): 0.811,
    (2026, 1, "Pubsec", "Existing"): 1.121,
    # FY26 Q2
    (2026, 2, "AMS", "New"): 5.475,
    (2026, 2, "AMS", "Existing"): 8.441,
    (2026, 2, "EMEA", "New"): 4.406,
    (2026, 2, "EMEA", "Existing"): 6.233,
    (2026, 2, "APAC", "New"): 1.926,
    (2026, 2, "APAC", "Existing"): 2.591,
    (2026, 2, "Pubsec", "New"): 1.754,
    (2026, 2, "Pubsec", "Existing"): 2.423,
    # FY26 Q3
    (2026, 3, "AMS", "New"): 6.067,
    (2026, 3, "AMS", "Existing"): 9.353,
    (2026, 3, "EMEA", "New"): 4.769,
    (2026, 3, "EMEA", "Existing"): 6.747,
    (2026, 3, "APAC", "New"): 2.085,
    (2026, 3, "APAC", "Existing"): 2.805,
    (2026, 3, "Pubsec", "New"): 3.064,
    (2026, 3, "Pubsec", "Existing"): 4.233,
    # FY26 Q4
    (2026, 4, "AMS", "New"): 10.765,
    (2026, 4, "AMS", "Existing"): 16.598,
    (2026, 4, "EMEA", "New"): 8.266,
    (2026, 4, "EMEA", "Existing"): 11.695,
    (2026, 4, "APAC", "New"): 2.590,
    (2026, 4, "APAC", "Existing"): 3.484,
    (2026, 4, "Pubsec", "New"): 1.441,
    (2026, 4, "Pubsec", "Existing"): 1.990,
}


def region_of(family: Any) -> str:
    if family in {"AMS East", "AMS West", "AMS South", "AMS Corporate"}:
        return "AMS"
    if family in {"EMEA North", "EMEA DACH", "EMEA South", "EMEA Corporate"}:
        return "EMEA"
    return family


def deal_type_class(deal_type: Any) -> Optional[str]:
    if deal_type == "New Customer":
        return "New"
    if deal_type in ("Expansion", "Upsell", "Professional Services"):
        return "Existing"
    return None


def default_synapse_connection_string() -> str:
    """Build ODBC connection string from env (see .env.example); no I/O on import."""
    full = os.environ.get("SYNAPSE_CONNECTION_STRING")
    if full:
        return full.strip()
    driver = os.environ.get("SYNAPSE_DRIVER", "ODBC Driver 18 for SQL Server")
    server = os.environ.get("SYNAPSE_SERVER", "syn-digital-zuse2-prod.sql.azuresynapse.net")
    database = os.environ.get("SYNAPSE_DATABASE", "DedicatedSQLPool")
    auth = os.environ.get("SYNAPSE_AUTH", "ActiveDirectoryInteractive")
    encrypt = os.environ.get("SYNAPSE_ENCRYPT", "yes")
    trust = os.environ.get("SYNAPSE_TRUST_SERVER_CERTIFICATE", "no")
    return (
        f"Driver={{{driver}}};"
        f"Server={server};"
        f"Database={database};"
        f"Authentication={auth};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust};"
    )


def _build_snapshot_query_sql(fy_start: pd.Timestamp, fy_end: pd.Timestamp) -> str:
    return textwrap.dedent(
        f"""
        WITH opp_product AS (
            SELECT
                N.Opportunity_id AS Opportunity_ID,
                MIN(CASE
                    WHEN N.Record_Type IN ('Service','Platnium Support') THEN 'Recurring Services'
                    WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca') THEN 'Tosca'
                    WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC','TTA for SNOW','TTA SNOW','Tricentis Device Cloud','Mobile') THEN 'Testim'
                    WHEN N.Family IN ('Tosca BI','Tosca DI') THEN 'Data Integrity'
                    WHEN N.Family IN ('Vera') THEN 'Vera'
                    WHEN N.Family IN ('qTest') THEN 'qTest'
                    WHEN N.Family IN ('LiveCompare') THEN 'LiveCompare'
                    WHEN N.Family IN ('NeoLoad') THEN 'NeoLoad'
                    WHEN N.Family IN ('Tricentis Sealights') THEN 'Sealights'
                    ELSE N.Family
                END) AS Product,
                MIN(N.Opp_Geo) AS Geo
            FROM [src].[sku_nacv_fact] AS N
            WHERE Period = 'Period_1'
              AND N.NACV_USD != 0
              AND N.record_type IN ('Product','Service','Platinum support')
            GROUP BY N.Opportunity_id
        )
        SELECT
            s.Opp_Id AS Opportunity_ID,
            s.Account_Id,
            s.Cal_IACV,
            s.Total_NACV,
            s.snapshot_date,
            s.CloseDate,
            s.Stage_1_Start_Date AS CreateDate,
            s.Raw_Stage,
            s.Bookings_Team_static,
            CASE WHEN s.Opp_Type = 'New Business' THEN 'New Customer' ELSE s.Opp_Type END AS Deal_Type,
            CASE
                WHEN s.Raw_Stage IN ('Closed Deferred','Closed Lost') THEN 'Closed'
                WHEN s.Raw_Stage IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
                WHEN s.Raw_Stage IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                                     'Opportunity Rejected','0 - First Interaction') THEN 'Other'
                ELSE 'Open'
            END AS Stage,
            op.Product,
            op.Geo
        FROM [rep].[trf_opp_daily_snapshot_new] AS s
        LEFT JOIN opp_product AS op
            ON op.Opportunity_ID = s.Opp_Id
        WHERE CAST(s.snapshot_date AS DATE) >= '{fy_start.date()}'
          AND CAST(s.snapshot_date AS DATE) <= '{fy_end.date()}'
          AND CAST(s.CloseDate AS DATE) >= '{fy_start.date()}'
          AND CAST(s.CloseDate AS DATE) <= '{fy_end.date()}'
          AND s.Cal_IACV != 0
          AND s.Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
          AND s.Bookings_Team_static IS NOT NULL
          AND s.Raw_Stage NOT IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                                  'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction')
        """
    ).strip()


def pull_snapshot_data(
    fy_start: pd.Timestamp,
    fy_end: pd.Timestamp,
    connection_string: Optional[str] = None,
) -> pd.DataFrame:
    """Pull Synapse snapshot rows joined to SKU product/geo for the FY calendar window.

    This is the weekly dashboard source of truth: filter with ``filter_to_snapshot_date`` after
    resolving the calendar date via ``resolve_snapshot_date``.
    """
    conn_str = connection_string if connection_string is not None else default_synapse_connection_string()
    sql = _build_snapshot_query_sql(fy_start, fy_end)
    with pyodbc.connect(conn_str, autocommit=True) as conn:
        df = pd.read_sql(sql, conn)

    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df["CloseDate"] = pd.to_datetime(df["CloseDate"])
    df["CreateDate"] = pd.to_datetime(df["CreateDate"])
    df["Quarter"] = ((df["CloseDate"].dt.month - 1) // 3 + 1).astype("Int64")
    df["FY"] = df["CloseDate"].dt.year

    df = df.rename(columns={"Bookings_Team_static": "Booking_Team_Static"})

    df["Region Family"] = df["Booking_Team_Static"].map(REGION_FAMILY_MAP)
    df["Region"] = df["Region Family"].apply(region_of)
    df["Geo_View"] = np.where(df["Region Family"] == "Public Sector", "Pubsec", df["Geo"])

    df["Product"] = df["Product"].fillna("Unassigned")
    df["Geo"] = df["Geo"].fillna("Unassigned")
    df["Geo_View"] = pd.Series(df["Geo_View"]).fillna("Unassigned")

    df["Is_LS"] = df["Raw_Stage"].isin(LATE_STAGES)
    df["Deal_Class"] = df["Deal_Type"].apply(deal_type_class)

    df["Product_NACV"] = df["Cal_IACV"]  # naming compat; opp-grain Cal_IACV, not SKU-level NACV minus uplift

    return df


def resolve_snapshot_date(
    snap_df: pd.DataFrame,
    requested: pd.Timestamp,
) -> tuple[pd.Timestamp, bool]:
    """Return (actual_date, is_nearest). actual_date is the latest snapshot_date <= requested.

    is_nearest is True when actual_date != requested (calendar day). Raises ValueError if snap_df
    has no rows with snapshot_date on or before requested.
    """
    requested = pd.Timestamp(requested).normalize()
    if snap_df.empty:
        raise ValueError("Cannot resolve snapshot date: DataFrame is empty.")
    dates = pd.to_datetime(snap_df["snapshot_date"]).dt.normalize()
    eligible = pd.Series(sorted(dates.unique()))
    eligible = eligible[eligible <= requested]
    if eligible.empty:
        raise ValueError(f"No snapshot_date on or before {requested.date()} in the pulled range.")
    actual = pd.Timestamp(eligible.iloc[-1]).normalize()
    is_nearest = actual != requested
    return actual, bool(is_nearest)


def filter_to_snapshot_date(
    snap_df: pd.DataFrame,
    snapshot_date: pd.Timestamp,
) -> pd.DataFrame:
    """Return rows where snapshot_date equals snapshot_date (normalized calendar day)."""
    d = pd.Timestamp(snapshot_date).normalize()
    mask = pd.to_datetime(snap_df["snapshot_date"]).dt.normalize() == d
    return snap_df.loc[mask].copy()


def build_quarterly_table(
    df: pd.DataFrame, fy: int, quarter: int, targets: dict
) -> pd.DataFrame:
    """Returns the quarterly Pipe Balance summary table for one FY/Quarter."""
    q = df[(df["FY"] == fy) & (df["Quarter"] == quarter)].copy()
    q["NACV_M"] = q["Product_NACV"] / 1_000_000

    grouped = (
        q.groupby(["Region", "Region Family"], dropna=True)
        .apply(
            lambda g: pd.Series(
                {
                    "Total Pipe": g.loc[g["Stage"] == "Open", "NACV_M"].sum(),
                    "LS Pipe": g.loc[(g["Stage"] == "Open") & g["Is_LS"], "NACV_M"].sum(),
                    "QTD Booked": g.loc[g["Stage"] == "Closed Won", "NACV_M"].sum(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
        .rename(columns={"Region Family": "Team"})
    )

    grouped["Target"] = grouped["Team"].apply(lambda t: targets.get((fy, quarter, t), np.nan))

    multi_team_regions: list = []
    single_regions: list = []
    for region, region_df in grouped.groupby("Region", sort=True):
        (multi_team_regions if len(region_df) > 1 else single_regions).append((region, region_df))

    final_rows: list = []
    for region, region_df in multi_team_regions:
        final_rows.extend(region_df.sort_values("Team").to_dict("records"))
        final_rows.append(
            {
                "Region": f"{region} Total",
                "Team": "",
                "Total Pipe": region_df["Total Pipe"].sum(),
                "LS Pipe": region_df["LS Pipe"].sum(),
                "QTD Booked": region_df["QTD Booked"].sum(),
                "Target": region_df["Target"].sum(),
            }
        )

    for region, region_df in single_regions:
        row = region_df.iloc[0].to_dict()
        row["Team"] = ""
        row["Target"] = targets.get((fy, quarter, region), np.nan)
        final_rows.append(row)

    core = grouped
    final_rows.append(
        {
            "Region": "Core Total",
            "Team": "",
            "Total Pipe": core["Total Pipe"].sum(),
            "LS Pipe": core["LS Pipe"].sum(),
            "QTD Booked": core["QTD Booked"].sum(),
            "Target": core["Target"].sum(),
        }
    )

    table = pd.DataFrame(final_rows)
    table["LTB"] = table["Target"] - table["QTD Booked"]

    ltb = table["LTB"]
    table["Total Pipe Cov LTB"] = np.where(ltb > 0, table["Total Pipe"] / ltb, np.nan)
    table["LS Pipe Cov LTB"] = np.where(ltb > 0, table["LS Pipe"] / ltb, np.nan)

    return table[
        [
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
    ]


def quarterly_pipe_reconciliation(
    df: pd.DataFrame,
    fy: int,
    quarter: int,
    *,
    targets: dict | None = None,
) -> dict[str, Any]:
    """
    Explain gaps between “all open pipe in the quarter slice” and Quarterly Summary Core Total.

    The quarterly table groups by (Region, Region Family) with ``dropna=True``, so any row whose
    ``Booking_Team_Static`` is missing from ``REGION_FAMILY_MAP`` gets **no row** and its open
    pipe is **excluded** from subtotals and from Core Total. This helper quantifies that gap.
    """
    tgt = targets if targets is not None else TARGETS_M
    q = df[(df["FY"] == fy) & (df["Quarter"] == quarter)].copy()
    q["NACV_M"] = q["Product_NACV"] / 1_000_000
    open_m = q["Stage"] == "Open"
    full_open = q.loc[open_m, "NACV_M"].sum()
    unmapped_m = open_m & q["Region Family"].isna()
    unmapped_open = q.loc[unmapped_m, "NACV_M"].sum()
    tbl = build_quarterly_table(df, fy, quarter, tgt)
    core_open = float(tbl.loc[tbl["Region"] == "Core Total", "Total Pipe"].iloc[0])
    by_team = (
        q.loc[unmapped_m]
        .groupby("Booking_Team_Static", dropna=False)["NACV_M"]
        .sum()
        .sort_values(ascending=False)
    )
    top_unmapped = [(str(k), float(v)) for k, v in by_team.head(25).items() if v != 0]
    return {
        "fy": fy,
        "quarter": quarter,
        "open_pipe_all_rows_m": full_open,
        "open_pipe_core_total_m": core_open,
        "gap_open_pipe_m": full_open - core_open,
        "open_pipe_unmapped_teams_m": unmapped_open,
        "top_unmapped_teams_open_m": top_unmapped,
    }


# Keep in sync with run_weekly_report.py coverage delta column names
_TOTAL_COV_DELTA = "Total pipe coverage change"
_LS_COV_DELTA = "LS pipe coverage change"


def _coverage_class_for(v: float) -> str:
    """Tier class for a ratio cell."""
    if pd.isna(v):
        return "cov-dash"
    if v >= 2.5:
        return "cov-good"
    if v >= 1.5:
        return "cov-warn"
    return "cov-bad"


def _delta_class_for(v: float) -> str:
    """Sign class for a Δ cell."""
    if pd.isna(v):
        return "delta-dash"
    if v > 0:
        return "delta-pos"
    if v < 0:
        return "delta-neg"
    return "delta-zero"


def _build_td_classes(
    table: pd.DataFrame,
    *,
    label_cols: list[str],
    numeric_cols: list[str],
    cov_cols: list[str],
    delta_cols: list[str],
    subtotal_mask: pd.Series,
    total_mask: pd.Series,
) -> pd.DataFrame:
    """Returns same-shape DataFrame of CSS class strings to apply via Styler.set_td_classes."""
    classes = pd.DataFrame("", index=table.index, columns=table.columns)

    row_class = pd.Series("", index=table.index)
    row_class[subtotal_mask] = "row-subtotal"
    row_class[total_mask] = "row-total"

    for col in table.columns:
        if col in label_cols:
            col_class = "l"
        elif col in cov_cols:
            col_class = "num cov"
        elif col in delta_cols:
            col_class = "num delta"
        elif col in numeric_cols:
            col_class = "num"
        else:
            col_class = ""

        if col in cov_cols:
            tier = table[col].apply(_coverage_class_for)
            cell = (col_class + " " + tier).str.strip()
        elif col in delta_cols:
            sign = table[col].apply(_delta_class_for)
            cell = (col_class + " " + sign).str.strip()
        else:
            cell = pd.Series([col_class] * len(table), index=table.index)

        combined = (row_class + " " + cell).str.strip().str.replace(r"\s+", " ", regex=True)
        classes[col] = combined

    for col in cov_cols:
        classes[col] = (classes[col] + " group-start").str.replace(r"\s+", " ", regex=True)

    return classes


def _format_signed_delta(v: float) -> str:
    if pd.isna(v):
        return "\u2014"
    if v == 0:
        return "\u00b10.00"
    if v > 0:
        return f"+{v:,.2f}"
    return f"\u2212{abs(v):,.2f}"


def _format_for_renamed(
    table: pd.DataFrame,
    money_cols: list[str],
    delta_money_cols: list[str],
    cov_cols: list[str],
    delta_cols: list[str],
) -> dict:
    """Format dict — Unicode × for ratios, Unicode − on negative deltas.
    `cov_cols` and `delta_cols` are the *renamed* (display) column names."""
    fmt: dict = {}
    for c in money_cols:
        if c in table.columns:
            fmt[c] = "${:,.1f}"
    for c in delta_money_cols:
        if c in table.columns:
            fmt[c] = "${:+,.1f}"
    for c in cov_cols:
        if c in table.columns:
            fmt[c] = "{:.2f}\u00d7"
    for c in delta_cols:
        if c in table.columns:
            fmt[c] = _format_signed_delta
    return fmt


# Compact leaf-header names. Grouped header (Total Coverage / LS Coverage) carries disambiguation.
_LEAF_RENAME_QUARTERLY = {
    "Total Pipe Cov LTB": "Ratio",
    _TOTAL_COV_DELTA: "Δ WoW",
    "LS Pipe Cov LTB": "Ratio ",
    _LS_COV_DELTA: "Δ WoW ",
}
_LEAF_RENAME_PRODUCT = {
    "Total Pipe Cov": "Ratio",
    _TOTAL_COV_DELTA: "Δ WoW",
    "LS Pipe Cov": "Ratio ",
    _LS_COV_DELTA: "Δ WoW ",
}
_LEAF_RENAME_DEAL = _LEAF_RENAME_PRODUCT


def _rename_for_display(table: pd.DataFrame, mapping: dict[str, str]) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Return a copy with leaf coverage/delta columns renamed. Trailing space on second Ratio/Δ WoW keeps names unique."""
    keys = list(mapping.keys())
    total_cov_old, total_delta_old, ls_cov_old, ls_delta_old = keys[0], keys[1], keys[2], keys[3]

    present = {old: new for old, new in mapping.items() if old in table.columns}
    renamed = table.rename(columns=present)

    cov_cols = [present[c] for c in (total_cov_old, ls_cov_old) if c in present]
    delta_cols = [present[c] for c in (total_delta_old, ls_delta_old) if c in present]
    return renamed, cov_cols, delta_cols


def render_quarterly(table: pd.DataFrame, fy: int, quarter: int) -> "Styler":
    subtotal_rows = {"AMS Total", "EMEA Total"}

    label_cols = ["Region", "Team"]
    money_cols = ["Total Pipe", "LS Pipe", "QTD Booked", "Target", "LTB"]

    display, cov_cols, delta_cols = _rename_for_display(table, _LEAF_RENAME_QUARTERLY)
    subtotal_mask = display["Region"].isin(subtotal_rows)
    total_mask = display["Region"] == "Core Total"

    classes = _build_td_classes(
        display,
        label_cols=label_cols,
        numeric_cols=money_cols,
        cov_cols=cov_cols,
        delta_cols=delta_cols,
        subtotal_mask=subtotal_mask,
        total_mask=total_mask,
    )

    fmt = _format_for_renamed(display, money_cols, ["ΔTotal Pipe", "ΔLS Pipe"], cov_cols, delta_cols)

    return (
        display.style.set_td_classes(classes)
        .format(fmt, na_rep="\u2014")
        .hide(axis="index")
        .set_table_attributes('class="t"')
    )


def build_product_table(
    df: pd.DataFrame,
    fy: int,
    quarter: int,
    product_targets: dict,
    products: Optional[list] = None,
) -> pd.DataFrame:
    """
    Returns the product breakdown table for one FY/Quarter.
    Includes ALL products in the data; coverage shows '—' when no target exists.
    NULL Products and NULL Geos show up as 'Unassigned'.
    """
    q = df[(df["FY"] == fy) & (df["Quarter"] == quarter)].copy()
    q["NACV_M"] = q["Product_NACV"] / 1_000_000

    if products is None:
        all_products = set(q["Product"].dropna().unique())
        with_target = sorted(
            p
            for p in all_products
            if any((fy, quarter, g, p) in product_targets for g in ["AMS", "EMEA", "APAC"])
        )
        without_target = sorted(all_products - set(with_target))
        products = with_target + without_target

    geos = ["AMS", "EMEA", "APAC"]
    if (q["Geo"] == "Unassigned").any():
        geos.append("Unassigned")

    def metrics_for(mask: pd.Series) -> dict:
        sub = q[mask]
        is_open = sub["Stage"] == "Open"
        is_won = sub["Stage"] == "Closed Won"
        return {
            "Total Pipe": sub.loc[is_open, "NACV_M"].sum(),
            "LS Pipe": sub.loc[is_open & sub["Is_LS"], "NACV_M"].sum(),
            "ACV": sub.loc[is_won, "NACV_M"].sum(),
        }

    final_rows: list = []
    for product in products:
        prod_mask = q["Product"] == product

        geo_rows = []
        for geo in geos:
            m = metrics_for(prod_mask & (q["Geo"] == geo))
            m["Product"] = ""
            m["Geo"] = geo
            m["Target"] = product_targets.get((fy, quarter, geo, product), np.nan)
            geo_rows.append(m)

        target_sum = sum(r["Target"] for r in geo_rows if pd.notna(r["Target"]))
        total = {
            "Product": product,
            "Geo": "",
            "Total Pipe": sum(r["Total Pipe"] for r in geo_rows),
            "LS Pipe": sum(r["LS Pipe"] for r in geo_rows),
            "ACV": sum(r["ACV"] for r in geo_rows),
            "Target": target_sum if target_sum > 0 else np.nan,
        }

        final_rows.append(total)
        final_rows.extend(geo_rows)

    product_totals = [r for r in final_rows if r["Product"] != ""]
    final_rows.append(
        {
            "Product": "Grand Total",
            "Geo": "",
            "Total Pipe": sum(r["Total Pipe"] for r in product_totals),
            "LS Pipe": sum(r["LS Pipe"] for r in product_totals),
            "ACV": sum(r["ACV"] for r in product_totals),
            "Target": sum(r["Target"] for r in product_totals if pd.notna(r["Target"])),
        }
    )

    table = pd.DataFrame(final_rows)
    ltb = table["Target"] - table["ACV"]
    table["Total Pipe Cov"] = np.where((ltb > 0) & pd.notna(table["Target"]), table["Total Pipe"] / ltb, np.nan)
    table["LS Pipe Cov"] = np.where((ltb > 0) & pd.notna(table["Target"]), table["LS Pipe"] / ltb, np.nan)

    return table[
        ["Product", "Geo", "Total Pipe", "LS Pipe", "ACV", "Target", "Total Pipe Cov", "LS Pipe Cov"]
    ]


def render_product(table: pd.DataFrame, fy: int, quarter: int) -> "Styler":
    label_cols = ["Product", "Geo"]
    money_cols = ["Total Pipe", "LS Pipe", "ACV", "Target"]

    display, cov_cols, delta_cols = _rename_for_display(table, _LEAF_RENAME_PRODUCT)
    subtotal_mask = (display["Product"] != "") & (display["Product"] != "Grand Total")
    total_mask = display["Product"] == "Grand Total"

    classes = _build_td_classes(
        display,
        label_cols=label_cols,
        numeric_cols=money_cols,
        cov_cols=cov_cols,
        delta_cols=delta_cols,
        subtotal_mask=subtotal_mask,
        total_mask=total_mask,
    )

    fmt = _format_for_renamed(display, money_cols, ["ΔTotal Pipe", "ΔLS Pipe"], cov_cols, delta_cols)

    return (
        display.style.set_td_classes(classes)
        .format(fmt, na_rep="\u2014")
        .hide(axis="index")
        .set_table_attributes('class="t"')
    )


def build_deal_type_table(df: pd.DataFrame, fy: int, quarter: int, deal_type_targets: dict) -> pd.DataFrame:
    """
    Returns the Geo × Deal Type breakdown table for one FY/Quarter.
    Rows: Geo (bold total) → New / Existing sub-rows, then Total at bottom.
    NULL Geos show up as 'Unassigned'.
    """
    q = df[(df["FY"] == fy) & (df["Quarter"] == quarter) & df["Deal_Class"].notna()].copy()
    q["NACV_M"] = q["Product_NACV"] / 1_000_000

    geos = ["AMS", "EMEA", "APAC", "Pubsec"]
    if (q["Geo_View"] == "Unassigned").any():
        geos.append("Unassigned")
    classes = ["New", "Existing"]

    def metrics_for(mask: pd.Series) -> dict:
        sub = q[mask]
        is_open = sub["Stage"] == "Open"
        is_won = sub["Stage"] == "Closed Won"
        return {
            "Total Pipe": sub.loc[is_open, "NACV_M"].sum(),
            "LS Pipe": sub.loc[is_open & sub["Is_LS"], "NACV_M"].sum(),
            "ACV": sub.loc[is_won, "NACV_M"].sum(),
        }

    final_rows: list = []
    for geo in geos:
        geo_mask = q["Geo_View"] == geo

        class_rows = []
        for cls in classes:
            m = metrics_for(geo_mask & (q["Deal_Class"] == cls))
            m["Geo"] = ""
            m["Type"] = cls
            m["Target"] = deal_type_targets.get((fy, quarter, geo, cls), np.nan)
            class_rows.append(m)

        total = {
            "Geo": geo,
            "Type": "",
            "Total Pipe": sum(r["Total Pipe"] for r in class_rows),
            "LS Pipe": sum(r["LS Pipe"] for r in class_rows),
            "ACV": sum(r["ACV"] for r in class_rows),
            "Target": sum(r["Target"] for r in class_rows if pd.notna(r["Target"])),
        }

        final_rows.append(total)
        final_rows.extend(class_rows)

    geo_totals = [r for r in final_rows if r["Geo"] != ""]
    final_rows.append(
        {
            "Geo": "Total",
            "Type": "",
            "Total Pipe": sum(r["Total Pipe"] for r in geo_totals),
            "LS Pipe": sum(r["LS Pipe"] for r in geo_totals),
            "ACV": sum(r["ACV"] for r in geo_totals),
            "Target": sum(r["Target"] for r in geo_totals),
        }
    )

    table = pd.DataFrame(final_rows)
    ltb = table["Target"] - table["ACV"]
    table["Total Pipe Cov"] = np.where(ltb > 0, table["Total Pipe"] / ltb, np.nan)
    table["LS Pipe Cov"] = np.where(ltb > 0, table["LS Pipe"] / ltb, np.nan)

    return table[["Geo", "Type", "Total Pipe", "LS Pipe", "ACV", "Target", "Total Pipe Cov", "LS Pipe Cov"]]


def render_deal_type(table: pd.DataFrame, fy: int, quarter: int) -> "Styler":
    label_cols = ["Geo", "Type"]
    money_cols = ["Total Pipe", "LS Pipe", "ACV", "Target"]

    display, cov_cols, delta_cols = _rename_for_display(table, _LEAF_RENAME_DEAL)
    subtotal_mask = (display["Geo"] != "") & (display["Geo"] != "Total")
    total_mask = display["Geo"] == "Total"

    classes = _build_td_classes(
        display,
        label_cols=label_cols,
        numeric_cols=money_cols,
        cov_cols=cov_cols,
        delta_cols=delta_cols,
        subtotal_mask=subtotal_mask,
        total_mask=total_mask,
    )

    fmt = _format_for_renamed(display, money_cols, ["ΔTotal Pipe", "ΔLS Pipe"], cov_cols, delta_cols)

    return (
        display.style.set_td_classes(classes)
        .format(fmt, na_rep="\u2014")
        .hide(axis="index")
        .set_table_attributes('class="t"')
    )


def _build_snapshot_lookup_sql(fy_start: pd.Timestamp, fy_end: pd.Timestamp, as_of_cap: pd.Timestamp) -> str:
    """Latest snapshot_date on/before as_of_cap within FY calendar bounds."""
    return textwrap.dedent(
        f"""
        SELECT CAST(MAX(snapshot_date) AS DATE) AS d
        FROM [rep].[trf_opp_daily_snapshot_new]
        WHERE CAST(snapshot_date AS DATE) <= '{as_of_cap.date()}'
          AND CAST(snapshot_date AS DATE) >= '{fy_start.date()}'
          AND CAST(snapshot_date AS DATE) <= '{fy_end.date()}'
        """
    ).strip()


def _build_snapshot_body_sql(snapshot_day: pd.Timestamp, fy_start: pd.Timestamp, fy_end: pd.Timestamp) -> str:
    return textwrap.dedent(
        f"""
        SELECT
            Opp_Id AS Opportunity_ID,
            Account_Id,
            Cal_IACV,
            Total_NACV,
            snapshot_date,
            CloseDate,
            Stage_1_Start_Date AS CreateDate,
            Raw_Stage,
            Bookings_Team_static,
            CASE
                WHEN Raw_Stage IN ('Closed Deferred','Closed Lost') THEN 'Closed'
                WHEN Raw_Stage IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
                WHEN Raw_Stage IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                                   'Opportunity Rejected','0 - First Interaction') THEN 'Other'
                ELSE 'Open'
            END AS Stage
        FROM [rep].[trf_opp_daily_snapshot_new]
        WHERE CAST(snapshot_date AS DATE) = '{snapshot_day.date()}'
          AND CAST(CloseDate AS DATE) >= '{fy_start.date()}'
          AND CAST(CloseDate AS DATE) <= '{fy_end.date()}'
          AND Bookings_Team_static NOT IN ('Account Management','Global','QAS Account Management')
          AND Bookings_Team_static IS NOT NULL
          AND Raw_Stage NOT IN ('Closed - Duplicate','Stage 6 - Closed - Admin','Stage 7 - Churned',
                                'Opportunity Rejected','Stage 0 - Renewal Outreach Not Started','0 - First Interaction')
        """
    ).strip()


def pull_snapshot_day(
    as_of_date: pd.Timestamp,
    fy_start: pd.Timestamp,
    fy_end: pd.Timestamp,
    connection_string: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """
    Pull a single-day slice from ``trf_opp_daily_snapshot_new`` (WoW baseline path).

    If no rows exist for the requested calendar date (e.g. weekend), falls back to the latest
    ``snapshot_date`` on or before ``as_of_date`` within the FY window.

    Returns ``(dataframe, actual_snapshot_date)`` — the second value is the resolved calendar date
    used for ``snapshot_date`` (after any fallback).
    """
    conn_str = connection_string if connection_string is not None else default_synapse_connection_string()
    requested = pd.Timestamp(as_of_date).normalize()

    with pyodbc.connect(conn_str, autocommit=True) as conn:
        sql_exact = _build_snapshot_body_sql(requested, fy_start, fy_end)
        df = pd.read_sql(sql_exact, conn)

    resolved = requested

    if df.empty:
        with pyodbc.connect(conn_str, autocommit=True) as conn:
            lu = pd.read_sql(_build_snapshot_lookup_sql(fy_start, fy_end, requested), conn)
        alt: Optional[pd.Timestamp] = None
        if not lu.empty and lu.iloc[0]["d"] is not None and not pd.isna(lu.iloc[0]["d"]):
            alt = pd.Timestamp(lu.iloc[0]["d"]).normalize()
        if alt is not None:
            resolved = alt
            with pyodbc.connect(conn_str, autocommit=True) as conn:
                sql_fb = _build_snapshot_body_sql(resolved, fy_start, fy_end)
                df = pd.read_sql(sql_fb, conn)
        else:
            for delta in range(1, 8):
                cand = requested - pd.Timedelta(days=delta)
                if cand.date() < fy_start.date():
                    break
                with pyodbc.connect(conn_str, autocommit=True) as conn:
                    sql_try = _build_snapshot_body_sql(cand.normalize(), fy_start, fy_end)
                    df_try = pd.read_sql(sql_try, conn)
                if not df_try.empty:
                    df = df_try
                    resolved = cand.normalize()
                    break

    return df, resolved


def enrich_snapshot_dataframe(df_snap: pd.DataFrame) -> pd.DataFrame:
    """
    Map raw snapshot columns to the shape expected by ``build_quarterly_table``.

    The Synapse snapshot extract does not include Geo, Product, or Deal_Type. This enriched frame
    is therefore suitable only for ``build_quarterly_table``, not ``build_product_table`` or
    ``build_deal_type_table``.
    """
    out = df_snap.copy()

    out["Product_NACV"] = out["Total_NACV"]
    out["Booking_Team_Static"] = out["Bookings_Team_static"]
    out["StageName"] = out["Raw_Stage"]
    out["Close_Date"] = pd.to_datetime(out["CloseDate"])
    out["Create_Date"] = pd.to_datetime(out["CreateDate"])
    out["Quarter"] = ((out["Close_Date"].dt.month - 1) // 3 + 1).astype("Int64")
    out["FY"] = out["Close_Date"].dt.year

    out["Region Family"] = out["Booking_Team_Static"].map(REGION_FAMILY_MAP)
    out["Region"] = out["Region Family"].apply(region_of)

    out["Geo"] = "Unassigned"
    out["Geo_View"] = "Unassigned"
    out["Product"] = "Unassigned"
    out["Deal_Class"] = np.nan

    out["Is_LS"] = out["StageName"].isin(LATE_STAGES)

    return out


def build_quarterly_with_snapshot_wow(
    df_live: pd.DataFrame,
    df_snap_prior_enriched: pd.DataFrame,
    fy: int,
    quarter: int,
    targets: dict,
) -> pd.DataFrame:
    """
    Live quarterly table from ``targets``, plus coverage WoW deltas vs the prior snapshot quarter
    built with the same ``build_quarterly_table`` and ``targets`` (apples-to-apples).

    Rows present in live but missing in the prior snapshot get NaN deltas (rendered as em-dash).
    """
    tbl_live = build_quarterly_table(df_live, fy, quarter, targets)
    tbl_prior = build_quarterly_table(df_snap_prior_enriched, fy, quarter, targets)

    keys = ["Region", "Team"]
    base_cols = ["Total Pipe Cov LTB", "LS Pipe Cov LTB"]

    out = tbl_live.copy()
    prior = tbl_prior.copy()
    for c in keys:
        out[c] = out[c].astype(str)
        prior[c] = prior[c].astype(str)

    rename_map = {b: f"_prior_{b}" for b in base_cols}
    merged = out.merge(prior[keys + base_cols].rename(columns=rename_map), on=keys, how="left")
    merged[_TOTAL_COV_DELTA] = merged["Total Pipe Cov LTB"] - merged["_prior_Total Pipe Cov LTB"]
    merged[_LS_COV_DELTA] = merged["LS Pipe Cov LTB"] - merged["_prior_LS Pipe Cov LTB"]
    merged = merged.drop(columns=[f"_prior_{b}" for b in base_cols])

    column_order = [
        "Region",
        "Team",
        "Total Pipe",
        "LS Pipe",
        "QTD Booked",
        "Target",
        "LTB",
        "Total Pipe Cov LTB",
        _TOTAL_COV_DELTA,
        "LS Pipe Cov LTB",
        _LS_COV_DELTA,
    ]
    ordered = [c for c in column_order if c in merged.columns]
    tail = [c for c in merged.columns if c not in ordered]
    return merged[ordered + tail]
