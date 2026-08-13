"""Build JSON for `handoff/preview_dashboard_v3.html` (schema per CURSOR_PROMPT_v3_shadcn.md)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.pipeline import (
    REGION_FAMILY_MAP,
    STATIC_TARGETS_M,
    build_deal_type_table,
    build_product_table,
    build_quarterly_table,
    region_of,
)
from src.wow_merge import (
    DEAL_COL_ORDER,
    DEAL_DELTA_SPECS,
    LS_PIPE_COV_CHANGE,
    PRODUCT_COL_ORDER,
    PRODUCT_DELTA_SPECS,
    QUARTERLY_COL_ORDER,
    QUARTERLY_DELTA_SPECS,
    TOTAL_PIPE_COV_CHANGE,
    insert_delta_columns,
)

# Static drill-down: same coverage Δ semantics as quarterly (insert_delta_columns on static grain).
STATIC_KEYS = ["team", "static"]
STATIC_COL_ORDER: list[str] = [
    "team",
    "static",
    "Total Pipe",
    "LS Pipe",
    "QTD Booked",
    "Target",
    "LTB",
    "Total Pipe Cov LTB",
    "LS Pipe Cov LTB",
    TOTAL_PIPE_COV_CHANGE,
    LS_PIPE_COV_CHANGE,
]


def _nan_to_none(x: Any) -> Any:
    if x is None:
        return None
    if isinstance(x, (float, np.floating)):
        if pd.isna(x) or (isinstance(x, float) and math.isnan(x)):
            return None
        return float(x)
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return {k: _nan_to_none(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_nan_to_none(v) for v in x]
    return x


def _prior_leaf_metric(x: Any) -> float:
    """Serialize prior pipe/ls/qtd (or acv) for JSON: finite float, else 0 (for JS aggregation)."""
    if x is None:
        return 0.0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(v) or pd.isna(v):
        return 0.0
    return v


def _strip_delta_if_no_prior(merged: pd.DataFrame, prior_ok: bool) -> pd.DataFrame:
    if prior_ok:
        return merged
    out = merged.copy()
    for col in (TOTAL_PIPE_COV_CHANGE, LS_PIPE_COV_CHANGE):
        if col in out.columns:
            out[col] = np.nan
    return out


def _derive_filter_universe(df_live: pd.DataFrame) -> tuple[list[str], dict[str, list[str]], dict[str, list[str]]]:
    teams_by_geo: dict[str, set[str]] = {}
    static_by_team: dict[str, set[str]] = {}

    sub = df_live[df_live["Booking_Team_Static"].notna()].copy()
    for static in sub["Booking_Team_Static"].unique():
        fam = REGION_FAMILY_MAP.get(static)
        if fam is None or pd.isna(fam):
            continue
        geo = region_of(fam)
        teams_by_geo.setdefault(geo, set()).add(str(fam))
        static_by_team.setdefault(str(fam), set()).add(str(static))

    geos = sorted(teams_by_geo.keys())
    teams_sorted = {g: sorted(teams_by_geo[g]) for g in geos}
    static_sorted = {t: sorted(static_by_team[t]) for t in sorted(static_by_team.keys())}
    return geos, teams_sorted, static_sorted


def _build_static_metrics_table(df: pd.DataFrame, fy: int, q: int) -> pd.DataFrame:
    """One row per mapped Booking_Team_Static with quarterly columns aligned to WoW merge."""
    sl = df[(df["FY"] == fy) & (df["Quarter"] == q)].copy()
    if sl.empty:
        return pd.DataFrame(
            columns=[
                "team",
                "static",
                "Total Pipe",
                "LS Pipe",
                "QTD Booked",
                "Target",
                "LTB",
                "Total Pipe Cov LTB",
                "LS Pipe Cov LTB",
            ]
        )
    sl["NACV_M"] = sl["Product_NACV"] / 1_000_000
    rows: list[dict[str, Any]] = []
    for static, g in sl.groupby("Booking_Team_Static", dropna=True):
        if pd.isna(static):
            continue
        fam = REGION_FAMILY_MAP.get(static)
        if fam is None or pd.isna(fam):
            continue
        open_m = g["Stage"] == "Open"
        won_m = g["Stage"] == "Closed Won"
        pipe = float(g.loc[open_m, "NACV_M"].sum())
        ls = float(g.loc[open_m & g["Is_LS"], "NACV_M"].sum())
        qtd = float(g.loc[won_m, "NACV_M"].sum())

        target = STATIC_TARGETS_M.get((fy, q, str(static)), np.nan)
        if pd.notna(target):
            ltb = float(target) - qtd
            total_cov = pipe / ltb if ltb > 0 else np.nan
            ls_cov = ls / ltb if ltb > 0 else np.nan
        else:
            ltb = np.nan
            total_cov = np.nan
            ls_cov = np.nan

        rows.append(
            {
                "team": str(fam),
                "static": str(static),
                "Total Pipe": pipe,
                "LS Pipe": ls,
                "QTD Booked": qtd,
                "Target": float(target) if pd.notna(target) else np.nan,
                "LTB": ltb,
                "Total Pipe Cov LTB": total_cov,
                "LS Pipe Cov LTB": ls_cov,
            }
        )
    return pd.DataFrame(rows)


def _product_table_leaf_unique(tbl: pd.DataFrame) -> pd.DataFrame:
    """Stacked product table -> one row per (Product, Geo) so WoW merge keys are unique."""
    if tbl.empty:
        return tbl.copy()
    out_rows: list[dict[str, Any]] = []
    current_product: str | None = None
    for _, row in tbl.iterrows():
        pr = row.get("Product")
        geo = row.get("Geo")
        if pr == "Grand Total":
            continue
        ps = "" if pd.isna(pr) else str(pr)
        gs = "" if pd.isna(geo) else str(geo)
        if ps != "" and gs == "":
            current_product = ps
            continue
        if ps == "" and gs != "" and current_product is not None:
            d = row.to_dict()
            d["Product"] = current_product
            d["Geo"] = gs
            out_rows.append(d)
    if not out_rows:
        return pd.DataFrame(columns=tbl.columns)
    return pd.DataFrame(out_rows)


def _deal_geo_label(geo_cell: Any) -> str:
    s = str(geo_cell) if geo_cell is not None and not pd.isna(geo_cell) else ""
    if s == "Pubsec":
        return "Public Sector"
    return s


def _deal_type_table_leaf_unique(tbl: pd.DataFrame) -> pd.DataFrame:
    """Stacked deal-type table -> one row per (Geo, Type) with display Geo for merge keys."""
    if tbl.empty:
        return tbl.copy()
    out_rows: list[dict[str, Any]] = []
    current_geo: str | None = None
    for _, row in tbl.iterrows():
        g = row.get("Geo")
        typ = row.get("Type")
        gs = "" if pd.isna(g) else str(g)
        ts = "" if pd.isna(typ) else str(typ)
        if gs == "Total":
            continue
        if gs != "" and ts == "":
            current_geo = _deal_geo_label(gs)
            continue
        if gs == "" and ts != "" and current_geo is not None:
            d = row.to_dict()
            d["Geo"] = current_geo
            d["Type"] = ts
            out_rows.append(d)
    if not out_rows:
        return pd.DataFrame(columns=tbl.columns)
    return pd.DataFrame(out_rows)


def _static_rows_for_quarter(
    df_live: pd.DataFrame,
    df_prior: pd.DataFrame,
    fy: int,
    q: int,
    prior_ok: bool,
) -> list[dict[str, Any]]:
    live_tbl = _build_static_metrics_table(df_live, fy, q)
    if live_tbl.empty:
        return []
    prior_tbl = _build_static_metrics_table(df_prior, fy, q) if prior_ok else None
    merged = insert_delta_columns(
        live_tbl,
        prior_tbl,
        STATIC_KEYS,
        QUARTERLY_DELTA_SPECS,
        STATIC_COL_ORDER,
    )
    merged = _strip_delta_if_no_prior(merged, prior_ok)
    work = merged.copy()
    if prior_ok and prior_tbl is not None and not prior_tbl.empty:
        pr = prior_tbl[STATIC_KEYS + ["Total Pipe", "LS Pipe", "QTD Booked"]].copy()
        for k in STATIC_KEYS:
            pr[k] = pr[k].astype(str)
            work[k] = work[k].astype(str)
        pr = pr.rename(
            columns={
                "Total Pipe": "_pipePrior",
                "LS Pipe": "_lsPrior",
                "QTD Booked": "_qtdPrior",
            }
        )
        work = work.merge(pr, on=STATIC_KEYS, how="left")
    else:
        work["_pipePrior"] = 0.0
        work["_lsPrior"] = 0.0
        work["_qtdPrior"] = 0.0
    for c in ("_pipePrior", "_lsPrior", "_qtdPrior"):
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)

    rows_out: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        td = _nan_to_none(row.get(TOTAL_PIPE_COV_CHANGE))
        ld = _nan_to_none(row.get(LS_PIPE_COV_CHANGE))
        if not prior_ok:
            td = None
            ld = None
        rows_out.append(
            {
                "team": str(row["team"]),
                "static": str(row["static"]),
                "pipe": _nan_to_none(row.get("Total Pipe")),
                "ls": _nan_to_none(row.get("LS Pipe")),
                "qtd": _nan_to_none(row.get("QTD Booked")),
                "target": _nan_to_none(row.get("Target")),
                "ltb": _nan_to_none(row.get("LTB")),
                "totalCov": _nan_to_none(row.get("Total Pipe Cov LTB")),
                "lsCov": _nan_to_none(row.get("LS Pipe Cov LTB")),
                "totalDelta": td,
                "lsDelta": ld,
                "pipePrior": _prior_leaf_metric(row.get("_pipePrior")),
                "lsPrior": _prior_leaf_metric(row.get("_lsPrior")),
                "qtdPrior": _prior_leaf_metric(row.get("_qtdPrior")),
            }
        )
    rows_out.sort(key=lambda x: (x["team"], x["static"]))
    return rows_out


def _product_rows_from_flat_merged(
    merged: pd.DataFrame,
    prior_ok: bool,
    p_flat_prior: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """One JSON row per row of flattened product WoW merge (unique Product × Geo)."""
    work = merged.copy()
    keys = ["Product", "Geo"]
    if prior_ok and p_flat_prior is not None and not p_flat_prior.empty:
        pr = p_flat_prior[keys + ["Total Pipe", "LS Pipe", "ACV"]].copy()
        for k in keys:
            pr[k] = pr[k].astype(str)
            work[k] = work[k].astype(str)
        pr = pr.rename(
            columns={
                "Total Pipe": "_pipePrior",
                "LS Pipe": "_lsPrior",
                "ACV": "_acvPrior",
            }
        )
        work = work.merge(pr, on=keys, how="left")
    else:
        work["_pipePrior"] = 0.0
        work["_lsPrior"] = 0.0
        work["_acvPrior"] = 0.0
    for c in ("_pipePrior", "_lsPrior", "_acvPrior"):
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)

    out: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        td = _nan_to_none(row.get(TOTAL_PIPE_COV_CHANGE))
        ld = _nan_to_none(row.get(LS_PIPE_COV_CHANGE))
        if not prior_ok:
            td = None
            ld = None
        out.append(
            {
                "product": str(row["Product"]),
                "geo": str(row["Geo"]),
                "pipe": _nan_to_none(row.get("Total Pipe")),
                "ls": _nan_to_none(row.get("LS Pipe")),
                "acv": _nan_to_none(row.get("ACV")),
                "target": _nan_to_none(row.get("Target")),
                "totalCov": _nan_to_none(row.get("Total Pipe Cov")),
                "lsCov": _nan_to_none(row.get("LS Pipe Cov")),
                "totalDelta": td,
                "lsDelta": ld,
                "pipePrior": _prior_leaf_metric(row.get("_pipePrior")),
                "lsPrior": _prior_leaf_metric(row.get("_lsPrior")),
                "acvPrior": _prior_leaf_metric(row.get("_acvPrior")),
            }
        )
    return out


def _rollup_team_metrics_from_static_rows(
    static_rows: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Sum pipe / ls / qtd per rolled-up team from dashboard staticRows."""
    out: dict[str, dict[str, float]] = {}
    for r in static_rows:
        t = str(r["team"])
        if t not in out:
            out[t] = {"pipe": 0.0, "ls": 0.0, "qtd": 0.0}
        out[t]["pipe"] += float(r.get("pipe") or 0)
        out[t]["ls"] += float(r.get("ls") or 0)
        out[t]["qtd"] += float(r.get("qtd") or 0)
    return out


def _rollup_team_metrics_from_df(df: pd.DataFrame, fy: int, q: int) -> dict[str, dict[str, float]]:
    """Prior-week team totals (same grain as staticRows, grouped by rolled-up team)."""
    tbl = _build_static_metrics_table(df, fy, q)
    if tbl.empty:
        return {}
    out: dict[str, dict[str, float]] = {}
    for _, row in tbl.iterrows():
        t = str(row["team"])
        if t not in out:
            out[t] = {"pipe": 0.0, "ls": 0.0, "qtd": 0.0}
        out[t]["pipe"] += float(row["Total Pipe"] or 0)
        out[t]["ls"] += float(row["LS Pipe"] or 0)
        out[t]["qtd"] += float(row["QTD Booked"] or 0)
    return out


def _ltb_cov(pipe: float, ls: float, qtd: float, target: Any) -> tuple[float, float, float]:
    """LTB and coverage ratios (same semantics as build_quarterly_table)."""
    if target is None or pd.isna(target):
        return (float("nan"), float("nan"), float("nan"))
    tgt = float(target)
    ltb = tgt - float(qtd)
    if ltb <= 0 or np.isnan(ltb):
        return (ltb, float("nan"), float("nan"))
    return (ltb, float(pipe) / ltb, float(ls) / ltb)


def _quarterly_rows_from_teams_and_static(
    geos: list[str],
    teams_by_geo: dict[str, list[str]],
    static_live_rows: list[dict[str, Any]],
    df_prior: pd.DataFrame,
    fy: int,
    q: int,
    targets: dict,
    prior_ok: bool,
) -> list[dict[str, Any]]:
    """One row per (geo, team) in teamsByGeo; metrics summed from staticRows (live) vs prior df."""
    live_by_team = _rollup_team_metrics_from_static_rows(static_live_rows)
    prior_by_team = _rollup_team_metrics_from_df(df_prior, fy, q) if prior_ok else {}

    rows_out: list[dict[str, Any]] = []
    for geo in geos:
        for team in teams_by_geo.get(geo, []):
            lm = live_by_team.get(team, {"pipe": 0.0, "ls": 0.0, "qtd": 0.0})
            pipe = lm["pipe"]
            ls = lm["ls"]
            qtd = lm["qtd"]
            tgt = targets.get((fy, q, team), np.nan)
            ltb, tc_f, lc_f = _ltb_cov(pipe, ls, qtd, tgt)

            pm = prior_by_team.get(team, {"pipe": 0.0, "ls": 0.0, "qtd": 0.0})
            ptgt = targets.get((fy, q, team), np.nan)
            _, p_tc_f, p_lc_f = _ltb_cov(pm["pipe"], pm["ls"], pm["qtd"], ptgt)

            td: Any = None
            ld: Any = None
            if prior_ok:
                if not (np.isnan(tc_f) or np.isnan(p_tc_f)):
                    td = float(tc_f - p_tc_f)
                if not (np.isnan(lc_f) or np.isnan(p_lc_f)):
                    ld = float(lc_f - p_lc_f)

            pp = float(pm["pipe"]) if prior_ok else 0.0
            lp = float(pm["ls"]) if prior_ok else 0.0
            qp = float(pm["qtd"]) if prior_ok else 0.0
            rows_out.append(
                {
                    "geo": geo,
                    "team": team,
                    "pipe": _nan_to_none(pipe),
                    "ls": _nan_to_none(ls),
                    "qtd": _nan_to_none(qtd),
                    "target": _nan_to_none(tgt),
                    "ltb": _nan_to_none(ltb),
                    "totalCov": _nan_to_none(tc_f),
                    "lsCov": _nan_to_none(lc_f),
                    "totalDelta": _nan_to_none(td),
                    "lsDelta": _nan_to_none(ld),
                    "pipePrior": pp,
                    "lsPrior": lp,
                    "qtdPrior": qp,
                }
            )
    return rows_out


def _deal_rows_from_flat_merged(
    merged: pd.DataFrame,
    prior_ok: bool,
    d_flat_prior: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """One JSON row per row of flattened deal-type WoW merge (unique Geo × Type)."""
    work = merged.copy()
    keys = ["Geo", "Type"]
    if prior_ok and d_flat_prior is not None and not d_flat_prior.empty:
        pr = d_flat_prior[keys + ["Total Pipe", "LS Pipe", "ACV"]].copy()
        for k in keys:
            pr[k] = pr[k].astype(str)
            work[k] = work[k].astype(str)
        pr = pr.rename(
            columns={
                "Total Pipe": "_pipePrior",
                "LS Pipe": "_lsPrior",
                "ACV": "_acvPrior",
            }
        )
        work = work.merge(pr, on=keys, how="left")
    else:
        work["_pipePrior"] = 0.0
        work["_lsPrior"] = 0.0
        work["_acvPrior"] = 0.0
    for c in ("_pipePrior", "_lsPrior", "_acvPrior"):
        work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0.0)

    out: list[dict[str, Any]] = []
    for _, row in work.iterrows():
        td = _nan_to_none(row.get(TOTAL_PIPE_COV_CHANGE))
        ld = _nan_to_none(row.get(LS_PIPE_COV_CHANGE))
        if not prior_ok:
            td = None
            ld = None
        out.append(
            {
                "geo": str(row["Geo"]),
                "dealType": str(row["Type"]),
                "pipe": _nan_to_none(row.get("Total Pipe")),
                "ls": _nan_to_none(row.get("LS Pipe")),
                "acv": _nan_to_none(row.get("ACV")),
                "target": _nan_to_none(row.get("Target")),
                "totalCov": _nan_to_none(row.get("Total Pipe Cov")),
                "lsCov": _nan_to_none(row.get("LS Pipe Cov")),
                "totalDelta": td,
                "lsDelta": ld,
                "pipePrior": _prior_leaf_metric(row.get("_pipePrior")),
                "lsPrior": _prior_leaf_metric(row.get("_lsPrior")),
                "acvPrior": _prior_leaf_metric(row.get("_acvPrior")),
            }
        )
    return out


def _kpis_from_quarterly_merged(q_merged: pd.DataFrame, prior_raw: pd.DataFrame | None) -> dict[str, Any]:
    core = q_merged.loc[q_merged["Region"] == "Core Total"]
    if core.empty:
        return {
            "totalPipe": None,
            "lsPipe": None,
            "totalCov": None,
            "lsCov": None,
            "totalCovDelta": None,
            "lsCovDelta": None,
            "lsShare": None,
        }
    row = core.iloc[0]
    tp = row.get("Total Pipe")
    lp = row.get("LS Pipe")
    ls_share = None
    if pd.notna(tp) and tp != 0 and pd.notna(lp):
        ls_share = float(lp) / float(tp)
    prior_ok = prior_raw is not None and not prior_raw.empty
    d_t = row.get(TOTAL_PIPE_COV_CHANGE)
    d_l = row.get(LS_PIPE_COV_CHANGE)
    if not prior_ok:
        d_t = np.nan
        d_l = np.nan
    return {
        "totalPipe": _nan_to_none(tp),
        "lsPipe": _nan_to_none(lp),
        "totalCov": _nan_to_none(row.get("Total Pipe Cov LTB")),
        "lsCov": _nan_to_none(row.get("LS Pipe Cov LTB")),
        "totalCovDelta": _nan_to_none(d_t),
        "lsCovDelta": _nan_to_none(d_l),
        "lsShare": _nan_to_none(ls_share),
    }


def _build_all_quarter_tables(
    df: pd.DataFrame,
    fy: int,
    *,
    targets: dict,
    product_targets: dict,
    deal_type_targets: dict,
) -> dict[int, dict[str, pd.DataFrame]]:
    """Build quarterly/product/deal_type tables for all four quarters from one DataFrame."""
    out: dict[int, dict[str, pd.DataFrame]] = {}
    for q in (1, 2, 3, 4):
        out[q] = {
            "quarterly": build_quarterly_table(df, fy, q, targets),
            "product": build_product_table(df, fy, q, product_targets),
            "deal_type": build_deal_type_table(df, fy, q, deal_type_targets),
        }
    return out


def build_dashboard_data(
    df_live: pd.DataFrame,
    df_prior: pd.DataFrame,
    *,
    fy: int,
    current_quarter: int,
    as_of: pd.Timestamp,
    live_date: pd.Timestamp,
    prior_date: pd.Timestamp,
    run_at_utc: pd.Timestamp,
    targets: dict,
    product_targets: dict,
    deal_type_targets: dict,
    live_is_nearest: bool,
    prior_is_nearest: bool,
    live_tables: dict[int, dict[str, pd.DataFrame]] | None = None,
    prior_tables: dict[int, dict[str, pd.DataFrame]] | None = None,
) -> dict[str, Any]:
    """
    Build the dashboard JSON payload.

    If live_tables and prior_tables are provided, they are used directly. Each is
    expected to have the shape:
        {1: {"quarterly": df, "product": df, "deal_type": df}, 2: {...}, ...}

    If not provided, tables are built from df_live / df_prior via build_*_table
    (backwards-compat for notebooks and other callers that only have raw DataFrames).
    """
    prior_ok = df_prior is not None and not df_prior.empty

    if live_tables is None:
        live_tables = _build_all_quarter_tables(
            df_live,
            fy,
            targets=targets,
            product_targets=product_targets,
            deal_type_targets=deal_type_targets,
        )
    if prior_ok and prior_tables is None:
        prior_tables = _build_all_quarter_tables(
            df_prior,
            fy,
            targets=targets,
            product_targets=product_targets,
            deal_type_targets=deal_type_targets,
        )

    lag_days = int(
        (pd.Timestamp(prior_date).normalize() - pd.Timestamp(live_date).normalize()).days
    )
    u_minus = "\u2212"
    if lag_days == 0:
        lag_label = "0 days"
    elif lag_days < 0:
        lag_label = f"{u_minus}{abs(lag_days)} day{'s' if abs(lag_days) != 1 else ''}"
    else:
        lag_label = f"+{lag_days} day{'s' if lag_days != 1 else ''}"

    products_sorted = sorted(str(p) for p in df_live["Product"].dropna().unique())
    deal_classes = df_live["Deal_Class"].dropna().unique()
    deal_types_list = sorted(str(x) for x in deal_classes if str(x) in ("New", "Existing"))

    geos, teams_by_geo, static_by_team = _derive_filter_universe(df_live)

    by_quarter: dict[str, dict[str, Any]] = {}
    kpis: dict[str, Any] = {}
    kpis_by_quarter: dict[str, Any] = {}

    for q in (1, 2, 3, 4):
        q_live_q = live_tables[q]["quarterly"]
        q_prior_q = prior_tables[q]["quarterly"] if prior_ok and prior_tables is not None else None
        q_merged = insert_delta_columns(
            q_live_q,
            q_prior_q,
            ["Region", "Team"],
            QUARTERLY_DELTA_SPECS,
            QUARTERLY_COL_ORDER,
        )
        q_merged = _strip_delta_if_no_prior(q_merged, prior_ok)

        p_live_q = live_tables[q]["product"]
        p_prior_q = prior_tables[q]["product"] if prior_ok and prior_tables is not None else None
        p_flat_live = _product_table_leaf_unique(p_live_q)
        p_flat_prior = _product_table_leaf_unique(p_prior_q) if prior_ok and p_prior_q is not None else None
        if p_flat_live.empty:
            p_merged = p_flat_live
        else:
            p_merged = insert_delta_columns(
                p_flat_live,
                p_flat_prior,
                ["Product", "Geo"],
                PRODUCT_DELTA_SPECS,
                PRODUCT_COL_ORDER,
            )
        p_merged = _strip_delta_if_no_prior(p_merged, prior_ok)

        d_live_q = live_tables[q]["deal_type"]
        d_prior_q = prior_tables[q]["deal_type"] if prior_ok and prior_tables is not None else None
        d_flat_live = _deal_type_table_leaf_unique(d_live_q)
        d_flat_prior = _deal_type_table_leaf_unique(d_prior_q) if prior_ok and d_prior_q is not None else None
        if d_flat_live.empty:
            d_merged = d_flat_live
        else:
            d_merged = insert_delta_columns(
                d_flat_live,
                d_flat_prior,
                ["Geo", "Type"],
                DEAL_DELTA_SPECS,
                DEAL_COL_ORDER,
            )
        d_merged = _strip_delta_if_no_prior(d_merged, prior_ok)

        static_rows_q = _static_rows_for_quarter(df_live, df_prior, fy, q, prior_ok)

        by_quarter[str(q)] = {
            "rows": _quarterly_rows_from_teams_and_static(
                geos, teams_by_geo, static_rows_q, df_prior, fy, q, targets, prior_ok
            ),
            "staticRows": static_rows_q,
            "productRows": _product_rows_from_flat_merged(
                p_merged, prior_ok, p_flat_prior if prior_ok else None
            ),
            "dealTypeRows": _deal_rows_from_flat_merged(
                d_merged, prior_ok, d_flat_prior if prior_ok else None
            ),
        }

        kpis_by_quarter[str(q)] = _kpis_from_quarterly_merged(q_merged, q_prior_q)

        if q == current_quarter:
            kpis = kpis_by_quarter[str(q)]

    data: dict[str, Any] = {
        "snapshot": {
            "runOn": pd.Timestamp(as_of).strftime("%Y-%m-%d"),
            "live": pd.Timestamp(live_date).strftime("%Y-%m-%d"),
            "baseline": pd.Timestamp(prior_date).strftime("%Y-%m-%d"),
            "runAt": run_at_utc.strftime("%H:%M UTC"),
            "lag": lag_label,
            "liveIsNearest": live_is_nearest,
            "priorIsNearest": prior_is_nearest,
        },
        "fiscalYear": fy,
        "quarter": current_quarter,
        "quarters": [1, 2, 3, 4],
        "geos": geos,
        "teamsByGeo": teams_by_geo,
        "staticByTeam": static_by_team,
        "dealTypes": deal_types_list,
        "products": products_sorted,
        "byQuarter": by_quarter,
        "kpis": kpis,
        "kpisByQuarter": kpis_by_quarter,
    }
    return _nan_to_none(data)
