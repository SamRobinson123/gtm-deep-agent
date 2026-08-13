"""Entry point: pull data, build the coverage frame, save it.

Run:  python -m backend.build_coverage
"""

import json
import sys
from pathlib import Path

import pandas as pd


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which can't encode the ``→`` arrows in
    our progress prints — that raised a UnicodeEncodeError that the broad
    ``except`` below then mislabeled as "Synapse pull failed". Force UTF-8 so the
    pipeline runs cleanly without needing ``PYTHONIOENCODING=utf-8``.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

from backend.coverage_builder import build_coverage, build_segment_coverage, build_product_coverage, compute_recommendations, compute_segment_recommendations, compute_product_recommendations, compute_product_conversion
from backend.coverage_render import render as render_dashboard
from backend.snapshot import pull_live_booked, pull_opp_products, pull_snapshot
from backend.synapse import connect
from data.inputs.loaders import load_booking_team_mapping, load_quarter_targets, load_segment_targets, load_product_targets

FISCAL_YEARS = [
    ("FY24", "2024-01-01", "2024-12-31"),
    ("FY25", "2025-01-01", "2025-12-31"),
    ("FY26", "2026-01-01", "2026-12-31"),
]

LIVE_BOOKED_START = "2024-01-01"
LIVE_BOOKED_END = "2026-12-31"

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "output" / "coverage.parquet"


def main():
    _force_utf8_stdout()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    try:
        with connect() as conn:
            print("Pulling booking-team mapping...")
            mapping = load_booking_team_mapping(conn)
            print(f"  {len(mapping):,} active teams")

            snapshots = []
            for label, fy_start, fy_end in FISCAL_YEARS:
                # Pull snapshots from ~2 weeks before the FY too, so Q1's "week 1"
                # can anchor to the end-of-prior-December snapshot (PLAN.md §6.2).
                # CloseDate stays bounded to the FY, so no overlap with the prior FY.
                snap_start = (pd.Timestamp(fy_start) - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
                print(f"Pulling snapshot for {label} ({fy_start} → {fy_end}, snapshots from {snap_start})...")
                df = pull_snapshot(conn, fy_start, fy_end, snap_start=snap_start)
                print(f"  {len(df):,} rows")
                snapshots.append(df)

            print(f"Pulling live bookings ({LIVE_BOOKED_START} → {LIVE_BOOKED_END})...")
            live_booked = pull_live_booked(conn, LIVE_BOOKED_START, LIVE_BOOKED_END)
            print(f"  {len(live_booked):,} live SKU rows")

            print("Pulling per-opp product families (SKU-inclusive, for product win rate)...")
            opp_products = pull_opp_products(conn)
            print(f"  {len(opp_products):,} opp-product pairs")
    except Exception as exc:
        raise SystemExit(
            f"Synapse pull failed: {exc}\n"
            "  → Check you're on the VPN and that the connection settings in your "
            ".env (server / database / credentials) are correct."
        ) from exc

    snapshot = pd.concat(snapshots, ignore_index=True)

    print("Loading targets (FY24 / FY25 / FY26)...")
    try:
        targets = load_quarter_targets()
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Target workbook not found: {exc}\n"
            "  → Expected FY'24/FY'25/FY'26 Targets.xlsx in data/inputs/."
        ) from exc
    print(f"  {len(targets):,} target rows")

    print("Loading segment (tier) targets (FY25 / FY26)...")
    try:
        segment_targets = load_segment_targets()
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Target workbook not found: {exc}\n"
            "  → Expected FY'25/FY'26 Targets.xlsx in data/inputs/."
        ) from exc
    print(f"  {len(segment_targets):,} segment target rows")

    print("Loading product targets (FY25 / FY26)...")
    try:
        product_targets = load_product_targets()
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Target workbook not found: {exc}\n"
            "  → Expected FY'25/FY'26 Targets.xlsx in data/inputs/."
        ) from exc
    print(f"  {len(product_targets):,} product target rows")

    print("Building coverage frame...")
    coverage = build_coverage(snapshot, mapping, targets, live_booked=live_booked)
    print(f"  {len(coverage):,} coverage rows")

    coverage.to_parquet(OUTPUT_PATH, index=False)
    print(f"Saved coverage frame:  {OUTPUT_PATH}")

    print("Building segment (geo × tier) coverage frame...")
    coverage_segment = build_segment_coverage(snapshot, mapping, segment_targets, live_booked=live_booked)
    seg_path = OUTPUT_PATH.parent / "coverage_segment.parquet"
    coverage_segment.to_parquet(seg_path, index=False)
    print(f"  {len(coverage_segment):,} segment coverage rows → {seg_path}")

    print("Computing segment needed coverage...")
    segment_recs = compute_segment_recommendations(coverage_segment)
    seg_recs_path = OUTPUT_PATH.parent / "recommendations_segment.parquet"
    segment_recs.to_parquet(seg_recs_path, index=False)
    print(f"  {len(segment_recs):,} segment rec rows → {seg_recs_path}")

    print("Building product coverage frame...")
    coverage_product = build_product_coverage(snapshot, mapping, product_targets, live_booked=live_booked, opp_products=opp_products)
    prod_path = OUTPUT_PATH.parent / "coverage_product.parquet"
    coverage_product.to_parquet(prod_path, index=False)
    print(f"  {len(coverage_product):,} product coverage rows → {prod_path}")

    print("Computing per-product conversion (from the final product frame — snapshot pro-rated)...")
    product_conversion = compute_product_conversion(snapshot, mapping, coverage_product)
    pconv_path = OUTPUT_PATH.parent / "product_conversion.parquet"
    product_conversion.to_parquet(pconv_path, index=False)
    print(f"  {len(product_conversion):,} product conversion rows → {pconv_path}")

    print("Computing product needed coverage (from SKU-inclusive conversions)...")
    product_recs = compute_product_recommendations(product_conversion)
    prod_recs_path = OUTPUT_PATH.parent / "recommendations_product.parquet"
    product_recs.to_parquet(prod_recs_path, index=False)
    print(f"  {len(product_recs):,} product rec rows → {prod_recs_path}")

    print("Computing loose-aggregate recommendations (operational guidance)...")
    recs_agg, recs_per_q = compute_recommendations(coverage)
    recs_path = OUTPUT_PATH.parent / "recommendations.parquet"
    recs_agg.to_parquet(recs_path, index=False)
    print(f"Saved recommendations: {recs_path}  ({len(recs_agg):,} rows)")
    recs_pq_path = OUTPUT_PATH.parent / "recommendations_per_quarter.parquet"
    recs_per_q.to_parquet(recs_pq_path, index=False)
    print(f"Saved per-quarter (loose): {recs_pq_path}  ({len(recs_per_q):,} rows)")

    # "As of" = the latest snapshot date in the pull (the data's freshness, NOT
    # the run date). Persisted to meta.json so template-only re-renders keep it.
    as_of = pd.to_datetime(snapshot["snapshot_date"]).max().date().isoformat()
    (OUTPUT_PATH.parent / "meta.json").write_text(
        json.dumps({"asOfDate": as_of}), encoding="utf-8")
    print(f"Data as-of (latest snapshot): {as_of}")

    print("Rendering HTML dashboard...")
    html_path = render_dashboard(coverage, recs=recs_agg, recs_per_quarter=recs_per_q, coverage_segment=coverage_segment, segment_recs=segment_recs, coverage_product=coverage_product, product_recs=product_recs, as_of=as_of)
    print(f"Rendered dashboard:    {html_path}")

    print("\nEnd-of-quarter snapshot ($M):")
    # Pin each quarter to its latest week with any snapshot data (closed quarters
    # land at week 13; in-flight quarters at the current snapshot's week). For
    # purely future quarters with only live-booked rows, fall back to the
    # absolute last week present in the frame.
    has_snap = coverage[(coverage["open_pipe"] > 0) | (coverage["ls_pipe"] > 0)]
    quarter_max_week = has_snap.groupby("quarter")["week_of_quarter"].max()
    fallback = coverage.groupby("quarter")["week_of_quarter"].max()
    quarter_max_week = quarter_max_week.reindex(fallback.index).fillna(fallback).astype(int)

    final = coverage[
        coverage["week_of_quarter"] == coverage["quarter"].map(quarter_max_week)
    ]
    summary = (
        final.groupby("quarter")[["open_pipe", "booked", "target_usd"]]
        .sum()
        .div(1_000_000)
        .round(2)
    )
    print(summary.to_string())


if __name__ == "__main__":
    main()
