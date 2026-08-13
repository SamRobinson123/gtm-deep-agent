"""Archive old output files to keep directories tidy.

Run via run_weekly_report.py at the end of each weekly run, or standalone via:
  python -m src.output_rotation
  python -m src.output_rotation --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

SNAPSHOT_RE = re.compile(
    r"^FY(?P<fy>\d{4})_Q(?P<q>\d+)_"
    r"(?P<run>\d{4}-\d{2}-\d{2})(_(?P<suffix>\d+))?\.xlsx$"
)
WEEKLY_RE = re.compile(
    r"^GTM_Weekly_WoW_FY(?P<fy>\d{4})_Q(?P<q>\d+)_"
    r"(?P<run>\d{4}-\d{2}-\d{2})\.xlsx$"
)
DASHBOARD_RE = re.compile(
    r"^GTM_Weekly_FY(?P<fy>\d{4})_Q(?P<q>\d+)_"
    r"(?P<run>\d{4}-\d{2}-\d{2})\.html$"
)

RETENTION: dict[str, int] = {
    "snapshots": 8,
    "weekly": 4,
    "dashboards": 4,
}

_SUBDIR_CONFIG: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("snapshots", SNAPSHOT_RE),
    ("dashboards", DASHBOARD_RE),
    ("weekly", WEEKLY_RE),
)


def _suffix_rank(suffix: str | None) -> int:
    return 0 if suffix is None else int(suffix)


def _parse_named_file(name: str, pattern: re.Pattern[str]) -> tuple[int, int, date, int] | None:
    m = pattern.match(name)
    if not m:
        return None
    run = date.fromisoformat(m.group("run"))
    suffix = m.groupdict().get("suffix")
    return int(m.group("fy")), int(m.group("q")), run, _suffix_rank(suffix)


def _group_files(
    directory: Path,
    pattern: re.Pattern[str],
) -> dict[tuple[int, int], dict[date, list[Path]]]:
    """(fy, quarter) -> run_date -> list of matching paths (newest suffix first per date)."""
    groups: dict[tuple[int, int], dict[date, list[Path]]] = defaultdict(lambda: defaultdict(list))
    if not directory.is_dir():
        return {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        parsed = _parse_named_file(path.name, pattern)
        if parsed is None:
            continue
        fy, q, run, _rank = parsed
        groups[(fy, q)][run].append(path)
    for run_map in groups.values():
        for run in run_map:
            run_map[run].sort(
                key=lambda p: _parse_named_file(p.name, pattern)[3]  # type: ignore[index]
            )
    return groups


def rotate(output_root: Path, *, dry_run: bool = False) -> dict[str, list[str]]:
    """Move files older than the retention window into _archive/ subdirs. Returns moved paths."""
    moved: dict[str, list[str]] = {name: [] for name in RETENTION}
    root = Path(output_root)

    for subdir, pattern in _SUBDIR_CONFIG:
        keep_n = RETENTION[subdir]
        directory = root / subdir
        groups = _group_files(directory, pattern)

        for (fy, q), by_date in groups.items():
            sorted_dates = sorted(by_date.keys(), reverse=True)
            keep_dates = set(sorted_dates[:keep_n])
            archive_dir = directory / "_archive" / f"FY{fy}_Q{q}"

            for run_date in sorted_dates:
                if run_date in keep_dates:
                    continue
                for path in by_date[run_date]:
                    dest = archive_dir / path.name
                    moved[subdir].append(str(path))
                    if dry_run:
                        continue
                    archive_dir.mkdir(parents=True, exist_ok=True)
                    if dest.exists():
                        dest.unlink()
                    shutil.move(str(path), str(dest))

    return moved


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Archive old GTM weekly output files.")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output root (default: <project>/output)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report files that would be archived without moving them",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    out_root = Path(args.output_dir).resolve() if args.output_dir else (root / "output")
    result = rotate(out_root, dry_run=args.dry_run)
    label = "Would archive" if args.dry_run else "Archived"
    for subdir, paths in result.items():
        if paths:
            print(f"{label} {len(paths)} file(s) from output/{subdir}/")
            for p in paths:
                print(f"  {p}")
        else:
            print(f"output/{subdir}/: nothing to archive (retention={RETENTION[subdir]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
