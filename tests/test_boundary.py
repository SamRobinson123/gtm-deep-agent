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
    table, column set or non-date filter, it stops being a windowing variant and
    becomes a new capability that needs its own review.

    The date predicates legitimately differ in SHAPE, not just in value — the
    historic pull ORs several disjoint ranges — so they are stripped rather than
    normalised, and everything else must match exactly.
    """
    import re

    def without_dates(sql: str) -> str:
        lines = [ln for ln in sql.splitlines() if "snapshot_date" not in ln or "snap.snapshot_date," in ln]
        return " ".join(" ".join(lines).split())

    assert without_dates(queries.SNAP_HIST_SQL) == without_dates(queries.SNAP_SQL)
    # And the historic one must still be date-bounded — an unbounded pull of this
    # table is the whole feed.
    assert re.search(r"snapshot_date\s*>=", queries.SNAP_HIST_SQL)
    assert re.search(r"snapshot_date\s*<=", queries.SNAP_HIST_SQL)


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
    """Returns the OFF-CONTRACT tables, so an empty list means fully documented."""
    sql, _, _ = queries.REGISTRY[name]
    assert sqlguard.assert_read_only(sql, name) == []


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
    assert sqlguard.assert_read_only(sql) == []


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
    assert sqlguard.assert_read_only(sql) == []


def test_cte_name_is_not_mistaken_for_a_table():
    assert "cohort" in sqlguard.cte_names("WITH cohort AS (SELECT 1) SELECT * FROM cohort")


def test_an_undocumented_table_warns_but_runs():
    """Changed 2026-08-11. Being off-contract is a CORRECTNESS caveat, not a
    safety one — blocking it stopped legitimate investigation. The table is
    reported so the caller can say the result rests on no documented contract."""
    off = sqlguard.assert_read_only("SELECT * FROM [dbo].[secret_payroll]")
    assert off == ["dbo.secret_payroll"]


def test_strict_tables_restores_the_hard_refusal():
    with pytest.raises(sqlguard.UnsafeSQL, match="not documented"):
        sqlguard.assert_read_only("SELECT * FROM [dbo].[secret_payroll]",
                                  strict_tables=True)


@pytest.mark.parametrize("write", [
    "INSERT INTO t VALUES (1)",
    "CREATE VIEW v AS SELECT 1",
    "DROP TABLE t",
    "UPDATE t SET a = 1",
    "SELECT a INTO newtbl FROM [sfdc_trf].[opportunity_live]",
])
def test_writing_to_the_database_is_still_absolutely_refused(write):
    """The line the model owner drew: compose and run any READ, never a write.
    Relaxing the table check must not have relaxed this."""
    with pytest.raises(sqlguard.UnsafeSQL):
        sqlguard.assert_read_only(write)


@pytest.mark.parametrize("t", ["sfdc_trf.opportunity_live", "rep.trf_opp_daily_snapshot_new"])
def test_allowlist_covers_the_tables_the_derivation_needs(t):
    assert t in sqlguard.ALLOWED_TABLES


# --- hooks ------------------------------------------------------------------
#
# RETIRED 2026-08-11, v2 step 2. The read-confinement tests
# (test_read_scope_allows_docs_and_data, ..._denies_specs_and_env_and_outside,
# ..._denies_traversal) and test_bash_allowlist are GONE ON PURPOSE: their
# subject — ALLOWED_READ_ROOTS and ALLOWED_BASH_PREFIXES — was deleted, not
# relaxed. General Bash and open reads are the point of v2.
#
# They are replaced rather than merely removed. tests/test_hooks.py covers what
# survives (credential denial on both Read paths and Bash strings,
# docs/superpowers/ read denial, traversal resolving before the name check) plus
# what is new (write protection on docs/, CLAUDE.md, settings.json, data/, and
# existing run dirs). The security argument that replaces the cage lives in
# tests/test_env_isolation.py.


# --- options ----------------------------------------------------------------
#
# MOVED 2026-08-11, v2 step 3 -> tests/test_options.py, which now also asserts
# the settings.json allow rules and the new OPERATING_RULES sections.
#
# One of them did not move, it was RETIRED: `test_write_tools_are_disallowed`
# asserted that Write and Edit were in disallowed_tools. In v2 that is exactly
# backwards — the agent writes itself a script in workspace/scratch/, runs it and
# deletes it, and without Write/Edit it cannot think at all. test_options.py
# carries `test_write_and_edit_are_no_longer_disallowed` in its place, so a merge
# that restores the v1 list fails loudly instead of silently re-caging the agent.


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


# --- tool schemas match what the handlers actually read -----------------------

def _tool_arg_keys(fn_src):
    """Every args.get("x") / args["x"] key a handler reads."""
    import re
    return set(re.findall(r'args\.get\(\s*["\'](\w+)["\']', fn_src)) | \
           set(re.findall(r'args\[\s*["\'](\w+)["\']\s*\]', fn_src))


@pytest.mark.parametrize("t", [t for t in tools.GTM_TOOLS], ids=lambda t: t.name)
def test_tool_schema_matches_the_args_the_handler_reads(t):
    """A key read but not declared can never be set by the model; a key declared
    but not read is silently ignored. Both happened: derive_pipe_create_target
    read `as_of` without declaring it, so the in-flight vs future regime could
    not be selected, and declared `slip_quarters` long after it stopped reading
    it. Neither shows up in any other test.
    """
    import inspect
    src = inspect.getsource(t.handler)
    read = _tool_arg_keys(src)
    declared = set(t.input_schema or {})

    undeclared = read - declared
    assert not undeclared, (
        f"{t.name} reads {sorted(undeclared)} but does not declare them — "
        f"the model can never set them")

    unread = declared - read
    assert not unread, (
        f"{t.name} declares {sorted(unread)} but never reads them — "
        f"the model can pass them and be silently ignored")
