"""Tests for snapshot discovery (including suffixed workbooks) and load order."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.snapshots import load_latest_snapshot, list_snapshots  # noqa: E402


def _write_min_workbook(path: Path, quarterly_marker: str) -> None:
    q = pd.DataFrame({"Region": [quarterly_marker], "Team": [""]})
    p = pd.DataFrame({"Product": ["p"], "Geo": ["AMS"]})
    d = pd.DataFrame({"Geo": ["AMS"], "Type": ["t"]})
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        q.to_excel(writer, sheet_name="Quarterly", index=False)
        p.to_excel(writer, sheet_name="Product", index=False)
        d.to_excel(writer, sheet_name="Deal Type", index=False)


class TestSnapshotXlsxSuffix(unittest.TestCase):
    def test_load_latest_finds_suffixed_workbook_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            snap = out / "snapshots"
            snap.mkdir(parents=True)
            path = snap / "FY2026_Q2_2026-05-01_1.xlsx"
            _write_min_workbook(path, "from_suffix")
            pack = load_latest_snapshot(2026, 2, out, before_date=pd.Timestamp("2026-05-03"))
            self.assertIsNotNone(pack)
            assert pack is not None
            self.assertEqual(pd.Timestamp(pack["run_date"]).normalize(), pd.Timestamp("2026-05-01").normalize())
            self.assertEqual(pack["quarterly"].iloc[0]["Region"], "from_suffix")
            self.assertEqual(Path(pack["prior_snapshot_path"]).resolve(), path.resolve())

    def test_canonical_workbook_tried_before_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            snap = out / "snapshots"
            snap.mkdir(parents=True)
            canonical = snap / "FY2026_Q2_2026-05-01.xlsx"
            suffixed = snap / "FY2026_Q2_2026-05-01_1.xlsx"
            _write_min_workbook(canonical, "canonical")
            _write_min_workbook(suffixed, "suffix")
            pack = load_latest_snapshot(2026, 2, out, before_date=pd.Timestamp("2026-05-03"))
            self.assertIsNotNone(pack)
            assert pack is not None
            self.assertEqual(pack["quarterly"].iloc[0]["Region"], "canonical")
            self.assertEqual(Path(pack["prior_snapshot_path"]).resolve(), canonical.resolve())

    def test_list_snapshots_includes_suffixed_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            snap = out / "snapshots"
            snap.mkdir(parents=True)
            path = snap / "FY2026_Q2_2026-05-01_2.xlsx"
            _write_min_workbook(path, "x")
            df = list_snapshots(out)
            self.assertGreater(len(df), 0)
            paths = df["path"].tolist()
            self.assertIn(str(path.resolve()), paths)


if __name__ == "__main__":
    unittest.main()
