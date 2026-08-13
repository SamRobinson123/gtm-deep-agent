"""Build a SEPARATE dashboard whose segment page uses the NEW tier logic.

Comparison aid (Sam, 2026-06-28): the production segment page uses the old
fixed initial-tier (served from the frozen payload). This script renders a
second dashboard, identical in every other page, where the segment page is
rebuilt from `[sfdc_trf].[account_live]` via
`COALESCE(Current_Segment__c, X2019_Segment_expected__c)` — the new
company-standard "Current Segment" tier (see the "Confirmation Required:
Account Segment Field" email thread).

It does NOT touch the existing dashboard, the production SQL, or any parquet.
All non-segment pages come straight from the cached output/*.parquet (so they
match the current dashboard); only the segment coverage + recs are rebuilt live.

To compare the tier LOGIC change without the data-freshness change, the build
is capped to an as-of date (default 2026-06-02, the date the frozen original
was captured): the snapshot is filtered to snapshot_date <= as-of and live
bookings to opps closed by then. Closed quarters are unaffected (their snapshots
predate June 2); only the in-flight quarter and post-June-2 drift are pulled
back, so the only remaining difference vs the original is the tier definition.

Output: output/coverage_dashboard_NEW_SEGMENT.html

Needs a fresh Synapse pull (the snapshot must be re-pulled with the new tier
join), so you'll get the interactive Microsoft sign-in. Run it yourself:
    ! uv run python scripts/build_new_segment_dashboard.py
    ! uv run python scripts/build_new_segment_dashboard.py --as-of 2026-06-02
    ! uv run python scripts/build_new_segment_dashboard.py --no-cap   # full/today
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from backend.synapse import connect  # noqa: E402
from backend.coverage_builder import (  # noqa: E402
    build_segment_coverage,
    compute_segment_recommendations,
)
import backend.coverage_render as cr  # noqa: E402
from data.inputs.loaders import (  # noqa: E402
    load_booking_team_mapping,
    load_segment_targets,
)

FISCAL_YEARS = [
    ("FY24", "2024-01-01", "2024-12-31"),
    ("FY25", "2025-01-01", "2025-12-31"),
    ("FY26", "2026-01-01", "2026-12-31"),
]
LIVE_BOOKED_START, LIVE_BOOKED_END = "2024-01-01", "2026-12-31"
DEFAULT_AS_OF = "2026-06-02"  # date the frozen original dashboard was captured
OUT_HTML = REPO / "output" / "coverage_dashboard_NEW_SEGMENT.html"

# The retired account_segment_quarterly CTE (identical text in snapshot.sql and
# live_booked.sql) -> account_live COALESCE. The CASE guard maps any stray
# non-tier value (e.g. a legacy "2. Enterprise Account") to NULL -> Unassigned,
# matching the dashboard's existing missing-tier handling.
OLD_CTE = """account_segment AS (
    SELECT Id, QuarterStartSegment
    FROM (
        SELECT
            Id,
            QuarterStartSegment,
            ROW_NUMBER() OVER (PARTITION BY Id ORDER BY QuarterStartDate ASC) AS rn
        FROM [rpt_cx].[account_segment_quarterly]
    ) ranked
    WHERE rn = 1
)"""
NEW_CTE = """account_segment AS (
    SELECT Id,
        CASE WHEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
                  IN ('Tier 1', 'Tier 2', 'Tier 3')
             THEN COALESCE(Current_Segment__c, X2019_Segment_expected__c)
        END AS QuarterStartSegment
    FROM [sfdc_trf].[account_live]
)"""


def _new_logic_sql(filename: str) -> str:
    sql = (REPO / "backend" / "sql" / filename).read_text()
    swapped = sql.replace(OLD_CTE, NEW_CTE)
    if swapped == sql:
        raise SystemExit(
            f"Could not find the account_segment CTE in {filename} to swap — "
            "the SQL must have changed; update OLD_CTE."
        )
    return swapped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=DEFAULT_AS_OF,
                    help=f"cap snapshot/bookings to this date (default {DEFAULT_AS_OF})")
    ap.add_argument("--no-cap", action="store_true",
                    help="ignore --as-of; pull everything up to today")
    args = ap.parse_args()
    as_of = None if args.no_cap else args.as_of

    snap_sql = _new_logic_sql("snapshot.sql")
    lb_sql = _new_logic_sql("live_booked.sql")
    # Bound live bookings at the as-of so the in-flight quarter's live-booked
    # override matches the frozen original (closed quarters all closed earlier).
    lb_end = as_of or LIVE_BOOKED_END

    print(f"Pulling from Synapse with the account_live COALESCE tier join "
          f"(as-of {as_of or 'today'})...")
    with connect() as conn:
        mapping = load_booking_team_mapping(conn)
        snapshots = []
        for label, fy_start, fy_end in FISCAL_YEARS:
            snap_start = (pd.Timestamp(fy_start) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
            print(f"  snapshot {label}...")
            df = pd.read_sql(
                snap_sql.format(fy_start=fy_start, fy_end=fy_end, snap_start=snap_start),
                conn,
            ).drop_duplicates(ignore_index=True)
            snapshots.append(df)
        print("  live bookings...")
        live_booked = pd.read_sql(
            lb_sql.format(start_date=LIVE_BOOKED_START, end_date=lb_end), conn
        )
    snapshot = pd.concat(snapshots, ignore_index=True)

    if as_of:
        cutoff = pd.Timestamp(as_of)
        before = len(snapshot)
        snapshot = snapshot[pd.to_datetime(snapshot["snapshot_date"]) <= cutoff].copy()
        print(f"  capped snapshot to <= {as_of}: {before:,} -> {len(snapshot):,} rows")

    segment_targets = load_segment_targets()

    print("Building NEW-logic segment coverage + recs...")
    coverage_segment = build_segment_coverage(
        snapshot, mapping, segment_targets, live_booked=live_booked
    )
    segment_recs = compute_segment_recommendations(coverage_segment)

    # Sanity: show the tier mix we just built (catches an all-Unassigned regression).
    mix = coverage_segment.groupby("segment", as_index=False)["open_pipe"].sum()
    print("  segment open-pipe by tier ($M):")
    for _, r in mix.iterrows():
        print(f"    {r['segment']:12} {r['open_pipe']/1e6:,.1f}")

    # Force the live segment build instead of the frozen payload, for this render
    # only (module-global patch in this process; touches no file).
    cr.FROZEN_SEGMENT_PATH = REPO / "data" / "inputs" / "__no_frozen_segment__.json"

    coverage = pd.read_parquet(REPO / "output" / "coverage.parquet")
    # Stamp the as-of pill to the capped snapshot's latest date so it reads the
    # same vintage as the frozen original, not the cached meta.json (Jun 22).
    render_as_of = (
        pd.to_datetime(snapshot["snapshot_date"]).max().date().isoformat()
        if as_of else None
    )
    print(f"Rendering {OUT_HTML.name} (non-segment pages from cached parquets)...")
    out = cr.render(
        coverage,
        coverage_segment=coverage_segment,
        segment_recs=segment_recs,
        html_out=OUT_HTML,
        as_of=render_as_of,
    )
    print(f"Done: {out}")
    print("Original dashboard untouched: output/coverage_dashboard.html")


if __name__ == "__main__":
    main()
