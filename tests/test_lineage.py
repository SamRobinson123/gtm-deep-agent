"""Run lineage: immutability, hashing, honest recording of a dirty tree."""
from __future__ import annotations

import json

import pytest

from agent import lineage


def test_run_ids_are_distinct():
    assert lineage.new_run_id() != lineage.new_run_id()


def test_run_directory_is_never_overwritten(tmp_path):
    r = lineage.Run(runs_dir=tmp_path)
    with pytest.raises(FileExistsError):
        lineage.Run(run_id=r.run_id, runs_dir=tmp_path)


def test_manifest_records_input_hash_and_detects_a_one_byte_change(tmp_path):
    src = tmp_path / "input.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")

    with lineage.Run(runs_dir=tmp_path) as run:
        run.add_input(src)
    first = json.loads((run.dir / "manifest.json").read_text())["inputs"][0]["sha256"]

    src.write_text("a,b\n1,3\n", encoding="utf-8")
    with lineage.Run(runs_dir=tmp_path) as run2:
        run2.add_input(src)
    second = json.loads((run2.dir / "manifest.json").read_text())["inputs"][0]["sha256"]

    assert first and second and first != second


def test_manifest_records_git_state_and_derived_months(tmp_path):
    with lineage.Run(quarter_start="2026-07-01", runs_dir=tmp_path) as run:
        run.headline(pipe_target=201_789_918).caveat("invariant-10-opportunities-unit")
    m = json.loads((run.dir / "manifest.json").read_text())

    assert m["quarter"] == "Q3 FY26"
    assert m["month_columns"] == ["M202607", "M202608", "M202609"]
    assert "dirty" in m["git"]  # recorded, never a reason to refuse the run
    assert m["caveats"] == ["invariant-10-opportunities-unit"]
    assert m["headline"]["pipe_target"] == 201_789_918


def test_index_is_append_only_and_latest_is_a_regular_file(tmp_path):
    ids = []
    for _ in range(3):
        with lineage.Run(runs_dir=tmp_path) as run:
            ids.append(run.run_id)

    rows = lineage.list_runs(runs_dir=tmp_path)
    assert [r["run_id"] for r in rows] == ids

    latest = tmp_path / "latest.json"
    # A pointer file, not a symlink — symlinks on Windows need elevation.
    assert latest.is_file() and not latest.is_symlink()
    assert json.loads(latest.read_text())["run_id"] == ids[-1]


def test_earlier_run_survives_a_newer_iteration(tmp_path):
    """The whole point: a superseded number stays reviewable."""
    with lineage.Run(runs_dir=tmp_path) as first:
        first.headline(pipe_target=1)
    before = (first.dir / "manifest.json").read_bytes()

    with lineage.Run(runs_dir=tmp_path) as second:
        second.headline(pipe_target=2)

    assert (first.dir / "manifest.json").read_bytes() == before
    assert json.loads((second.dir / "manifest.json").read_text())["headline"]["pipe_target"] == 2
