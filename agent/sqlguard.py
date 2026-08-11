"""Read-only assertion on rendered query templates.

THIS IS NOT THE PRIMARY CONTROL. The primary control is that no tool accepts SQL
text at all — the agent can only name one of the four queries in
pipeline/queries.py. This module is defence-in-depth against a bad edit to that
file, so a template that stopped being read-only fails loudly instead of running.

Because it only ever sees templates a human wrote, it can afford to be strict:
anything it does not positively recognise as a read is rejected.
"""
from __future__ import annotations

import re

# Statements that write, change structure, change permissions, or execute code.
FORBIDDEN = (
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "MERGE", "ALTER", "CREATE",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "BACKUP", "RESTORE", "BULK",
    "OPENROWSET", "OPENQUERY", "SP_", "XP_", "INTO",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")


class UnsafeSQL(ValueError):
    """A rendered template is not a read-only statement."""


def _strip(sql: str) -> str:
    """Remove comments and string literals.

    Comments are stripped so a write cannot hide behind one; string literals are
    stripped so a legitimate value like 'Closed - Duplicate' cannot trip a keyword
    match. Order matters — strings first would leave comment text exposed to the
    string regex.
    """
    sql = _BLOCK_COMMENT.sub(" ", sql)
    sql = _LINE_COMMENT.sub(" ", sql)
    sql = _STRING.sub("''", sql)
    return sql


def assert_read_only(sql: str, name: str = "<query>") -> str:
    """Raise UnsafeSQL unless `sql` is a single read-only SELECT/WITH statement."""
    if not sql or not sql.strip():
        raise UnsafeSQL(f"{name}: empty query")

    stripped = _strip(sql)

    # Stacked statements. Trailing semicolons are fine; a semicolon with anything
    # after it is not. Validating only the first statement is the classic bypass.
    if ";" in stripped.rstrip().rstrip(";"):
        raise UnsafeSQL(f"{name}: multiple statements are not allowed")

    upper = stripped.upper()

    first = re.match(r"\s*(\w+)", upper)
    if not first or first.group(1) not in ("SELECT", "WITH"):
        raise UnsafeSQL(f"{name}: must begin with SELECT or WITH, got {first.group(1) if first else '?'}")

    for kw in FORBIDDEN:
        # SP_/XP_ are prefixes; the rest are whole words. INTO is included to catch
        # SELECT ... INTO, which reads like a select and creates a table.
        pattern = rf"\b{kw}" if kw.endswith("_") else rf"\b{kw}\b"
        if re.search(pattern, upper):
            raise UnsafeSQL(f"{name}: forbidden keyword {kw!r}")

    return sql
