#!/usr/bin/env python3
"""CLI: pull Synapse pipeline, build tables, WoW deltas, HTML dashboard, snapshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Project root = parent of scripts/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dashboard_data import build_dashboard_data  # noqa: E402
from src.dashboard_render import render_dashboard_html  # noqa: E402
from src.pipeline import (  # noqa: E402
    DEAL_TYPE_TARGETS_M,
    PRODUCT_TARGETS_M,
    TARGETS_M,
    build_deal_type_table,
    build_product_table,
    build_quarterly_table,
    filter_to_snapshot_date,
    pull_snapshot_data,
    quarterly_pipe_reconciliation,
    resolve_snapshot_date,
)
from src.output_rotation import rotate  # noqa: E402
from src.snapshots import save_snapshot  # noqa: E402
from src.wow_merge import (  # noqa: E402
    DEAL_COL_ORDER,
    PRODUCT_COL_ORDER,
    QUARTERLY_COL_ORDER,
    DEAL_DELTA_SPECS,
    PRODUCT_DELTA_SPECS,
    QUARTERLY_DELTA_SPECS,
    insert_delta_columns,
)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = ROOT / ".env"
    if env_path.is_file():
        load_dotenv(env_path)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GTM Weekly Reporting — Synapse pull, HTML, snapshots.")
    p.add_argument("--as-of", dest="as_of", metavar="YYYY-MM-DD", help="Override today's date (backfills)")
    p.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), help="Override auto-detected quarter")
    p.add_argument("--no-snapshot", action="store_true", help="Do not write snapshot Excel workbook")
    p.add_argument(
        "--no-rotate",
        action="store_true",
        help="Do not archive old files under output/ (snapshots, dashboards, weekly)",
    )
    p.add_argument("--output-dir", type=Path, default=None, help="Override output root (default: <project>/output)")
    p.add_argument(
        "--reconcile",
        action="store_true",
        help="After the pull, print open-pipe reconciliation (quarterly Core Total vs full slice, unmapped teams)",
    )
    return p.parse_args()


def _fy_quarter(as_of: pd.Timestamp, quarter_override: int | None) -> tuple[int, int]:
    as_of = pd.Timestamp(as_of).normalize()
    fy = int(as_of.year)
    q = quarter_override if quarter_override is not None else int((as_of.month - 1) // 3 + 1)
    return fy, q


def _print_unmapped_booking_teams(df: pd.DataFrame) -> None:
    """
    After pull + enrichment, warn if any Booking_Team_Static is not in REGION_FAMILY_MAP
    (Region Family is NaN). Those rows are dropped from the quarterly table groupby.
    """
    miss = df["Region Family"].isna()
    if not miss.any():
        print("Mapping check: all Booking_Team_Static values are covered by REGION_FAMILY_MAP.")
        return
    sub = df.loc[miss, "Booking_Team_Static"]
    counts = sub.value_counts(dropna=False).sort_values(ascending=False)
    print("Mapping check: unmapped Booking_Team_Static (not in REGION_FAMILY_MAP) —", file=sys.stderr, end=" ")
    print(
        f"{len(counts)} distinct value(s); rows excluded from Quarterly Summary rollups / Core Total.",
        file=sys.stderr,
    )
    for team, n in counts.items():
        label = "(null)" if pd.isna(team) else str(team)
        print(f"  • {label} — {int(n):,} row(s)", file=sys.stderr)
    print(
        "  Fix: add entries to REGION_FAMILY_MAP in src/pipeline.py.",
        file=sys.stderr,
    )


def _save_weekly_wow_workbook(
    quarterly: pd.DataFrame,
    product: pd.DataFrame,
    deal_type: pd.DataFrame,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        quarterly.to_excel(writer, sheet_name="Quarterly", index=False)
        product.to_excel(writer, sheet_name="Product", index=False)
        deal_type.to_excel(writer, sheet_name="Deal Type", index=False)


def main() -> int:
    _load_env()
    args = _parse_args()

    as_of = pd.Timestamp(args.as_of).normalize() if args.as_of else pd.Timestamp.today().normalize()
    fy, quarter = _fy_quarter(as_of, args.quarter)

    print(f"=== GTM Weekly Report Run: {as_of.strftime('%Y-%m-%d')} ===")

    out_root = Path(args.output_dir).resolve() if args.output_dir else (ROOT / "output")
    dashboards_dir = out_root / "dashboards"
    weekly_dir = out_root / "weekly"
    dashboards_dir.mkdir(parents=True, exist_ok=True)

    fy_start = pd.Timestamp(fy, 1, 1)
    fy_end = pd.Timestamp(fy, 12, 31)

    snap_df_all = pull_snapshot_data(fy_start, fy_end)
    if snap_df_all.empty:
        print("Error: Synapse snapshot range pull returned no rows.", file=sys.stderr)
        return 1

    print(
        f"Pulled {len(snap_df_all):,} snapshot rows across {snap_df_all['snapshot_date'].nunique()} "
        f"snapshot dates ({snap_df_all['snapshot_date'].min().date()} -> "
        f"{snap_df_all['snapshot_date'].max().date()})"
    )

    try:
        live_date, live_is_nearest = resolve_snapshot_date(snap_df_all, as_of)
        prior_date, prior_is_nearest = resolve_snapshot_date(
            snap_df_all, as_of - pd.Timedelta(days=7)
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(
        f"Live: requested {as_of.date()} | actual {live_date.date()}"
        f"{' (nearest available)' if live_is_nearest else ''}"
    )
    print(
        f"Snapshot baseline: requested {(as_of - pd.Timedelta(days=7)).date()} | "
        f"actual {prior_date.date()}{' (nearest available)' if prior_is_nearest else ''}"
    )

    df = filter_to_snapshot_date(snap_df_all, live_date)
    prior_df = filter_to_snapshot_date(snap_df_all, prior_date)
    _print_unmapped_booking_teams(df)

    if args.reconcile:
        print("--- Quarterly open-pipe reconciliation (all quarters) ---")
        for qn in (1, 2, 3, 4):
            rec = quarterly_pipe_reconciliation(df, fy, qn, targets=TARGETS_M)
            print(
                f"FY{fy} Q{qn}: open pipe (all rows) ${rec['open_pipe_all_rows_m']:,.3f}M | "
                f"Quarterly Core Total ${rec['open_pipe_core_total_m']:,.3f}M | "
                f"gap ${rec['gap_open_pipe_m']:,.3f}M | "
                f"unmapped teams ${rec['open_pipe_unmapped_teams_m']:,.3f}M",
            )
            teams = rec["top_unmapped_teams_open_m"]
            if teams:
                for name, amt in teams[:15]:
                    print(f"    • {name}: ${amt:,.3f}M")
            elif abs(rec["gap_open_pipe_m"]) > 1e-6:
                print(
                    "    (gap exists but unmapped total is 0 — check Region/Family NaN outside Region Family.)",
                    file=sys.stderr,
                )
        print("--- end reconciliation ---\n")

    quarterly_tables: dict[int, pd.DataFrame] = {}
    product_tables: dict[int, pd.DataFrame] = {}
    deal_type_tables: dict[int, pd.DataFrame] = {}
    for qn in (1, 2, 3, 4):
        quarterly_tables[qn] = build_quarterly_table(df, fy, qn, TARGETS_M)
        product_tables[qn] = build_product_table(df, fy, qn, PRODUCT_TARGETS_M)
        deal_type_tables[qn] = build_deal_type_table(df, fy, qn, DEAL_TYPE_TARGETS_M)

    prior_quarterly_tables: dict[int, pd.DataFrame] = {}
    prior_product_tables: dict[int, pd.DataFrame] = {}
    prior_deal_type_tables: dict[int, pd.DataFrame] = {}
    for qn in (1, 2, 3, 4):
        prior_quarterly_tables[qn] = build_quarterly_table(prior_df, fy, qn, TARGETS_M)
        prior_product_tables[qn] = build_product_table(prior_df, fy, qn, PRODUCT_TARGETS_M)
        prior_deal_type_tables[qn] = build_deal_type_table(prior_df, fy, qn, DEAL_TYPE_TARGETS_M)

    pri_q = prior_quarterly_tables[quarter]
    pri_p = prior_product_tables[quarter]
    pri_d = prior_deal_type_tables[quarter]

    q_tbl = quarterly_tables[quarter]
    p_tbl = product_tables[quarter]
    d_tbl = deal_type_tables[quarter]

    q_show = insert_delta_columns(
        quarterly_tables[quarter],
        pri_q,
        ["Region", "Team"],
        QUARTERLY_DELTA_SPECS,
        QUARTERLY_COL_ORDER,
    )
    p_show = insert_delta_columns(
        product_tables[quarter],
        pri_p,
        ["Product", "Geo"],
        PRODUCT_DELTA_SPECS,
        PRODUCT_COL_ORDER,
    )
    d_show = insert_delta_columns(
        deal_type_tables[quarter],
        pri_d,
        ["Geo", "Type"],
        DEAL_DELTA_SPECS,
        DEAL_COL_ORDER,
    )
    wow_tables = (q_show, p_show, d_show)

    live_tables_by_quarter = {
        q: {
            "quarterly": quarterly_tables[q],
            "product": product_tables[q],
            "deal_type": deal_type_tables[q],
        }
        for q in (1, 2, 3, 4)
    }
    prior_tables_by_quarter = {
        q: {
            "quarterly": prior_quarterly_tables[q],
            "product": prior_product_tables[q],
            "deal_type": prior_deal_type_tables[q],
        }
        for q in (1, 2, 3, 4)
    }

    data = build_dashboard_data(
        df_live=df,
        df_prior=prior_df,
        fy=fy,
        current_quarter=quarter,
        as_of=as_of,
        live_date=live_date,
        prior_date=prior_date,
        run_at_utc=pd.Timestamp.now("UTC"),
        targets=TARGETS_M,
        product_targets=PRODUCT_TARGETS_M,
        deal_type_targets=DEAL_TYPE_TARGETS_M,
        live_is_nearest=live_is_nearest,
        prior_is_nearest=prior_is_nearest,
        live_tables=live_tables_by_quarter,
        prior_tables=prior_tables_by_quarter,
    )

    template_path = ROOT / "handoff" / "preview_dashboard_v3.html"
    if not template_path.is_file():
        print(f"Error: dashboard template missing: {template_path}", file=sys.stderr)
        return 1

    html_path = dashboards_dir / f"GTM_Weekly_FY{fy}_Q{quarter}_{as_of.strftime('%Y-%m-%d')}.html"
    render_dashboard_html(data, template_path, html_path)

    wow_path = weekly_dir / f"GTM_Weekly_WoW_FY{fy}_Q{quarter}_{as_of.strftime('%Y-%m-%d')}.xlsx"
    _save_weekly_wow_workbook(wow_tables[0], wow_tables[1], wow_tables[2], wow_path)

    snap_paths: list[Path] = []
    if not args.no_snapshot:
        snapshot_tables: dict[str, pd.DataFrame] = {
            "quarterly": q_tbl,
            "product": p_tbl,
            "deal_type": d_tbl,
        }
        for qn in (1, 2, 3, 4):
            snapshot_tables[f"quarterly_q{qn}"] = quarterly_tables[qn]
            snapshot_tables[f"product_q{qn}"] = product_tables[qn]
            snapshot_tables[f"deal_type_q{qn}"] = deal_type_tables[qn]
        snap_paths = save_snapshot(
            snapshot_tables,
            run_date=as_of,
            fy=fy,
            quarter=quarter,
            output_dir=out_root,
        )

    print()
    print(f"HTML dashboard: {html_path}")
    print(f"Weekly WoW Excel: {wow_path}")
    if snap_paths:
        print("Snapshot workbook (single file with all quarters for Quarterly / Product / Deal Type):")
        for sp in snap_paths:
            print(f"  {sp}")
    elif args.no_snapshot:
        print("Snapshots skipped (--no-snapshot).")
    print()
    print("Reconciliation (notebook Section 8):")
    q_open = df[(df["FY"] == fy) & (df["Quarter"] == quarter) & (df["Stage"] == "Open")]
    raw_total = q_open["Product_NACV"].sum() / 1e6
    team_total = quarterly_tables[quarter].iloc[-1]["Total Pipe"]
    prod_total = product_tables[quarter].iloc[-1]["Total Pipe"]
    deal_total = deal_type_tables[quarter].iloc[-1]["Total Pipe"]
    print(f"Raw open pipe FY{str(fy)[-2:]} Q{quarter}:  ${raw_total:.2f}M")
    print(f"Team Core Total:        ${team_total:.2f}M  (delta {team_total - raw_total:+.2f})")
    print(f"Product Grand Total:    ${prod_total:.2f}M  (delta {prod_total - raw_total:+.2f})")
    print(f"Deal Type Total:        ${deal_total:.2f}M  (delta {deal_total - raw_total:+.2f})")

    if not args.no_rotate:
        rotated = rotate(out_root, dry_run=False)
        total_moved = sum(len(v) for v in rotated.values())
        if total_moved:
            print()
            print(f"Output rotation: archived {total_moved} file(s) to output/*/_archive/")
            for subdir, paths in rotated.items():
                if paths:
                    print(f"  {subdir}: {len(paths)}")
        else:
            print()
            print("Output rotation: nothing to archive (within retention limits).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
