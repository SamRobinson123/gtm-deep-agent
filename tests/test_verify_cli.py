"""The verifier execution relay — closing the gap the CLI left.

SDK 0.2.134 does not grant Bash to subagents (proved across three acceptance
runs; permissionMode tried and reverted). So the loop is split at the only
seam that preserves independence: the VERIFIER derives the recompute logic in
its own context window, without ever seeing the maker's reasoning, and writes
it to a fixed path; the MAIN agent — which has Bash — executes it through this
CLI and reports the delta. Independence lives in who derives the logic, not in
who types `python`.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from pipeline import verify_cli


def _run_with_claim(tmp_path, rid="2026-08-12T000000Z_ver001", claim=1000.0):
    d = tmp_path / "runs" / rid
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": rid,
        "headline": {"quarter_pipe_target": claim},
    }), encoding="utf-8")
    return rid


def _script(tmp_path, rid, body):
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    p = scratch / f"verify_{rid}.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_agreement_within_tolerance(tmp_path, monkeypatch):
    """The happy path: the verifier's script prints RECOMPUTED lines, the relay
    matches them to the manifest's headline and computes the delta."""
    rid = _run_with_claim(tmp_path, claim=1000.0)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")
    _script(tmp_path, rid, """
        print("RECOMPUTED quarter_pipe_target=1000.25")
    """)

    report = verify_cli.execute(rid)
    assert "AGREE" in report
    assert "0.25" in report, "the delta is shown even on agreement"


def test_disagreement_carries_the_delta(tmp_path, monkeypatch):
    """DISAGREE with no number is unactionable — the size says whether it is
    float dust or a broken assumption."""
    rid = _run_with_claim(tmp_path, claim=1000.0)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")
    _script(tmp_path, rid, """
        print("RECOMPUTED quarter_pipe_target=1500.0")
    """)

    report = verify_cli.execute(rid)
    assert "DISAGREE" in report
    assert "500" in report and "%" in report


def test_a_missing_script_says_the_verifier_has_not_run(tmp_path, monkeypatch):
    """The relay must not invent a verdict when there is nothing to execute."""
    rid = _run_with_claim(tmp_path)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")

    report = verify_cli.execute(rid)
    assert "CANNOT VERIFY" in report
    assert "verifier has not" in report.lower()
    assert f"verify_{rid}.py" in report, "say exactly which file is missing"


def test_a_crashing_script_is_reported_not_swallowed(tmp_path, monkeypatch):
    rid = _run_with_claim(tmp_path)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")
    _script(tmp_path, rid, """
        raise RuntimeError("the CSV moved")
    """)

    report = verify_cli.execute(rid)
    assert "CANNOT VERIFY" in report
    assert "the CSV moved" in report, "the script's own error is the diagnosis"


def test_a_script_that_prints_no_recomputed_line_is_not_agreement(tmp_path, monkeypatch):
    """Exit code 0 with no figures proves nothing. Silence is not success —
    treating it as agreement would let an empty script 'verify' anything."""
    rid = _run_with_claim(tmp_path)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")
    _script(tmp_path, rid, """
        print("looked at some things")
    """)

    report = verify_cli.execute(rid)
    assert "CANNOT VERIFY" in report
    assert "RECOMPUTED" in report, "the report explains the expected output shape"


def test_the_script_runs_without_the_connection_string(tmp_path, monkeypatch):
    """The relay must not weaken step 1: the child process inherits the parent
    environment, and SYNAPSE_CONN_STR is not in it."""
    rid = _run_with_claim(tmp_path)
    monkeypatch.setattr(verify_cli.config, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(verify_cli, "SCRATCH", tmp_path / "scratch")
    _script(tmp_path, rid, """
        import os
        assert "SYNAPSE_CONN_STR" not in os.environ, "leak!"
        print("RECOMPUTED quarter_pipe_target=1000.0")
    """)

    assert "AGREE" in verify_cli.execute(rid)
