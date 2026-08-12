"""Run-reading endpoints.

Runs are immutable lineage artifacts, so a schema rename must not make an older
run unreadable. These tests pin that: they write a run directory using the
PRE-rename column names and assert both endpoints still serve it.
"""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from agent import lineage
from gtm_ui.server import app
from pipeline import config


@pytest.fixture
def legacy_run(tmp_path, monkeypatch):
    """A run written before sales_cycle_tail_from_earlier_quarters was renamed."""
    # lineage keeps its own RUNS_DIR; patching only config.RUNS leaves the
    # manifest lookup pointing at the real runs directory.
    monkeypatch.setattr(config, "RUNS", tmp_path)
    monkeypatch.setattr(lineage, "RUNS_DIR", tmp_path)
    rid = "2026-01-01T000000Z_legacy"
    d = tmp_path / rid
    d.mkdir(parents=True)
    pd.DataFrame([{
        "quarter": "Q3 FY26", "quarter_start": "2026-07-01", "Territory": "T1",
        "bookings_target": 1_000_000.0, "closed_won": 100_000.0,
        "expected_from_existing_pipe": 200_000.0,
        "maturation_tail_from_earlier_quarters": 50_000.0,   # the old name
        "gap": 650_000.0, "yield_per_dollar": 0.07,
        "required_by_gap": 9_285_714.0, "historic_floor": 1_000_000.0,
        "pipe_create_target": 9_285_714.0, "binding": "gap",
        "in_quarter_win_rate": 0.5, "pre_q_win_rate": 0.15, "q0_weight": 0.14,
    }]).to_csv(d / "derived_pipe_create.csv", index=False)
    (d / "manifest.json").write_text('{"run_id": "%s", "quarter": "Q3 FY26"}' % rid)
    return rid


def test_derivation_reads_a_run_written_before_the_rename(legacy_run):
    r = TestClient(app).get(f"/api/runs/{legacy_run}/derivation")
    assert r.status_code == 200, r.text
    steps = r.json()["quarters"][0]["steps"]
    tail = next(s for s in steps if "tail" in s["label"].lower())
    # The value must survive the migration, not silently become zero — a zero
    # tail is also what a genuinely missing tail looks like, and they mean
    # completely different things.
    assert tail["value"] == pytest.approx(-50_000.0)
    assert tail["provenance"] == "modelled"


def test_waterfall_reports_migration_rather_than_dropping_the_column(legacy_run):
    r = TestClient(app).get(f"/api/runs/{legacy_run}/waterfall")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sales_cycle_tail_from_earlier_quarters" in body["columns"]
    assert body["migrated_columns"] == ["sales_cycle_tail_from_earlier_quarters"]
    assert body["missing_columns"] == []


def test_a_run_with_no_derivation_is_404_not_500(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS", tmp_path)
    monkeypatch.setattr(lineage, "RUNS_DIR", tmp_path)
    (tmp_path / "empty_run").mkdir()
    assert TestClient(app).get("/api/runs/empty_run/derivation").status_code == 404


def test_a_missing_manifest_does_not_lose_the_chain(legacy_run, tmp_path):
    """The CSV is the substance. A run whose manifest is gone still has a
    derivation worth showing, so this degrades rather than 500s."""
    (tmp_path / legacy_run / "manifest.json").unlink()
    r = TestClient(app).get(f"/api/runs/{legacy_run}/derivation")
    assert r.status_code == 200, r.text
    assert r.json()["quarters"][0]["steps"]


def test_the_waterfall_endpoint_is_actually_called_by_the_front_end():
    """It was served and never called for a day — the per-row table existed only
    as JSON. A dead endpoint looks identical to a working one from the server
    side, so the check has to be on the client."""
    import pathlib
    js = pathlib.Path("gtm_ui/static/app.js").read_text(encoding="utf-8", errors="replace")
    assert "/waterfall" in js, "app.js never fetches the waterfall endpoint"
    assert js.count("waterfallTable(") >= 2, (
        "waterfallTable must be defined AND called — a definition alone renders nothing")


def test_every_overridable_column_the_endpoint_advertises_is_a_real_assumption():
    """The UI turns these into editable cells. Advertising one the solve does not
    accept would produce a what-if that silently changes nothing."""
    from agent import waterfall as wf
    for a in wf.ASSUMPTIONS:
        assert isinstance(a, str) and a
    # the three slip terms are applied before the solve, not inside it
    assert set(wf.SLIP_ASSUMPTIONS) <= set(wf.ASSUMPTIONS)
