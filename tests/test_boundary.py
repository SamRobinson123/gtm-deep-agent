"""The warehouse boundary and the config surface. No tokens, no network."""
from __future__ import annotations

import inspect

import pytest

from agent import hooks, options, sqlguard, tools
from pipeline import queries


# --- the security model -----------------------------------------------------

def test_registry_is_exactly_the_approved_set():
    """The registry is the security boundary — this fails whenever it changes.

    `snapshot_hist` was added 2026-08-11 with the user's explicit approval, to
    measure slip against the prior year. It reads the SAME table, columns and
    filters as `snapshot`; only the date window differs. Widening `snapshot`
    itself was rejected because invariant 5's actuals anchoring depends on that
    window starting at the pre-quarter buffer.
    """
    assert set(queries.QUERY_NAMES) == {
        "sku_nacv", "snapshot", "snapshot_hist", "opp_ages", "bts"}


def test_historic_snapshot_query_differs_from_the_live_one_only_by_window():
    """Guards the justification above: if snapshot_hist ever grows a different
    table, column set or filter, this is no longer a windowing variant."""
    import re
    norm = lambda s: re.sub(r"'[\d-]{10}'", "'DATE'", " ".join(s.split()))
    assert norm(queries.SNAP_HIST_SQL) == norm(queries.SNAP_SQL)


def test_unknown_query_is_rejected():
    with pytest.raises(KeyError):
        queries.get("anything_else")


def test_exactly_one_tool_accepts_sql_and_it_is_guarded():
    """The agent composes SQL by design (2026-08-10 decision), so the control moved.

    It is no longer "no tool takes SQL" — it is "exactly one does, it validates
    before executing, and it is never auto-approved". This fails if a second
    SQL-accepting tool appears, or if the one that exists stops validating.
    """
    sql_tools = [
        t for t in tools.GTM_TOOLS
        if any("sql" in str(p).lower() for p in (getattr(t, "input_schema", None) or {}))
    ]
    assert [t.name for t in sql_tools] == ["query"], (
        f"expected exactly one SQL-accepting tool, found {[t.name for t in sql_tools]}"
    )

    src = inspect.getsource(sql_tools[0].handler)
    assert "assert_read_only" in src, "the query tool must validate before executing"
    assert src.index("assert_read_only") < src.index("read_sql"), (
        "validation must happen BEFORE execution"
    )


def test_sql_tool_is_never_auto_approved():
    """Every composed query is shown to the user and approved before it runs."""
    from gtm_ui.session import AUTO_ALLOW
    for name in ("mcp__gtm__query", "mcp__gtm__run_pull", "mcp__gtm__azure_login"):
        assert name not in AUTO_ALLOW, f"{name} must require explicit approval"


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
    sql = ("SELECT * FROM [sfdc_trf].[opportunity_live] "
           "WHERE StageName NOT IN ('Closed - Duplicate', 'Created')")
    assert sqlguard.assert_read_only(sql)


# --- the table allowlist ----------------------------------------------------

def test_composed_analytical_query_is_allowed():
    """Real work must pass: CTEs, joins, aggregates over a chosen window."""
    sql = """
    WITH cohort AS (
        SELECT o.Id, o.CreatedDate, o.CloseDate, o.StageName, o.Bookings_Team_Static
        FROM [sfdc_trf].[opportunity_live] o
        WHERE o.IsDeleted = 0 AND o.CreatedDate >= '2026-01-01'
    )
    SELECT c.Bookings_Team_Static, COUNT(*) AS n
    FROM cohort c
    JOIN [sharepoint].[Map_Booking_Team_Static_live] b
      ON b.Bookings_Team_Static = c.Bookings_Team_Static
    GROUP BY c.Bookings_Team_Static
    """
    assert sqlguard.assert_read_only(sql)


def test_cte_name_is_not_mistaken_for_a_table():
    assert "cohort" in sqlguard.cte_names("WITH cohort AS (SELECT 1) SELECT * FROM cohort")


def test_undocumented_table_is_refused():
    """Querying without a documented contract is refused, even read-only."""
    with pytest.raises(sqlguard.UnsafeSQL, match="not documented"):
        sqlguard.assert_read_only("SELECT * FROM [dbo].[secret_payroll]")


@pytest.mark.parametrize("t", ["sfdc_trf.opportunity_live", "rep.trf_opp_daily_snapshot_new"])
def test_allowlist_covers_the_tables_the_derivation_needs(t):
    assert t in sqlguard.ALLOWED_TABLES


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


# --- Azure CLI resolution ---------------------------------------------------

def test_az_is_resolved_via_which_not_bare_name():
    """Regression: on Windows `az` is az.CMD, and subprocess without shell=True
    cannot launch a .cmd by bare name. It raises FileNotFoundError, which reads
    exactly like "not installed" and produced a completely wrong diagnosis —
    the agent reported Azure CLI missing on a machine where it was installed
    and logged in. Resolve through shutil.which, which honours PATHEXT."""
    src = inspect.getsource(tools)
    assert 'shutil.which("az")' in src
    assert '["az", "account"' not in src, "must not invoke az by bare name"
    assert '"az", "login"' not in src, "must not invoke az by bare name"
