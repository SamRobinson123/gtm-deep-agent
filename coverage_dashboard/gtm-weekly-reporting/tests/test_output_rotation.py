"""Tests for output directory rotation."""

from __future__ import annotations

from pathlib import Path

from src.output_rotation import RETENTION, rotate


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_rotate_snapshots_dry_run_then_move(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snapshots"
    fy, q = 2026, 2
    dates = [f"2026-05-{d:02d}" for d in range(1, 12)]
    for d in dates:
        _touch(snap_dir / f"FY{fy}_Q{q}_{d}.xlsx")

    dry = rotate(tmp_path, dry_run=True)
    assert len(dry["snapshots"]) == 3
    assert all(snap_dir.joinpath(Path(p).name).is_file() for p in dry["snapshots"])

    result = rotate(tmp_path, dry_run=False)
    assert len(result["snapshots"]) == 3

    remaining = sorted(p.name for p in snap_dir.glob("FY*.xlsx"))
    assert len(remaining) == RETENTION["snapshots"]
    assert remaining == [
        f"FY{fy}_Q{q}_2026-05-04.xlsx",
        f"FY{fy}_Q{q}_2026-05-05.xlsx",
        f"FY{fy}_Q{q}_2026-05-06.xlsx",
        f"FY{fy}_Q{q}_2026-05-07.xlsx",
        f"FY{fy}_Q{q}_2026-05-08.xlsx",
        f"FY{fy}_Q{q}_2026-05-09.xlsx",
        f"FY{fy}_Q{q}_2026-05-10.xlsx",
        f"FY{fy}_Q{q}_2026-05-11.xlsx",
    ]

    archive = snap_dir / "_archive" / f"FY{fy}_Q{q}"
    archived = sorted(p.name for p in archive.glob("*.xlsx"))
    assert archived == [
        f"FY{fy}_Q{q}_2026-05-01.xlsx",
        f"FY{fy}_Q{q}_2026-05-02.xlsx",
        f"FY{fy}_Q{q}_2026-05-03.xlsx",
    ]


def test_rotate_dashboards_and_weekly_retention(tmp_path: Path) -> None:
    dash_dir = tmp_path / "dashboards"
    weekly_dir = tmp_path / "weekly"
    fy, q = 2026, 2
    for i, d in enumerate(["01", "03", "04", "06", "07", "11"], start=1):
        _touch(dash_dir / f"GTM_Weekly_FY{fy}_Q{q}_2026-05-{d}.html")
        _touch(weekly_dir / f"GTM_Weekly_WoW_FY{fy}_Q{q}_2026-05-{d}.xlsx")

    rotate(tmp_path, dry_run=False)

    dash_left = sorted(p.name for p in dash_dir.glob("GTM_Weekly_*.html"))
    assert len(dash_left) == RETENTION["dashboards"]
    assert dash_left[-1] == f"GTM_Weekly_FY{fy}_Q{q}_2026-05-11.html"

    weekly_left = sorted(p.name for p in weekly_dir.glob("GTM_Weekly_WoW_*.xlsx"))
    assert len(weekly_left) == RETENTION["weekly"]

    assert (dash_dir / "_archive" / f"FY{fy}_Q{q}" / f"GTM_Weekly_FY{fy}_Q{q}_2026-05-01.html").is_file()
    assert (weekly_dir / "_archive" / f"FY{fy}_Q{q}" / f"GTM_Weekly_WoW_FY{fy}_Q{q}_2026-05-01.xlsx").is_file()


def test_snapshot_suffix_same_run_date(tmp_path: Path) -> None:
    """Locked-file fallback (_1) moves with the canonical file for that run date."""
    snap_dir = tmp_path / "snapshots"
    fy, q = 2026, 2
    run = "2026-05-01"
    _touch(snap_dir / f"FY{fy}_Q{q}_{run}.xlsx")
    _touch(snap_dir / f"FY{fy}_Q{q}_{run}_1.xlsx")
    for d in [f"2026-05-{n:02d}" for n in range(2, 10)]:
        _touch(snap_dir / f"FY{fy}_Q{q}_{d}.xlsx")

    rotate(tmp_path, dry_run=False)
    archive = snap_dir / "_archive" / f"FY{fy}_Q{q}"
    assert not (snap_dir / f"FY{fy}_Q{q}_{run}.xlsx").is_file()
    assert not (snap_dir / f"FY{fy}_Q{q}_{run}_1.xlsx").is_file()
    assert (archive / f"FY{fy}_Q{q}_{run}.xlsx").is_file()
    assert (archive / f"FY{fy}_Q{q}_{run}_1.xlsx").is_file()
    assert len(list(snap_dir.glob("FY*.xlsx"))) == RETENTION["snapshots"]
