"""Render the HTML dashboard from a coverage frame + recommendations.

The dashboard is focused on a single question: given historic conversion rates,
what coverage do we *need* to carry by (geo, deal_type) to hit target — and
how does our current quarter compare? Reads `output/coverage.parquet` and
`output/recommendations.parquet`, builds a compact JSON payload, injects it
into `frontend/dashboard_template.html`, writes `output/coverage_dashboard.html`.
"""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from backend.coverage_builder import _quarter_start

REPO = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = REPO / "frontend" / "dashboard_template.html"
DEFAULT_OUTPUT = REPO / "output" / "coverage_dashboard.html"
# The per-account tier source (rpt_cx.account_segment_quarterly) was removed from
# Synapse, so the live segment build collapses every opp to "Unassigned". When a
# frozen segment payload exists (captured from the last good dashboard via
# scripts/freeze_segment_payload.py) it is served verbatim instead of the broken
# live build. Delete this file once a replacement tier source is wired up.
FROZEN_SEGMENT_PATH = REPO / "data" / "inputs" / "segment_payload_frozen.json"

GEO_DISPLAY = [
    {"code": "All",           "label": "All Geos"},
    {"code": "AMS",           "label": "AMS"},
    {"code": "APAC",          "label": "APAC"},
    {"code": "EMEA",          "label": "EMEA"},
    {"code": "Public Sector", "label": "Public Sector"},
]
# Real geos only (excludes the "All" pseudo-geo) — used for the table that
# always shows every geo as a row, regardless of the filter selection.
GEO_DISPLAY_REAL = [g for g in GEO_DISPLAY if g["code"] != "All"]
DEAL_TYPE_DISPLAY = [
    {"code": "All", "label": "All Deal Types", "canonical": "All"},
    {"code": "NB",  "label": "New Business",   "canonical": "New Business"},
    {"code": "EX",  "label": "Expansion",      "canonical": "Expansion"},
    {"code": "UP",  "label": "Upsell",         "canonical": "Upsell"},
    {"code": "PS",  "label": "Prof Services",  "canonical": "Professional Services"},
]
# Real deal types only (excludes the "All" pseudo). Used in the detail table.
DEAL_TYPE_DISPLAY_REAL = [d for d in DEAL_TYPE_DISPLAY if d["code"] != "All"]

# Account-tier (segment) display — drives the Geo × Segment page. `canonical`
# matches the snapshot's `Quarter_Start_Segment` / segment-target values.
SEGMENT_DISPLAY = [
    {"code": "All", "label": "All Segments", "canonical": "All"},
    {"code": "T1",  "label": "Tier 1",       "canonical": "Tier 1"},
    {"code": "T2",  "label": "Tier 2",       "canonical": "Tier 2"},
    {"code": "T3",  "label": "Tier 3",       "canonical": "Tier 3"},
    # Accounts with no tier in account_segment_quarterly — carried so segment
    # pipe ties to the deal-type page; no segment target → no coverage.
    {"code": "UN",  "label": "Unassigned",   "canonical": "Unassigned"},
]
SEGMENT_DISPLAY_REAL = [s for s in SEGMENT_DISPLAY if s["code"] != "All"]

# Product display — drives the Product page. `canonical` matches the snapshot's
# canonical `Product` (and the product-target mapping). "Other" catches unknown
# snapshot products plus the unmatched target families (TTM4J/Legacy). Genuine
# no-product opps are funnel-cleaned away (→ Tosca / real families), so "Other"
# no longer holds null products. AI Credits is its own product (it has a target).
PRODUCT_DISPLAY = [
    {"code": "All", "label": "All Products",      "canonical": "All"},
    {"code": "TOS", "label": "Tosca",             "canonical": "Tosca"},
    {"code": "TST", "label": "Testim",            "canonical": "Testim"},
    {"code": "QT",  "label": "qTest",             "canonical": "qTest"},
    {"code": "NL",  "label": "NeoLoad",           "canonical": "NeoLoad"},
    {"code": "DI",  "label": "Data Integrity",    "canonical": "Data Integrity"},
    {"code": "LC",  "label": "LiveCompare",       "canonical": "LiveCompare"},
    {"code": "SL",  "label": "Sealights",         "canonical": "Sealights"},
    {"code": "VRA", "label": "Vera",              "canonical": "Vera"},
    {"code": "RS",  "label": "Recurring Services", "canonical": "Recurring Services"},
    {"code": "OTH", "label": "Other",             "canonical": "Other"},
]
PRODUCT_DISPLAY_REAL = [p for p in PRODUCT_DISPLAY if p["code"] != "All"]

QUARTER_ORDER = [
    "FY24 Q1", "FY24 Q2", "FY24 Q3", "FY24 Q4",
    "FY25 Q1", "FY25 Q2", "FY25 Q3", "FY25 Q4",
    "FY26 Q1", "FY26 Q2", "FY26 Q3", "FY26 Q4",
]

# A quarter is "sparse" when fewer than this many weeks have any open_pipe data.
# The `[rep].[trf_opp_daily_snapshot_new]` table only reached weekly cadence in
# April 2024, so quarters before that (currently just FY24 Q1) can have as few
# as 1-2 usable weekly snapshots. For those, we collapse the open_pipe / ls_pipe
# trajectory to a single "quarterly representative" value (the max across
# whatever weeks exist), shown as a flat line in the trajectory chart. Booked
# stays as the normal cumulative live curve. A banner in the dashboard explains
# the change so viewers don't mistake the flat line for a real curve.
SPARSE_WEEK_THRESHOLD = 8


def _series_to_list(s, max_weeks: int):
    out = [None] * max_weeks
    for w, v in s.items():
        if 1 <= w <= max_weeks:
            out[w - 1] = None if pd.isna(v) else float(v)
    return out


def _safe(v):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))) or pd.isna(v):
        return None
    return float(v)


def _sparsify_series(series: dict, weeks_in_quarter: int) -> dict:
    """Collapse a per-week series to a flat 'quarterly representative' value
    for sparse quarters. Mutates `series` in place and returns it.

    Replaces `openPipe` and `lsPipe` with the max non-null value across the
    available weeks, broadcast to all 13 weeks. Recomputes `totalCov` per week
    from the flat open_pipe and the per-week booked/target (booked is from
    live and is complete even when snapshots are sparse).
    """
    open_vals = [v for v in (series.get("openPipe") or []) if v is not None and v > 0]
    ls_vals   = [v for v in (series.get("lsPipe")   or []) if v is not None and v > 0]
    open_rep = max(open_vals) if open_vals else None
    ls_rep   = max(ls_vals)   if ls_vals   else None

    series["openPipe"] = [open_rep] * weeks_in_quarter
    series["lsPipe"]   = [ls_rep]   * weeks_in_quarter

    target = series.get("target")
    new_cov = [None] * weeks_in_quarter
    booked_arr = series.get("booked") or [None] * weeks_in_quarter
    if open_rep is not None and target is not None and target > 0:
        for w in range(weeks_in_quarter):
            b = booked_arr[w]
            if b is None:
                continue
            ltb = target - b
            if ltb > 0:
                new_cov[w] = open_rep / ltb
    series["totalCov"] = new_cov
    return series


def _aggregate_quarter(rows: pd.DataFrame, weeks_in_quarter: int) -> dict:
    """Weekly aggregate across whatever grain `rows` is at."""
    if rows.empty:
        return {
            "openPipe": [None] * weeks_in_quarter,
            "lsPipe":   [None] * weeks_in_quarter,
            "booked":   [None] * weeks_in_quarter,
            "totalCov": [None] * weeks_in_quarter,
            "target":   None,
        }
    # min_count=1 keeps NaN when ALL underlying values are NaN (e.g. future
    # weeks of in-flight quarters where the snapshot has no rows yet). Without
    # it, an all-NaN group sums to 0 and downstream coverage = 0/ltb = 0×.
    weekly = (
        rows.groupby("week_of_quarter", as_index=False)
        .agg(
            open_pipe=("open_pipe", lambda s: s.sum(min_count=1)),
            ls_pipe=("ls_pipe", lambda s: s.sum(min_count=1)),
            booked=("booked", "sum"),
            target_usd=("target_usd", "sum"),
        )
    )
    weekly["ltb"] = weekly["target_usd"] - weekly["booked"]
    weekly["total_cov"] = weekly["open_pipe"] / weekly["ltb"].where(weekly["ltb"] > 0)
    target = float(weekly["target_usd"].max()) if not weekly["target_usd"].isna().all() else None
    return {
        "openPipe": _series_to_list(weekly.set_index("week_of_quarter")["open_pipe"], weeks_in_quarter),
        "lsPipe":   _series_to_list(weekly.set_index("week_of_quarter")["ls_pipe"], weeks_in_quarter),
        "booked":   _series_to_list(weekly.set_index("week_of_quarter")["booked"], weeks_in_quarter),
        "totalCov": _series_to_list(weekly.set_index("week_of_quarter")["total_cov"], weeks_in_quarter),
        "target":   target,
    }


def _build_per_quarter_lookup(recs_per_quarter: pd.DataFrame) -> dict:
    """Build a nested dict for the JS to read per-(quarter, week) loose-
    conversion values without client-side recomputation. The recommendation
    engine now uses loose math (see `compute_recommendations`), so each entry
    here captures `open_pipe_usd`, `booked_at_week_usd`, `final_booked_usd`,
    and the loose conversion (`final − booked_at_week) / open_pipe`).

    Shape: {f"{geo}·{dt_code}": {anchor_week: [{q, open, bkd, conv}, ...]}}
    """
    if recs_per_quarter is None or recs_per_quarter.empty:
        return {}
    canon_to_code = {d["canonical"]: d["code"] for d in DEAL_TYPE_DISPLAY}
    out: dict = {}
    for _, r in recs_per_quarter.iterrows():
        dt_code = canon_to_code.get(r["deal_type"])
        if dt_code is None:
            continue
        key = f"{r['geo']}·{dt_code}"
        week = int(r["week_of_quarter"])
        out.setdefault(key, {}).setdefault(week, []).append({
            "q":    r["quarter"],
            "open": _safe(r["open_pipe_usd"]),
            "bkd":  _safe(r["booked_at_week_usd"]),
            "conv": _safe(r["conv"]),
        })
    return out


def build_segment_payload(
    coverage_segment: pd.DataFrame,
    segment_recs: pd.DataFrame | None = None,
    weeks_in_quarter: int = 13,
) -> dict:
    """Per-quarter payload for the Geo × Segment page.

    Same per-week/per-cell shape as the deal-type payload's quarters, cut by
    account tier. Each cell carries a `recCov` array (needed coverage at each
    week, from `compute_segment_recommendations`). Cells are keyed
    `f"{geo}·{segCode}"` and include the "All" pseudo-rows so the page can show
    geo subtotals, segment subtotals, and a grand total.
    """
    if coverage_segment is None or coverage_segment.empty:
        return {"segments": SEGMENT_DISPLAY, "quarters": {}, "presentQuarters": []}

    cov = coverage_segment.copy()
    present_quarters = [q for q in QUARTER_ORDER if q in cov["quarter"].unique()]

    # (geo, segment_canonical, week) -> rec_coverage
    rec_lookup: dict[tuple, float] = {}
    if segment_recs is not None and not segment_recs.empty:
        for _, r in segment_recs.iterrows():
            rec_lookup[(r["geo"], r["segment"], int(r["week_of_quarter"]))] = _safe(r["rec_coverage"])

    def rec_array(geo_code: str, seg_canonical: str) -> list:
        return [rec_lookup.get((geo_code, seg_canonical, w + 1)) for w in range(weeks_in_quarter)]

    quarters_payload: dict[str, dict] = {}
    for q in present_quarters:
        q_rows = cov[cov["quarter"] == q]
        agg = _aggregate_quarter(q_rows, weeks_in_quarter)

        is_future_quarter = _quarter_start(q) > pd.Timestamp.today().normalize()
        weeks_with_data = sum(1 for v in agg["openPipe"] if v is not None and v > 0)
        is_sparse = (not is_future_quarter) and 0 < weeks_with_data < SPARSE_WEEK_THRESHOLD
        if is_sparse:
            _sparsify_series(agg, weeks_in_quarter)
            cur_week = weeks_in_quarter
        else:
            cur_week = 0
            for i, v in enumerate(agg["openPipe"]):
                if v is not None and v > 0:
                    cur_week = i + 1

        cells: dict[str, dict] = {}
        for g in GEO_DISPLAY:
            for seg in SEGMENT_DISPLAY:
                all_geo = g["code"] == "All"
                all_seg = seg["canonical"] == "All"
                if all_geo and all_seg:
                    cell_rows = q_rows
                elif all_geo:
                    cell_rows = q_rows[q_rows["segment"] == seg["canonical"]]
                elif all_seg:
                    cell_rows = q_rows[q_rows["geo"] == g["code"]]
                else:
                    cell_rows = q_rows[
                        (q_rows["geo"] == g["code"]) & (q_rows["segment"] == seg["canonical"])
                    ]
                series = _aggregate_quarter(cell_rows, weeks_in_quarter)
                if is_sparse:
                    _sparsify_series(series, weeks_in_quarter)
                series["recCov"] = rec_array(g["code"], seg["canonical"])
                cells[f"{g['code']}·{seg['code']}"] = series

        quarters_payload[q] = {
            "currentWeek": cur_week,
            "aggregate": agg,
            "cells": cells,
            "isSparseSnapshot": is_sparse,
            "weeksWithData": weeks_with_data,
        }

    return {
        "segments": SEGMENT_DISPLAY,
        "quarters": quarters_payload,
        "presentQuarters": present_quarters,
    }


def build_product_payload(
    coverage_product: pd.DataFrame,
    product_recs: pd.DataFrame | None = None,
    weeks_in_quarter: int = 13,
) -> dict:
    """Per-quarter payload for the Product page (single dimension, no geo).

    Cells are keyed by product code (plus "All"); each carries the per-week
    series + a `recCov` array (needed coverage at each week).
    """
    if coverage_product is None or coverage_product.empty:
        return {"products": PRODUCT_DISPLAY, "quarters": {}, "presentQuarters": []}

    cov = coverage_product.copy()
    present_quarters = [q for q in QUARTER_ORDER if q in cov["quarter"].unique()]

    rec_lookup: dict[tuple, float] = {}
    if product_recs is not None and not product_recs.empty:
        for _, r in product_recs.iterrows():
            rec_lookup[(r["product"], int(r["week_of_quarter"]))] = _safe(r["rec_coverage"])

    def rec_array(prod_canonical: str) -> list:
        return [rec_lookup.get((prod_canonical, w + 1)) for w in range(weeks_in_quarter)]

    quarters_payload: dict[str, dict] = {}
    for q in present_quarters:
        q_rows = cov[cov["quarter"] == q]
        agg = _aggregate_quarter(q_rows, weeks_in_quarter)

        is_future_quarter = _quarter_start(q) > pd.Timestamp.today().normalize()
        weeks_with_data = sum(1 for v in agg["openPipe"] if v is not None and v > 0)
        is_sparse = (not is_future_quarter) and 0 < weeks_with_data < SPARSE_WEEK_THRESHOLD
        if is_sparse:
            _sparsify_series(agg, weeks_in_quarter)
            cur_week = weeks_in_quarter
        else:
            cur_week = 0
            for i, v in enumerate(agg["openPipe"]):
                if v is not None and v > 0:
                    cur_week = i + 1

        cells: dict[str, dict] = {}
        for p in PRODUCT_DISPLAY:
            if p["canonical"] == "All":
                # "All" = the sum across products (ties page 1), like the segment page
                series = _aggregate_quarter(q_rows, weeks_in_quarter)
            else:
                cell_rows = q_rows[q_rows["product"] == p["canonical"]]
                series = _aggregate_quarter(cell_rows, weeks_in_quarter)
            if is_sparse:
                _sparsify_series(series, weeks_in_quarter)
            series["recCov"] = rec_array(p["canonical"])
            cells[p["code"]] = series

        quarters_payload[q] = {
            "currentWeek": cur_week,
            "aggregate": agg,
            "cells": cells,
            "isSparseSnapshot": is_sparse,
            "weeksWithData": weeks_with_data,
        }

    return {
        "products": PRODUCT_DISPLAY,
        "quarters": quarters_payload,
        "presentQuarters": present_quarters,
    }


def _segment_payload(coverage_segment, segment_recs, weeks_in_quarter) -> dict:
    """Frozen snapshot wins over the live build while the tier source is missing.

    See FROZEN_SEGMENT_PATH — the live `build_segment_coverage` produces all
    "Unassigned" rows until a replacement for rpt_cx.account_segment_quarterly
    exists, so the captured payload is served verbatim when present.
    """
    if FROZEN_SEGMENT_PATH.exists():
        return json.loads(FROZEN_SEGMENT_PATH.read_text(encoding="utf-8"))
    if coverage_segment is not None:
        return build_segment_payload(coverage_segment, segment_recs, weeks_in_quarter)
    return {"segments": SEGMENT_DISPLAY, "quarters": {}, "presentQuarters": []}


def build_payload(
    coverage: pd.DataFrame,
    recs: pd.DataFrame,
    weeks_in_quarter: int = 13,
    recs_per_quarter: pd.DataFrame | None = None,
    coverage_segment: pd.DataFrame | None = None,
    segment_recs: pd.DataFrame | None = None,
    coverage_product: pd.DataFrame | None = None,
    product_recs: pd.DataFrame | None = None,
    as_of: str | None = None,
) -> dict:
    coverage = coverage.copy()

    present_quarters = [q for q in QUARTER_ORDER if q in coverage["quarter"].unique()]
    quarters_by_fy: dict[str, list[str]] = {}
    for q in present_quarters:
        fy = q.split(" ")[0]
        quarters_by_fy.setdefault(fy, []).append(q)
    fiscal_years = list(quarters_by_fy.keys())

    # Build a lookup: (geo, deal_type, week) -> rec_coverage
    rec_lookup: dict[tuple, float] = {}
    if not recs.empty:
        for _, r in recs.iterrows():
            key = (r["geo"], r["deal_type"], int(r["week_of_quarter"]))
            rec_lookup[key] = _safe(r["rec_coverage"])

    def rec_array(geo_code: str, dt_canonical: str) -> list:
        return [
            rec_lookup.get((geo_code, dt_canonical, w + 1))
            for w in range(weeks_in_quarter)
        ]

    quarters_payload: dict[str, dict] = {}
    for q in present_quarters:
        q_rows = coverage[coverage["quarter"] == q]
        agg = _aggregate_quarter(q_rows, weeks_in_quarter)

        # Sparseness check (e.g., FY24 Q1 only has snapshots at weeks 9 + 13).
        # Future quarters (start date after today) carry a single week-1
        # "current standing" point (open pipe / booked as of the latest
        # snapshot). They are NOT sparse-historical, so exclude them — otherwise
        # the flat-broadcast + cur_week=13 would land on a week where booked is
        # None and blank the coverage KPI.
        is_future_quarter = _quarter_start(q) > pd.Timestamp.today().normalize()
        weeks_with_data = sum(
            1 for v in agg["openPipe"] if v is not None and v > 0
        )
        is_sparse = (not is_future_quarter) and 0 < weeks_with_data < SPARSE_WEEK_THRESHOLD
        if is_sparse:
            _sparsify_series(agg, weeks_in_quarter)
            cur_week = weeks_in_quarter
        else:
            cur_week = 0
            for i, v in enumerate(agg["openPipe"]):
                if v is not None and v > 0:
                    cur_week = i + 1

        cells: dict[str, dict] = {}
        for g in GEO_DISPLAY:
            for dt in DEAL_TYPE_DISPLAY:
                all_geo = g["code"] == "All"
                all_dt  = dt["canonical"] == "All"
                if all_geo and all_dt:
                    cell_rows = q_rows                                            # everything
                elif all_geo:
                    cell_rows = q_rows[q_rows["deal_type"] == dt["canonical"]]    # all geos, one DT
                elif all_dt:
                    cell_rows = q_rows[q_rows["geo"] == g["code"]]                # one geo, all DTs
                else:
                    cell_rows = q_rows[
                        (q_rows["geo"] == g["code"])
                        & (q_rows["deal_type"] == dt["canonical"])
                    ]
                series = _aggregate_quarter(cell_rows, weeks_in_quarter)
                if is_sparse:
                    _sparsify_series(series, weeks_in_quarter)
                # Attach the recommendation array for this (geo, dt)
                series["recCov"] = rec_array(g["code"], dt["canonical"])
                cells[f"{g['code']}·{dt['code']}"] = series

        quarters_payload[q] = {
            "currentWeek": cur_week,
            "aggregate": agg,
            "cells": cells,
            "isSparseSnapshot": is_sparse,
            "weeksWithData": weeks_with_data,
        }

    default_quarter = present_quarters[-1] if present_quarters else None
    default_fy = default_quarter.split(" ")[0] if default_quarter else (fiscal_years[-1] if fiscal_years else None)

    return {
        # "As of" = the LATEST SNAPSHOT DATE in the data (the data's freshness),
        # NOT the render date — re-rendering the HTML days after a pull must not
        # advance the pill (Sam, 2026-06-06). Falls back to today only if the
        # build never supplied it (no meta.json, pre-fix artifacts).
        "asOfDate": as_of or date.today().isoformat(),
        "weeksInQuarter": weeks_in_quarter,
        "fiscalYears": fiscal_years,
        "quartersByFy": quarters_by_fy,
        "geos": GEO_DISPLAY,
        "dealTypes": DEAL_TYPE_DISPLAY,
        "quarters": quarters_payload,
        "defaultFy": default_fy,
        "defaultQuarter": default_quarter,
        "strictPerQuarter": _build_per_quarter_lookup(recs_per_quarter) if recs_per_quarter is not None else {},
        "segments": SEGMENT_DISPLAY,
        "segment": _segment_payload(coverage_segment, segment_recs, weeks_in_quarter),
        "products": PRODUCT_DISPLAY,
        "product": build_product_payload(coverage_product, product_recs, weeks_in_quarter) if coverage_product is not None else {"products": PRODUCT_DISPLAY, "quarters": {}, "presentQuarters": []},
    }


def render(
    coverage: pd.DataFrame,
    recs: pd.DataFrame | None = None,
    html_out: Path = DEFAULT_OUTPUT,
    recs_per_quarter: pd.DataFrame | None = None,
    coverage_segment: pd.DataFrame | None = None,
    segment_recs: pd.DataFrame | None = None,
    coverage_product: pd.DataFrame | None = None,
    product_recs: pd.DataFrame | None = None,
    as_of: str | None = None,
) -> Path:
    if recs is None:
        recs_path = REPO / "output" / "recommendations.parquet"
        recs = pd.read_parquet(recs_path) if recs_path.exists() else pd.DataFrame()
    if recs_per_quarter is None:
        rpq_path = REPO / "output" / "recommendations_per_quarter.parquet"
        recs_per_quarter = pd.read_parquet(rpq_path) if rpq_path.exists() else pd.DataFrame()
    if coverage_segment is None:
        seg_path = REPO / "output" / "coverage_segment.parquet"
        coverage_segment = pd.read_parquet(seg_path) if seg_path.exists() else pd.DataFrame()
    if segment_recs is None:
        seg_recs_path = REPO / "output" / "recommendations_segment.parquet"
        segment_recs = pd.read_parquet(seg_recs_path) if seg_recs_path.exists() else pd.DataFrame()
    if coverage_product is None:
        prod_path = REPO / "output" / "coverage_product.parquet"
        coverage_product = pd.read_parquet(prod_path) if prod_path.exists() else pd.DataFrame()
    if product_recs is None:
        prod_recs_path = REPO / "output" / "recommendations_product.parquet"
        product_recs = pd.read_parquet(prod_recs_path) if prod_recs_path.exists() else pd.DataFrame()
    if as_of is None:
        # Parquet-only re-renders (template tweaks) read the as-of date the
        # last full build recorded, so the pill stays on the DATA's date.
        meta_path = REPO / "output" / "meta.json"
        if meta_path.exists():
            # utf-8-sig: tolerate a BOM (e.g. a meta.json written by PowerShell)
            as_of = json.loads(meta_path.read_text(encoding="utf-8-sig")).get("asOfDate")
    payload = build_payload(coverage, recs, recs_per_quarter=recs_per_quarter, coverage_segment=coverage_segment, segment_recs=segment_recs, coverage_product=coverage_product, product_recs=product_recs, as_of=as_of)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload_json = json.dumps(payload, allow_nan=False, default=str).replace("</script>", "<\\/script>")
    html = template.replace("__DATA__", payload_json)
    html_out.parent.mkdir(parents=True, exist_ok=True)
    html_out.write_text(html, encoding="utf-8")
    return html_out


def main():
    parquet_path = REPO / "output" / "coverage.parquet"
    recs_path = REPO / "output" / "recommendations.parquet"
    rpq_path = REPO / "output" / "recommendations_per_quarter.parquet"
    seg_path = REPO / "output" / "coverage_segment.parquet"
    seg_recs_path = REPO / "output" / "recommendations_segment.parquet"
    prod_path = REPO / "output" / "coverage_product.parquet"
    prod_recs_path = REPO / "output" / "recommendations_product.parquet"
    coverage = pd.read_parquet(parquet_path)
    recs = pd.read_parquet(recs_path) if recs_path.exists() else pd.DataFrame()
    recs_pq = pd.read_parquet(rpq_path) if rpq_path.exists() else pd.DataFrame()
    cov_seg = pd.read_parquet(seg_path) if seg_path.exists() else pd.DataFrame()
    seg_recs = pd.read_parquet(seg_recs_path) if seg_recs_path.exists() else pd.DataFrame()
    cov_prod = pd.read_parquet(prod_path) if prod_path.exists() else pd.DataFrame()
    prod_recs = pd.read_parquet(prod_recs_path) if prod_recs_path.exists() else pd.DataFrame()
    out = render(coverage, recs=recs, recs_per_quarter=recs_pq, coverage_segment=cov_seg, segment_recs=seg_recs, coverage_product=cov_prod, product_recs=prod_recs)
    print(f"Dashboard rendered: {out}")


if __name__ == "__main__":
    main()
