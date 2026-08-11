"""The warehouse boundary and the config surface. No tokens, no network."""
from __future__ import annotations

import inspect

import pytest

from agent import hooks, options, sqlguard, tools
from pipeline import queries


# --- the security model -----------------------------------------------------

def test_registry_is_exactly_four():
    assert set(queries.QUERY_NAMES) == {"sku_nacv", "snapshot", "opp_ages", "bts"}


def test_unknown_query_is_rejected():
    with pytest.raises(KeyError):
        queries.get("anything_else")


def test_no_tool_accepts_free_text_sql():
    """The narrow interface IS the security model.

    This fails the day someone adds an ad-hoc query tool for convenience —
    including a future me. Do not relax it; add a named query instead.
    """
    for t in tools.GTM_TOOLS:
        schema = getattr(t, "input_schema", None) or {}
        names = schema if isinstance(schema, dict) else {}
        for param in names:
            assert "sql" not in str(param).lower(), (
                f"tool {t.name!r} exposes a SQL-shaped parameter {param!r}. "
                "No tool may accept SQL text."
            )
        src = inspect.getsource(t.handler) if hasattr(t, "handler") else ""
        assert "read_sql(args" not in src


@pytest.mark.parametrize("name", list(queries.REGISTRY))
def test_every_template_is_read_only(name):
    sql, _, _ = queries.REGISTRY[name]
    assert sqlguard.assert_read_only(sql, name) is sql


@pytest.mark.parametrize("bad", [
    "SELECT 1; DROP TABLE x",              # stacked statements
    "SELECT a INTO newtbl FROM t",         # reads like a select, creates a table
    "/* comment */ INSERT INTO t VALUES(1)",
    "WITH a AS (SELECT 1) DELETE FROM t",  # write hidden behind a CTE
    "UPDATE t SET a = 1",
    "EXEC sp_who",
    "SELECT * FROM OPENROWSET('x','y','z')",
    "",
])
def test_unsafe_sql_rejected(bad):
    with pytest.raises(sqlguard.UnsafeSQL):
        sqlguard.assert_read_only(bad)


def test_string_literals_do_not_trip_keywords():
    """A legitimate value must not be mistaken for a keyword."""
    sql = "SELECT * FROM t WHERE Raw_Stage NOT IN ('Closed - Duplicate', 'Created')"
    assert sqlguard.assert_read_only(sql)


# --- hooks ------------------------------------------------------------------

def test_read_scope_allows_docs_and_data():
    assert hooks.check_read_scope("docs/models/pipe-create.md") is None
    assert hooks.check_read_scope("data/Target_Monthly.csv") is None


def test_read_scope_denies_specs_and_env_and_outside():
    assert hooks.check_read_scope("docs/superpowers/specs/x.md") is not None
    assert hooks.check_read_scope(".env") is not None
    assert hooks.check_read_scope("C:/Windows/System32/drivers/etc/hosts") is not None


def test_read_scope_denies_traversal():
    """Resolve before comparing, or docs/../../ walks straight out."""
    assert hooks.check_read_scope("docs/../../../etc/passwd") is not None


def test_bash_allowlist():
    assert hooks.check_bash("az account show") is None
    assert hooks.check_bash("az login") is None
    for bad in ["cat .env", "rm -rf /", "python -c 'import os'", "az account show && cat .env"]:
        assert hooks.check_bash(bad) is not None, bad


# --- options ----------------------------------------------------------------

def test_system_prompt_is_the_preset_not_none():
    """Regression test for the hello.py bug.

    With system_prompt=None the SDK does not inject CLAUDE.md, so the agent runs
    with none of its invariants while sounding just as confident.
    """
    o = options.build_options()
    assert isinstance(o.system_prompt, dict)
    assert o.system_prompt["preset"] == "claude_code"
    assert "invariant-10" in o.system_prompt["append"] or "opp-product-lines" in o.system_prompt["append"]


def test_write_tools_are_disallowed():
    o = options.build_options()
    for t in ("Write", "Edit", "WebSearch", "WebFetch"):
        assert t in o.disallowed_tools


def test_setting_sources_is_project_only():
    o = options.build_options()
    assert o.setting_sources == ["project"]


def test_permission_mode_is_not_bypass():
    """The prompt is a second access control, not friction to optimise away."""
    assert options.build_options().permission_mode != "bypassPermissions"


def test_doc_retrieval_subagent_is_read_only():
    o = options.build_options()
    sub = o.agents["doc-retrieval"]
    assert set(sub.tools) <= {"Read", "Glob", "Grep"}
