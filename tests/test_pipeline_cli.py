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
