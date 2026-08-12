"""SQL safety — the primary control on composed queries.

Role note: this module began as defence-in-depth over four human-written templates.
Since the agent now composes its own SQL, it is the PRIMARY control. It stands
between a generated statement and a production warehouse, so it is deliberately
strict: anything not positively recognised as a read against a documented table is
rejected.

Second control, not implemented here: every composed query is shown to the user in
an approval card and does not run until approved. This module decides what is even
allowed to be offered.

Updated 2026-08-11: the DOCUMENTED-TABLE check is advisory, not a refusal. The
agent is meant to investigate freely; being off-contract is a correctness caveat
to report, not a reason to block. Read-only enforcement is unchanged and remains
absolute.
"""
from __future__ import annotations

import re

# Tables with a documented contract in docs/tables/. Querying anything else means
# working without a contract, where results can be silently wrong in ways no doc
# warns about. Extend this only alongside a docs/tables/ entry.
ALLOWED_TABLES = {
    "sfdc_trf.opportunity_live",
    "sfdc_trf.opportunity_field_history_live",
    "sfdc_trf.opportunityhistory_live",
    "sfdc_trf.opportunitylineitem_live",
    "sfdc_trf.account_live",
    "sfdc_trf.user_live",
    "sfdc_trf.contact_live",
    "sfdc_trf.task_live",
    "src.sku_nacv_fact",
    "src.trf_account_dimension",
    "rep.trf_opp_daily_snapshot_new",
    "rep.trf_marketing_opps_dimension",
    "sharepoint.map_booking_team_static_live",
}

FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "MERGE", "ALTER", "CREATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "BACKUP", "RESTORE", "BULK",
    "OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "SHUTDOWN", "RECONFIGURE",
    "SP_", "XP_", "INTO", "WAITFOR",
)

MAX_ROWS = 500_000
TIMEOUT_S = 120

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")
# FROM/JOIN followed by a table reference, bracketed or bare, with optional db prefix.
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+((?:\[[^\]]+\]|[A-Za-z_][\w]*)(?:\s*\.\s*(?:\[[^\]]+\]|[A-Za-z_][\w]*)){0,2})",
    re.I,
)


class UnsafeSQL(ValueError):
    """A statement is not a permitted read."""


def _strip(sql: str) -> str:
    """Remove comments and string literals.

    Comments first, so a write cannot hide behind one; strings second, so a literal
    like 'Closed - Duplicate' cannot trip a keyword match. Reversing the order would
    leave comment text exposed to the string regex.
    """
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    return _STRING.sub("''", sql)


def _norm_table(ref: str) -> str:
    """'[sfdc_trf].[opportunity_live]' -> 'sfdc_trf.opportunity_live' (lowercased).

    Drops a leading database qualifier so db.schema.table matches schema.table.
    """
    parts = [p.strip().strip("[]").lower() for p in ref.split(".")]
    parts = [p for p in parts if p]
    if len(parts) > 2:
        parts = parts[-2:]
    return ".".join(parts)


def tables_in(sql: str) -> list[str]:
    """Every table referenced by FROM or JOIN, normalised.

    CTE names appear here too; callers filter them out via `extra_allowed`.
    """
    return [_norm_table(m.group(1)) for m in _TABLE_REF.finditer(_strip(sql))]


def cte_names(sql: str) -> set[str]:
    """Names bound by WITH, so a CTE reference is not mistaken for a table."""
    stripped = _strip(sql)
    return {m.group(1).lower() for m in re.finditer(r"(?:\bWITH\s+|,\s*)([A-Za-z_]\w*)\s+AS\s*\(", stripped, re.I)}


def assert_read_only(sql: str, name: str = "<query>", check_tables: bool = True,
                     strict_tables: bool = False) -> list[str]:
    """Raise UnsafeSQL unless `sql` is a single READ. Return off-contract tables.

    Two different things are being enforced here and they are not equally hard:

    SAFETY — non-negotiable, always raises. Single statement, begins with SELECT
    or WITH, and contains no FORBIDDEN keyword (INSERT/UPDATE/DELETE/DROP/CREATE/
    MERGE/EXEC/...). This is the line the model owner drew on 2026-08-11: the
    agent may write and run any query it likes, but may not write to the database
    or create views.

    CONTRACT — advisory by default. A table with no docs/tables/ entry can be
    queried, but the caller is told which ones so it can say the result rests on
    no documented contract. This used to raise, which blocked legitimate
    investigation on a correctness concern rather than a safety one. Pass
    strict_tables=True to restore the hard refusal.

    `check_tables=False` skips the contract check entirely — only for the registry
    templates, whose tables are documented by construction.

    Returns the sorted list of off-contract tables (empty when all are documented),
    NOT the sql. Callers that want the sql back already have it.
    """
    if not sql or not sql.strip():
        raise UnsafeSQL(f"{name}: empty query")

    stripped = _strip(sql)

    # Stacked statements. A trailing semicolon is fine; anything after one is not.
    # Validating only the first statement is the classic bypass.
    if ";" in stripped.rstrip().rstrip(";"):
        raise UnsafeSQL(f"{name}: multiple statements are not allowed")

    upper = stripped.upper()

    first = re.match(r"\s*(\w+)", upper)
    if not first or first.group(1) not in ("SELECT", "WITH"):
        raise UnsafeSQL(
            f"{name}: must begin with SELECT or WITH, got {first.group(1) if first else '?'}"
        )

    for kw in FORBIDDEN:
        pattern = rf"\b{kw}" if kw.endswith("_") else rf"\b{kw}\b"
        if re.search(pattern, upper):
            raise UnsafeSQL(f"{name}: forbidden keyword {kw!r}")

    if not check_tables:
        return []

    allowed = ALLOWED_TABLES | cte_names(sql)
    unknown = sorted({t for t in tables_in(sql) if t not in allowed})
    if unknown and strict_tables:
        raise UnsafeSQL(
            f"{name}: table(s) not documented in docs/tables/: {', '.join(unknown)}. "
            "Querying an undocumented table means working without a contract. "
            "Add a docs/tables/ entry and extend ALLOWED_TABLES first."
        )
    return unknown
