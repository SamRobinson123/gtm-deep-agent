"""Pull opportunity daily snapshots from Synapse."""

from pathlib import Path

import pandas as pd

SQL_DIR = Path(__file__).parent / "sql"


def pull_snapshot(conn, fy_start: str, fy_end: str, snap_start: str | None = None) -> pd.DataFrame:
    """Pull daily snapshot rows for the given fiscal-year window.

    `CloseDate` is bounded by `[fy_start, fy_end]`. `snapshot_date` is bounded by
    `[snap_start, fy_end]`, where `snap_start` defaults to `fy_start`. Passing an
    earlier `snap_start` (e.g. ~2 weeks before `fy_start`) additionally fetches
    the *prior* quarter-end snapshots for this FY's Q1-close deals — needed to
    anchor "week 1" open pipe to the end-of-prior-quarter snapshot (PBI's
    "starting pipe" convention, see PLAN.md §6.2). Because `CloseDate` stays
    bounded at `fy_start`, these extra rows never overlap the prior FY's pull.
    Dates as ISO strings (e.g. '2024-01-01').

    `Deal_Type` comes through as the raw `Opp_Type` value (`New Business`,
    `Expansion`, `Upsell`) — we do not rename to `New Customer` (see PLAN.md
    §3.4 / §8.4).
    """
    sql = (SQL_DIR / "snapshot.sql").read_text()
    sql = sql.format(fy_start=fy_start, fy_end=fy_end, snap_start=snap_start or fy_start)
    df = pd.read_sql(sql, conn)
    # Source currently returns every (opp, snapshot_date) row byte-identical
    # twice (upstream join blow-up), which doubles summed open_pipe/ls_pipe.
    # The documented grain is one row per (opp, snapshot_date) — collapse dupes.
    return df.drop_duplicates(ignore_index=True)


def pull_live_booked(conn, start_date: str, end_date: str) -> pd.DataFrame:
    """Pull live Closed Won bookings from `sku_nacv_fact` at product grain.

    Returns one row per product per opp whose current `Opp_Closed_Date` is in
    `[start_date, end_date]`. This is the live truth (current Salesforce state)
    used to override snapshot-derived booked numbers — see PLAN.md / discussion
    on `6 - Closed/Pending` phantom bookings.

    `Deal_Type` stays canonical (`New Business` not `New Customer`).
    """
    sql = (SQL_DIR / "live_booked.sql").read_text()
    sql = sql.format(start_date=start_date, end_date=end_date)
    return pd.read_sql(sql, conn)


def pull_opp_products(conn) -> pd.DataFrame:
    """Pull ALL product families per opportunity (one row per opp × canonical
    product) — the SKU-inclusive view the product win rate counts against.
    See `backend/sql/opp_products.sql` for why this exists alongside the
    snapshot's single-product-per-opp attribution.
    """
    sql = (SQL_DIR / "opp_products.sql").read_text()
    return pd.read_sql(sql, conn)
