"""Shared WoW delta merge for quarterly / product / deal-type tables (CLI + dashboard JSON)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass

TOTAL_PIPE_COV_CHANGE = "Total pipe coverage change"
LS_PIPE_COV_CHANGE = "LS pipe coverage change"

QUARTERLY_DELTA_SPECS: list[tuple[str, str]] = [
    ("Total Pipe Cov LTB", TOTAL_PIPE_COV_CHANGE),
    ("LS Pipe Cov LTB", LS_PIPE_COV_CHANGE),
]
QUARTERLY_COL_ORDER: list[str] = [
    "Region",
    "Team",
    "Total Pipe",
    "LS Pipe",
    "QTD Booked",
    "Target",
    "LTB",
    "Total Pipe Cov LTB",
    TOTAL_PIPE_COV_CHANGE,
    "LS Pipe Cov LTB",
    LS_PIPE_COV_CHANGE,
]

PRODUCT_DELTA_SPECS: list[tuple[str, str]] = [
    ("Total Pipe Cov", TOTAL_PIPE_COV_CHANGE),
    ("LS Pipe Cov", LS_PIPE_COV_CHANGE),
]
PRODUCT_COL_ORDER: list[str] = [
    "Product",
    "Geo",
    "Total Pipe",
    "LS Pipe",
    "ACV",
    "Target",
    "Total Pipe Cov",
    TOTAL_PIPE_COV_CHANGE,
    "LS Pipe Cov",
    LS_PIPE_COV_CHANGE,
]

DEAL_DELTA_SPECS: list[tuple[str, str]] = [
    ("Total Pipe Cov", TOTAL_PIPE_COV_CHANGE),
    ("LS Pipe Cov", LS_PIPE_COV_CHANGE),
]
DEAL_COL_ORDER: list[str] = [
    "Geo",
    "Type",
    "Total Pipe",
    "LS Pipe",
    "ACV",
    "Target",
    "Total Pipe Cov",
    TOTAL_PIPE_COV_CHANGE,
    "LS Pipe Cov",
    LS_PIPE_COV_CHANGE,
]


def _prior_for_wow_merge(prior: pd.DataFrame | None) -> pd.DataFrame | None:
    if prior is None or prior.empty:
        return None
    p = prior.drop(columns=["run_date", "fy", "quarter"], errors="ignore").copy()
    strip = [
        c
        for c in p.columns
        if str(c).startswith("Δ") or c in (TOTAL_PIPE_COV_CHANGE, LS_PIPE_COV_CHANGE)
    ]
    p = p.drop(columns=strip, errors="ignore")
    return p


def insert_delta_columns(
    current: pd.DataFrame,
    prior: pd.DataFrame | None,
    keys: list[str],
    delta_specs: list[tuple[str, str]],
    column_order: list[str],
) -> pd.DataFrame:
    """Add Δ columns vs prior snapshot row (matched on keys). Missing prior → zeros."""
    base_cols = [b for b, _ in delta_specs]

    def _zeros() -> pd.DataFrame:
        out = current.copy()
        for _, dcol in delta_specs:
            out[dcol] = 0.0
        ordered = [c for c in column_order if c in out.columns]
        tail = [c for c in out.columns if c not in ordered]
        return out[ordered + tail]

    p = _prior_for_wow_merge(prior)
    if p is None:
        return _zeros()

    need = keys + base_cols
    miss = [c for c in need if c not in p.columns or c not in current.columns]
    if miss:
        return _zeros()

    out = current.copy()
    for c in keys:
        out[c] = out[c].astype(str)
        p[c] = p[c].astype(str)

    rename_map = {b: f"_prior_{b}" for b in base_cols}
    merged = out.merge(p[keys + base_cols].rename(columns=rename_map), on=keys, how="left")
    for base, dcol in delta_specs:
        prior_vals = merged[f"_prior_{base}"]
        merged[dcol] = merged[base] - prior_vals.fillna(0)
    merged = merged.drop(columns=[f"_prior_{b}" for b in base_cols])

    ordered = [c for c in column_order if c in merged.columns]
    tail = [c for c in merged.columns if c not in ordered]
    return merged[ordered + tail]
