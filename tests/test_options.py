"""ClaudeAgentOptions — the agent's capability surface and its constitution.

Moved out of test_boundary.py in v2 step 3. Config bugs here fail SILENTLY: the
agent sounds exactly as confident with no invariants loaded as with all of them,
so these assertions are the only thing standing between a one-line edit and an
agent that has quietly lost its rules.

Verified against claude_agent_sdk 0.2.134.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent import options
from pipeline import config

ROOT = Path(config.ROOT)


# --- 1. generic capability is the point of v2 ---------------------------------

def test_the_full_thinking_tool_set_is_available():
    """v2's premise: the agent writes itself a script, runs it, reads the result.

    Write and Edit were DISALLOWED in v1 — `test_write_tools_are_disallowed` is
    retired in this commit, deliberately, because its assertion is now exactly
    backwards. Without these the agent cannot think in scratch, which is the
    whole redesign.
    """
    allowed = set(options.build_options().allowed_tools)
    for t in ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Task", "TodoWrite"):
        assert t in allowed, f"{t} must be available for scratch-script thinking"


# --- 2. the network stays shut ------------------------------------------------

def test_the_agent_cannot_reach_the_internet():
    """The one capability v2 does NOT widen. Answers come from docs/ and the
    warehouse; a web result has no contract and no lineage."""
    disallowed = set(options.build_options().disallowed_tools)
    assert {"WebSearch", "WebFetch"} <= disallowed


def test_write_and_edit_are_no_longer_disallowed():
    """The v1 rule inverted. Asserted explicitly so a merge that restores the
    old list fails loudly rather than silently re-caging the agent."""
    disallowed = set(options.build_options().disallowed_tools)
    assert "Write" not in disallowed
    assert "Edit" not in disallowed


# --- 3. the gotcha that survives every redesign -------------------------------

def test_system_prompt_is_the_preset_not_none():
    """Regression test for the hello.py bug.

    With system_prompt=None the SDK does not inject CLAUDE.md, so the agent runs
    with none of its invariants while sounding just as confident. This test has
    survived two redesigns and must survive the next one.
    """
    o = options.build_options()
    assert isinstance(o.system_prompt, dict)
    assert o.system_prompt["preset"] == "claude_code"
    assert "invariant-10" in o.system_prompt["append"] or "opp-product-lines" in o.system_prompt["append"]


def test_setting_sources_is_project_only():
    """Also what loads .claude/settings.json, so the allow rules depend on it."""
    assert options.build_options().setting_sources == ["project"]


def test_permission_mode_is_not_bypass():
    """The prompt is a second access control, not friction to optimise away.
    v2 tunes WHERE the friction falls via settings.json; it does not remove it."""
    assert options.build_options().permission_mode != "bypassPermissions"


def test_doc_retrieval_subagent_is_read_only():
    o = options.build_options()
    assert set(o.agents["doc-retrieval"].tools) <= {"Read", "Glob", "Grep"}


# --- the operating rules carry the v2 sections --------------------------------

def test_operating_rules_keep_the_sections_that_did_not_change():
    """The spec replaces WAREHOUSE and adds four sections. Everything else is
    kept verbatim — losing CAVEATS would silently drop the invariant-10 caveat
    from every opp-count figure the agent reports."""
    rules = options.OPERATING_RULES
    for section in ("ASKING", "CAVEATS", "DELEGATION", "ROUTING"):
        assert f"\n{section}" in rules or rules.startswith(section), f"{section} missing"


def test_operating_rules_carry_the_new_v2_sections():
    rules = options.OPERATING_RULES
    for section in ("COMPUTE", "VERIFICATION", "MEMORY", "SELF-MODIFICATION"):
        assert section in rules, f"{section} missing"


def test_the_warehouse_rule_forbids_bypassing_the_query_tool():
    """General Bash makes a direct connection attempt EXPRESSIBLE for the first
    time. The mechanical control is that the connection string is absent from
    the environment; this is the rule that tells the agent not to try."""
    rules = options.OPERATING_RULES
    assert "query" in rules
    assert "not in your environment" in rules or "connection string" in rules


def test_the_compute_rule_points_at_scratch_and_lineage():
    rules = options.OPERATING_RULES
    assert "workspace/scratch" in rules
    assert "lineage" in rules.lower()


def test_the_verification_rule_admits_there_are_no_golden_numbers():
    """Established 2026-08-11. An agent that thinks golden outputs exist will
    invent agreement with them."""
    assert "golden" in options.OPERATING_RULES.lower()


# --- .claude/settings.json ----------------------------------------------------

SETTINGS = ROOT / ".claude" / "settings.json"


def test_settings_file_exists_and_parses():
    """setting_sources=["project"] loads it. A syntax error here means the allow
    rules silently do not apply and every scratch run starts prompting."""
    assert SETTINGS.exists(), "v2 needs .claude/settings.json for the allow rules"
    json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_thinking_is_free_and_acting_on_the_world_is_approved():
    """The v2 friction model. Scratch scripts, pipeline modules and the test
    suite run unprompted; everything else — repo edits, arbitrary Bash, the
    query tool — still asks."""
    allow = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    for rule in ("Bash(python workspace/scratch/*)",
                 "Bash(python -m pipeline.*)",
                 "Bash(python -m pytest*)",
                 "Write(workspace/**)",
                 "Edit(workspace/**)"):
        assert rule in allow, f"missing allow rule: {rule}"


def test_the_allow_list_does_not_pre_approve_the_query_tool():
    """Composed SQL is never auto-approved — the human sees the statement. If
    this ever passes into the allow list, the second access control is gone."""
    allow = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    joined = " ".join(allow)
    assert "query" not in joined
    assert "mcp__gtm" not in joined


def test_the_allow_list_does_not_pre_approve_edits_outside_workspace():
    """pipeline/ and agent/ are editable, but only with a prompt. Auto-approving
    them would let the agent rewrite its own pipeline unobserved."""
    allow = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["allow"]
    for rule in allow:
        if rule.startswith(("Write(", "Edit(")):
            assert "workspace/" in rule, f"{rule} auto-approves edits outside workspace/"
