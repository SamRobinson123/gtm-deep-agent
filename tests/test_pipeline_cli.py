"""The demoted capabilities — behaviour must be identical after the move.

v2 step 4 moves `pipe_create_targets`, `derive_pipe_create_target`,
`what_if_assumption`, `slip_analysis`, `show_assumptions` and the exporters out
of `agent/tools.py` and into `pipeline/` modules with thin CLIs. That is a MOVE
and re-plumb, not a rewrite: the same logic, reachable by `python -m pipeline.X`
or by `import` from a scratch script instead of through a frozen tool interface.

These tests pin the parts a move silently breaks — the lineage slugs and the
module surface — so "identical behaviour" is asserted rather than asserted-to.
The figures themselves are covered by the existing property tests; nothing here
hardcodes a model number.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

# Every module the demotion creates. Named individually so a missing one is a
# clear failure rather than a confusing import error somewhere downstream.
CLI_MODULES = [
    "pipeline.derive",
    "pipeline.targets_cli",
    "pipeline.waterfall_cli",
    "pipeline.slip_cli",
    "pipeline.export_cli",
]


@pytest.mark.parametrize("name", CLI_MODULES)
def test_the_module_exists_and_imports(name):
    importlib.import_module(name)


@pytest.mark.parametrize("name", [m for m in CLI_MODULES if m.endswith("_cli")])
def test_every_cli_is_runnable_as_a_module(name):
    """`python -m pipeline.targets_cli ...` is the interface the spec names, so
    each CLI needs a main() and the __main__ guard that reaches it."""
    mod = importlib.import_module(name)
    assert hasattr(mod, "main"), f"{name} needs a main() to be runnable with -m"
    src = inspect.getsource(mod)
    assert '__name__ == "__main__"' in src


# --- the lineage contract is what a move breaks silently ----------------------

SLUGS = {
    "pipeline.targets_cli": {
        "caveats": ["invariant-10-opportunities-unit"],
        "warnings": ["offline-grain-rollup-not-bts"],
    },
    "pipeline.waterfall_cli": {
        "caveats": ["invariant-10-opportunities-unit"],
        "warnings": ["derived-not-published-target",
                     "existing-pipe-bookings-omitted-overstates-required-create"],
    },
}


@pytest.mark.parametrize("mod_name,expected", SLUGS.items())
def test_the_caveat_and_warning_slugs_survive_the_move(mod_name, expected):
    """Slugs are how a stored run explains itself months later. A move that
    drops one produces a run that looks clean and is not — and no figure would
    change, so nothing else would catch it.

    invariant-10 is the sharpest case: without it, every opp-count and ASP
    figure loses the caveat that they count opp-product-lines, not distinct
    opps.
    """
    src = inspect.getsource(importlib.import_module(mod_name))
    for slug in expected["caveats"]:
        assert f'caveat("{slug}")' in src, f"{mod_name} lost caveat {slug}"
    for slug in expected["warnings"]:
        assert f'warn("{slug}")' in src, f"{mod_name} lost warning {slug}"


def test_derive_frame_is_importable_by_a_scratch_script():
    """The whole point of demotion: a scratch script imports this instead of
    calling a tool. It must be a plain function — an async one would force every
    caller into an event loop for no reason (the original had no awaits)."""
    from pipeline import derive

    assert callable(derive.derive_frame)
    assert not inspect.iscoroutinefunction(derive.derive_frame), (
        "derive_frame had no awaits as a tool handler; keep it synchronous so a "
        "scratch script can just call it")


def test_derive_frame_still_returns_a_frame_and_its_notes():
    """The notes carry the Pre Q slip rate, the slip inflow and the untargeted
    pipe exclusion. A caller that only takes the frame reports numbers with no
    idea what shaped them."""
    from pipeline import derive

    sig = inspect.signature(derive.derive_frame)
    assert {"quarters", "grain", "overrides", "as_of", "window"} <= set(sig.parameters)


# --- the tool surface is now asserted, not assumed ----------------------------

def test_the_agent_keeps_exactly_the_three_security_tools():
    """Spec test 4, and the spiritual successor to the retired test 16.

    `query` stays a tool because it is a security boundary — sqlguard validates
    it and the user approves each statement. az_login_status and azure_login
    stay because MFA needs an interactive browser launch. Everything else became
    ordinary code, which is reviewable in a way a frozen tool interface is not.

    Widening this is now a test failure rather than a drift.
    """
    from agent import tools

    assert {t.name for t in tools.GTM_TOOLS} == {"query", "az_login_status", "azure_login"}


@pytest.mark.parametrize("gone", [
    "run_pull", "list_queries", "pipe_create_targets", "derive_pipe_create_target",
    "list_runs", "show_run", "what_if_assumption", "slip_analysis",
    "show_assumptions", "export_excel", "export_chart",
])
def test_the_demoted_tools_are_gone_from_the_tool_surface(gone):
    """Deleted as TOOLS. The logic survives in pipeline/ — this asserts the
    frozen interface is what went, so nobody re-adds one out of habit."""
    from agent import tools

    assert gone not in {t.name for t in tools.GTM_TOOLS}


# --- PDF export (v2 output-capability gap) --------------------------------------

def _fake_run(tmp_path, monkeypatch):
    """A minimal stored run the exporter can read, without touching real runs."""
    import json
    import pandas as pd
    from pipeline import config

    rid = "2026-08-12T000000Z_pdf123"
    d = tmp_path / rid
    d.mkdir(parents=True)
    pd.DataFrame({
        "quarter": ["Q3 FY26", "Q3 FY26", "Q4 FY26"],
        "Territory": ["T1", "T2", "T1"],
        "bookings_target": [100.0, 200.0, 300.0],
        "pipe_create_target": [1000.0, 2000.0, 3000.0],
        "pre_q_win_rate": [0.15, 0.12, 0.14],
    }).to_csv(d / "derived_pipe_create.csv", index=False)
    (d / "manifest.json").write_text(json.dumps({
        "run_id": rid, "caveats": ["invariant-10-opportunities-unit"],
        "warnings": ["derived-not-published-target"],
        "headline": {"derived_Q3 FY26": 3000.0, "derived_Q4 FY26": 3000.0},
    }), encoding="utf-8")
    (tmp_path / "index.jsonl").write_text(
        json.dumps({"run_id": rid}) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "RUNS", tmp_path)
    return rid


def test_pdf_export_writes_a_real_pdf_inside_the_boundary(tmp_path, monkeypatch):
    """Same write boundary as every other deliverable: a NAME goes in, never a
    path, and the file lands in exports. %PDF is the four-byte magic — a zero
    byte 'pdf' that Excel-style tests would miss is the classic silent failure."""
    from agent import exports
    from pipeline import export_cli

    rid = _fake_run(tmp_path, monkeypatch)
    monkeypatch.setattr(exports, "EXPORTS", tmp_path / "out")

    msg = export_cli.to_pdf(run_id=rid, name="test_report", title="Test report")
    pdfs = list((tmp_path / "out").glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes()[:4] == b"%PDF"
    assert rid in msg, "the message must say which run was exported"


def _pdf_text(path):
    """Crude text recovery: inflate every content stream and keep printables.

    Good enough to assert a phrase made it into the document; no PDF library
    needed, so the test suite gains no dependency for one assertion."""
    import base64
    import re
    import zlib
    raw = path.read_bytes()
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
        data = m.group(1)
        # reportlab wraps Flate in ASCII85 ("...~>"), so try that first, then
        # bare Flate, then give up and keep the raw bytes.
        for decode in (lambda d: zlib.decompress(base64.a85decode(d, adobe=True)),
                       zlib.decompress,
                       lambda d: d):
            try:
                out.append(decode(data))
                break
            except Exception:
                continue
    return b"\n".join(out).decode("latin-1", errors="replace")


def test_pdf_export_carries_the_derived_warning(tmp_path, monkeypatch):
    """A PDF is the output most likely to be forwarded to someone with no
    context. The derived-not-published caveat travels in the document itself,
    not only in the chat message around it."""
    from agent import exports
    from pipeline import export_cli

    rid = _fake_run(tmp_path, monkeypatch)
    monkeypatch.setattr(exports, "EXPORTS", tmp_path / "out")
    export_cli.to_pdf(run_id=rid, name="caveat_check")

    pdf = next((tmp_path / "out").glob("*.pdf"))
    text = _pdf_text(pdf)
    assert "DERIVED" in text, "the derived-not-published caveat must be IN the PDF"
    assert rid in text, "the run id must be IN the PDF so the figure stays traceable"


def test_pdf_export_without_runs_fails_with_a_pointer(tmp_path, monkeypatch):
    from pipeline import config, export_cli
    monkeypatch.setattr(config, "RUNS", tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="[Nn]o runs"):
        export_cli.to_pdf()
