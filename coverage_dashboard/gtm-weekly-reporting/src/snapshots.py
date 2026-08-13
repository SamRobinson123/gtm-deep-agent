"""Save and load snapshots for week-over-week comparison (Excel workbook or legacy CSV)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

_SNAPSHOT_NAME_RE = re.compile(
    r"^FY(?P<fy>\d+)_Q(?P<quarter>\d+)_(?P<run_date>\d{4}-\d{2}-\d{2})_(?P<table>quarterly|product|deal_type)\.csv$"
)
# Optional _N suffix matches save_snapshot fallbacks when the canonical file is locked.
_SNAPSHOT_XLSX_RE = re.compile(
    r"^FY(?P<fy>\d+)_Q(?P<quarter>\d+)_(?P<run_date>\d{4}-\d{2}-\d{2})(_(?P<suffix>\d+))?\.xlsx$"
)


def _xlsx_workbook_suffix_rank(filename: str) -> int:
    """0 = canonical FY…_Q…_YYYY-MM-DD.xlsx; larger integers = _1, _2, … from locked-file saves."""
    m = _SNAPSHOT_XLSX_RE.match(filename)
    if not m:
        return 10**9
    s = m.group("suffix")
    return 0 if s is None else int(s)

# Sheet names inside the workbook (readable tab labels)
_SHEETS: tuple[tuple[str, str], ...] = (
    ("quarterly", "Quarterly"),
    ("product", "Product"),
    ("deal_type", "Deal Type"),
)
_BASE_SHEET_MAP: dict[str, str] = dict(_SHEETS)
_QUARTER_KEY_RE = re.compile(r"^(quarterly|product|deal_type)_q([1-4])$")

# Match run_weekly_report coverage WoW headers (strip if a WoW export was mistaken for a snapshot)
_WOW_COVERAGE_CHANGE_COLS = frozenset({"Total pipe coverage change", "LS pipe coverage change"})


def _strip_delta_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove WoW columns if present so merges against raw snapshots stay valid."""
    delta_cols = [
        c
        for c in df.columns
        if str(c).startswith("Δ") or c in _WOW_COVERAGE_CHANGE_COLS
    ]
    if not delta_cols:
        return df
    return df.drop(columns=delta_cols)


def _snapshot_stem(fy: int, quarter: int, run_date: pd.Timestamp) -> str:
    d = pd.Timestamp(run_date).normalize().strftime("%Y-%m-%d")
    return f"FY{fy}_Q{quarter}_{d}"


def _sheet_title_for_key(key: str) -> Optional[str]:
    title = _BASE_SHEET_MAP.get(key)
    if title is not None:
        return title
    m = _QUARTER_KEY_RE.match(key)
    if m is None:
        return None
    base_key, q = m.group(1), m.group(2)
    return f"Q{q} {_BASE_SHEET_MAP[base_key]}"


def _key_for_sheet_title(sheet_title: str) -> Optional[str]:
    for key, base_title in _SHEETS:
        if sheet_title == base_title:
            return key
        for q in (1, 2, 3, 4):
            if sheet_title == f"Q{q} {base_title}":
                return f"{key}_q{q}"
    return None


def save_snapshot(
    tables: dict[str, pd.DataFrame],
    run_date: pd.Timestamp,
    fy: int,
    quarter: int,
    output_dir: Path,
) -> list[Path]:
    """
    Write quarterly / product / deal_type DataFrames as one .xlsx under output_dir/snapshots/
    (one worksheet per table). Adds run_date, fy, quarter columns to each copy before writing.
    Returns list containing the workbook path.
    """
    snap_dir = Path(output_dir) / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    stem = _snapshot_stem(fy, quarter, run_date)
    meta = {
        "run_date": pd.Timestamp(run_date).normalize(),
        "fy": fy,
        "quarter": quarter,
    }
    def _write_workbook(path: Path) -> None:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for key, df_src in tables.items():
                sheet_title = _sheet_title_for_key(key)
                if sheet_title is None:
                    continue
                df = df_src.copy()
                for c, v in meta.items():
                    df[c] = v
                df.to_excel(writer, sheet_name=sheet_title, index=False)

    path = snap_dir / f"{stem}.xlsx"
    wrote_canonical = False
    try:
        _write_workbook(path)
        wrote_canonical = True
    except PermissionError:
        # Common on Windows when the workbook is open in Excel. Save to a suffixed
        # filename for this run instead of failing the whole pipeline.
        fallback_path = None
        for i in range(1, 100):
            candidate = snap_dir / f"{stem}_{i}.xlsx"
            try:
                _write_workbook(candidate)
                fallback_path = candidate
                break
            except PermissionError:
                continue
        if fallback_path is None:
            raise
        path = fallback_path
    if wrote_canonical:
        for stale in snap_dir.glob(f"{stem}_*.xlsx"):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
    # Cleanup legacy same-run CSV snapshots so each run leaves one workbook artifact.
    for key, _ in _SHEETS:
        legacy_csv = snap_dir / f"{stem}_{key}.csv"
        if legacy_csv.exists():
            legacy_csv.unlink()
    return [path]


def _read_snapshot_xlsx(path: Path) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    with pd.ExcelFile(path, engine="openpyxl") as xl:
        for sheet in xl.sheet_names:
            key = _key_for_sheet_title(sheet)
            if key is not None:
                out[key] = _strip_delta_columns(pd.read_excel(xl, sheet_name=sheet))
    return out


def _load_tables_from_workbook_paths(
    paths: list[Path],
    quarter: int,
) -> Optional[tuple[dict[str, pd.DataFrame], Path]]:
    """
    Try workbook paths in order (canonical first, then _1, _2, …).
    Returns (tables, path_used) or None if none readable / complete.
    """
    need = ("quarterly", "product", "deal_type")
    for path in paths:
        try:
            loaded = _read_snapshot_xlsx(path)
        except PermissionError:
            continue
        for k in need:
            if k not in loaded:
                fallback_key = f"{k}_q{quarter}"
                if fallback_key in loaded:
                    loaded[k] = loaded[fallback_key]
        if not all(k in loaded for k in need):
            continue
        return ({k: loaded[k] for k in need}, path)
    return None


def _load_tables_from_run(
    files: dict[str, Any],
    quarter: int,
) -> Optional[tuple[dict[str, pd.DataFrame], Optional[Path]]]:
    """
    Load quarterly / product / deal_type for one snapshot run.
    Returns (tables, source_path) with source_path the workbook or first CSV used; None if unusable.
    """
    xlsx_paths = files.get("_xlsx_paths") or []
    if xlsx_paths:
        sorted_paths = sorted(xlsx_paths, key=lambda p: _xlsx_workbook_suffix_rank(p.name))
        got = _load_tables_from_workbook_paths(sorted_paths, quarter)
        if got is None:
            return None
        tables, src = got
        return (tables, src)

    out: dict[str, pd.DataFrame] = {}
    src: Optional[Path] = None
    for k in ("quarterly", "product", "deal_type"):
        try:
            out[k] = _strip_delta_columns(pd.read_csv(files[k]))
        except PermissionError:
            return None
        if src is None:
            src = files[k]
    return (out, src)


def load_latest_snapshot(
    fy: int,
    quarter: int,
    output_dir: Path,
    before_date: Optional[pd.Timestamp] = None,
) -> Optional[dict[str, Any]]:
    """
    Load the most recent snapshot for FY/quarter, optionally with run_date strictly before before_date
    (normalized to midnight). Prefers a single .xlsx workbook; falls back to legacy three CSVs for the same run.
    If the newest snapshot file is locked (e.g. open in Excel on Windows), tries the next-older complete run.
    Returns dict with quarterly, product, deal_type DataFrames, run_date, and optional prior_snapshot_path
    (workbook or CSV path that was read), or None if incomplete / missing.
    """
    snap_dir = Path(output_dir) / "snapshots"
    if not snap_dir.is_dir():
        return None

    cutoff = None
    if before_date is not None:
        cutoff = pd.Timestamp(before_date).normalize()

    # run_date -> {"_xlsx_paths": [Path, ...]} or legacy CSV paths per table
    runs: dict[pd.Timestamp, dict[str, Any]] = {}
    for p in snap_dir.iterdir():
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.endswith(".xlsx"):
            m = _SNAPSHOT_XLSX_RE.match(p.name)
            if not m:
                continue
            fyy = int(m.group("fy"))
            q = int(m.group("quarter"))
            if fyy != fy or q != quarter:
                continue
            rd = pd.Timestamp(m.group("run_date")).normalize()
            if cutoff is not None and not rd < cutoff:
                continue
            bucket = runs.setdefault(rd, {})
            bucket.setdefault("_xlsx_paths", []).append(p)
            continue
        if low.endswith(".csv"):
            m = _SNAPSHOT_NAME_RE.match(p.name)
            if not m:
                continue
            fyy = int(m.group("fy"))
            q = int(m.group("quarter"))
            if fyy != fy or q != quarter:
                continue
            rd = pd.Timestamp(m.group("run_date")).normalize()
            if cutoff is not None and not rd < cutoff:
                continue
            tbl = m.group("table")
            runs.setdefault(rd, {})[tbl] = p

    if not runs:
        return None

    for rd, f in runs.items():
        paths = f.get("_xlsx_paths")
        if paths:
            uniq = sorted({p.resolve(): p for p in paths}.values(), key=lambda x: _xlsx_workbook_suffix_rank(x.name))
            f["_xlsx_paths"] = uniq

    def run_complete(files: dict[str, Any]) -> bool:
        if files.get("_xlsx_paths"):
            return True
        return all(k in files for k in ("quarterly", "product", "deal_type"))

    complete_dates = [d for d, f in runs.items() if run_complete(f)]
    if not complete_dates:
        return None

    # Newest first; skip runs we cannot read (e.g. workbook open in Excel on Windows).
    for run_date in sorted(complete_dates, reverse=True):
        loaded = _load_tables_from_run(runs[run_date], quarter)
        if loaded is None:
            continue
        tables, source_path = loaded
        out: dict[str, Any] = {
            "run_date": run_date,
            **tables,
            "prior_snapshot_path": source_path,
        }
        return out
    return None


def list_snapshots(output_dir: Path) -> pd.DataFrame:
    """Index snapshot workbooks and legacy CSVs: run_date, fy, quarter, table, path."""
    snap_dir = Path(output_dir) / "snapshots"
    rows: list[dict[str, Any]] = []
    if not snap_dir.is_dir():
        return pd.DataFrame(columns=["run_date", "fy", "quarter", "table", "path"])

    seen_workbooks: set[str] = set()
    for p in sorted(snap_dir.iterdir()):
        if not p.is_file():
            continue
        low = p.name.lower()
        if low.endswith(".xlsx"):
            m = _SNAPSHOT_XLSX_RE.match(p.name)
            if not m:
                continue
            rd = pd.Timestamp(m.group("run_date")).normalize()
            fy = int(m.group("fy"))
            q = int(m.group("quarter"))
            key = str(p.resolve())
            if key in seen_workbooks:
                continue
            seen_workbooks.add(key)
            for tbl, _ in _SHEETS:
                rows.append(
                    {
                        "run_date": rd,
                        "fy": fy,
                        "quarter": q,
                        "table": tbl,
                        "path": str(p.resolve()),
                    }
                )
            continue
        if low.endswith(".csv"):
            m = _SNAPSHOT_NAME_RE.match(p.name)
            if not m:
                continue
            rows.append(
                {
                    "run_date": pd.Timestamp(m.group("run_date")).normalize(),
                    "fy": int(m.group("fy")),
                    "quarter": int(m.group("quarter")),
                    "table": m.group("table"),
                    "path": str(p.resolve()),
                }
            )

    if not rows:
        return pd.DataFrame(columns=["run_date", "fy", "quarter", "table", "path"])
    return pd.DataFrame(rows).sort_values(["run_date", "fy", "quarter", "table"]).reset_index(drop=True)
