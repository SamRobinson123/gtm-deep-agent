"""Pipeline configuration — quarters, paths, target loading, week derivation.

Materialized from docs/analysis/gtm-dashboard.md (the `pipeline/config.py`
section). That doc is the source of truth; this file must not drift from it.

Two deliberate deviations from the doc, both marked DEVIATION below:
  1. Target_Monthly.csv lives in data/, not the repo root.
  2. The CSV is loaded lazily and cached, rather than read at import time.
"""
import functools
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
RUNS = ROOT / "workspace" / "runs"

# DEVIATION 1: the doc has `ROOT / 'Target_Monthly.csv'`. The file was moved into
# data/ so that all local inputs sit behind one gitignored directory.
TARGET_MONTHLY_CSV = DATA / "Target_Monthly.csv"
HEADCOUNT_XLSX = DATA / "Headcount.xlsx"

load_dotenv(ROOT / ".env")
SYNAPSE_CONN_STR = os.environ.get("SYNAPSE_CONN_STR")

# Oldest first; [0] MUST stay the in-flight quarter — PRE_QUARTER_BUFFER_START and
# coverage.py's verified week-1 anchor logic both assume the buffer sits immediately
# before QUARTER_STARTS[0]. When Q4 becomes current, roll this list forward (and
# re-pull) rather than reordering it.
QUARTER_STARTS = ["2026-07-01", "2026-10-01"]


def q_end(start):
    """Last day of the 3-month quarter beginning `start`."""
    return (pd.Timestamp(start) + pd.DateOffset(months=3) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def fq_label(start):
    """'2026-07-01' -> 'Q3 FY26' (Tricentis fiscal year == calendar year)."""
    t = pd.Timestamp(start)
    return f"Q{(t.month - 1) // 3 + 1} FY{t.year % 100}"


QUARTER_START = QUARTER_STARTS[0]
QUARTER_END = q_end(QUARTER_START)
SNAP_END = q_end(QUARTER_STARTS[-1])

# pull.py grabs this many days of snapshot history BEFORE QUARTER_START so week 1 can
# anchor to the true last-snapshot-of-prior-quarter baseline instead of the first
# in-quarter snapshot. Some opps enter the daily snapshot feed 1-4 days late (created
# right at the quarter boundary but not captured until the ETL's next run) — without a
# buffer, week 1 picks up their first available (post-boundary) row as if it were the
# day-1 state, overstating day-1 pipe.
PRE_QUARTER_BUFFER_START = (pd.Timestamp(QUARTER_START) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")


def month_columns(quarter_start=QUARTER_START, n=3):
    """The M<YYYYMM> column names for a quarter, DERIVED from its start date.

    Invariant 1: never hardcode month column names. This is the single place the
    derivation happens; everything else calls this.
    """
    start = pd.Timestamp(quarter_start)
    return [f"M{(start + pd.DateOffset(months=i)).strftime('%Y%m')}" for i in range(n)]


@functools.lru_cache(maxsize=1)
def targets_raw():
    """Target_Monthly.csv, whitespace-stripped in names AND values.

    DEVIATION 2: the doc reads this at import time. Loading 12 MB on every import
    makes the module slow to import and impossible to import without the data file
    present (which breaks unit tests of unrelated helpers). Cached here instead —
    still read exactly once per process, which is what invariant 8's "read ONCE"
    requires.

    Invariant 8: the raw CSV has stray whitespace in ' Target_Type ', ' Geo ',
    ' GeoSubTerritory_AccountOwnerBookingsTeam'. Stripping names but not values
    silently creates blank-key rows and zeroes out real teams.
    """
    df = pd.read_csv(TARGET_MONTHLY_CSV, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        if str(df[c].dtype) in ("object", "str"):
            df[c] = df[c].astype(str).str.strip()
    return df


TEAM_COL = "GeoSubTerritory_AccountOwnerBookingsTeam"


def _target_by_team(target_type, quarter_start=QUARTER_START):
    """Team x month DataFrame for one Target_Type, for the 3 months of the quarter.

    Monthly, not summed to a quarter total, so pipe_create.py's weekly allocator can
    prorate partial weeks itself.
    """
    cols = month_columns(quarter_start)
    df = targets_raw()
    df = df[df["Target_Type"] == target_type]
    return df.groupby(TEAM_COL)[cols].sum()


def load_territory_targets(quarter_start=QUARTER_START):
    """Per-Territory Bookings targets for the quarter, as a dict."""
    return _target_by_team("Bookings", quarter_start).sum(axis=1).to_dict()


def load_pipe_create_targets(quarter_start=QUARTER_START):
    """Per-Territory Pipe Create $ and opp-count monthly targets.

    Returns (pipe_target_by_month, opp_target_by_month) — two team x month frames.

    Invariant 2: ASP is NOT a row in this file. Always derive it as
    pipe_target / opp_target at matching grain.

    Invariant 10: the opp-count target counts opp-product-lines, not distinct opps
    (verified — its rows carry a Product dimension and the target is built as
    sum_product(pipe_product / ASP_product)). The magnitude of the ~5.5x gap is not
    fully explained. Treat opp-count and ASP attainment as provisional and always
    carry the caveat.
    """
    return (
        _target_by_team("Pipeline", quarter_start),
        _target_by_team("Opportunities", quarter_start),
    )


def quarter_week(d, quarter_start=QUARTER_START):
    """Week-of-quarter (1-based, Mon-Sun) for date `d`.

    Reproduces the source snapshot table's own QuarterWeek column exactly. Used only
    to build the forward-looking week calendar for target allocation — actuals should
    read snap['QuarterWeek'] directly, never re-derive it with this.
    """
    anchor = pd.Timestamp(quarter_start)
    anchor = anchor - pd.Timedelta(days=anchor.weekday())
    return (pd.Timestamp(d) - anchor).days // 7 + 1


COVERAGE_BENCHMARK = {"Q3 FY26": 3.5, "Q4 FY26": 4.0}

EXCLUDED_TEAMS = ["Account Management", "Global", "QAS Account Management"]
EXCLUDED_STAGES = [
    "Closed - Duplicate",
    "Stage 6 - Closed - Admin",
    "Stage 7 - Churned",
    "Opportunity Rejected",
    "Stage 0 - Renewal Outreach Not Started",
    "0 - First Interaction",
]
