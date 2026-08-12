"""Scoped SDK tools â€” the agent's entire non-file capability.

DESIGN RULE, load-bearing: exactly ONE tool takes SQL (`query`), it validates
through agent.sqlguard BEFORE executing, and it is never auto-approved — the user
sees the exact statement and approves it.

This reverses the original four-queries-only design (2026-08-10). Deriving win
rates or sales cycle curves over an arbitrary window is impossible without composing
SQL, so the control moved: from "the agent cannot express SQL" to "the agent can
only express reads against documented tables, and you approve each one".
"""
from __future__ import annotations

import json
import subprocess

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent import lineage, sqlguard, targets
from pipeline import config, queries


def _ok(text: str):
    return {"content": [{"type": "text", "text": text}]}


def _az() -> str | None:
    """Resolve the Azure CLI executable.

    On Windows `az` is `az.CMD`, a batch file. subprocess without shell=True cannot
    launch a .cmd by bare name — it raises FileNotFoundError, which reads exactly
    like "not installed" and produces a completely wrong diagnosis. shutil.which
    honours PATHEXT and returns the real path.
    """
    import shutil
    return shutil.which("az")


@tool("az_login_status", "Check whether the Azure CLI session is live. Synapse pulls authenticate via `az login`, so a stale session is the most likely pull failure.", {})
async def az_login_status(args):
    az = _az()
    if not az:
        return _ok("Azure CLI is not installed or not on PATH. Pulls will fail until it is.")
    try:
        r = subprocess.run(
            [az, "account", "show", "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _ok(f"Azure CLI resolved to {az} but could not be executed.")
    except subprocess.TimeoutExpired:
        return _ok("`az account show` timed out after 30s.")
    if r.returncode != 0:
        return _ok(f"NOT logged in. Run `az login` before pulling.\n{r.stderr.strip()[:400]}")
    acct = json.loads(r.stdout)
    return _ok(f"Logged in as {acct.get('user', {}).get('name')} on subscription {acct.get('name')!r} ({acct.get('id')}).")


@tool("azure_login", "Sign in to Azure for the Synapse database scope. Launches `az login --scope https://database.windows.net/.default`, which opens your browser for MFA. Use when az_login_status reports you are not signed in, OR when a pull fails with AADSTS50078 (MFA expired for the database audience) — a general az login does not satisfy that.", {"tenant": str})
async def azure_login(args):
    import asyncio

    az = _az()
    if not az:
        return _ok("Azure CLI is not installed or not on PATH. Install it, then retry.")
    # Synapse needs a token for the database scope specifically. A general `az login`
    # can leave MFA unsatisfied for that audience (AADSTS50078), which surfaces later
    # as a confusing credential error rather than a login prompt.
    cmd = [az, "login", "--scope", "https://database.windows.net/.default", "--only-show-errors"]
    if args.get("tenant"):
        cmd[2:2] = ["--tenant", args["tenant"]]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return _ok(f"Azure CLI resolved to {az} but could not be executed.")
    try:
        # az login authenticates through a BROWSER, not a terminal prompt â€” which is
        # why it works from a subprocess. MFA lands in the user's browser window.
        _, err = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        return _ok("`az login` timed out after 5 minutes â€” the browser sign-in was not completed.")
    if proc.returncode != 0:
        return _ok(f"`az login` failed:\n{(err or b'').decode(errors='replace')[:600]}")
    return _ok("Signed in to Azure. Synapse queries can now run.")


@tool(
    "query",
    "Compose and run ANY read-only SQL query against Synapse. THIS IS THE MAIN "
    "INVESTIGATION TOOL — reach for it whenever cached parquet and the five "
    "registry queries cannot answer the question, rather than saying the data is "
    "unavailable. Write the SQL yourself: joins, CTEs, window functions, any "
    "aggregation, over any window. SELECT/WITH only — writes (INSERT, UPDATE, "
    "DELETE, CREATE VIEW, DROP, EXEC) are refused outright. A table with no "
    "docs/tables/ contract may be queried, and is reported back so you can flag "
    "it. Read docs/sql/conventions.md and the relevant docs/tables/ contract "
    "BEFORE composing — they carry the stage, date and financial-column rules "
    "that separate a right answer from a plausible-looking one. The user approves "
    "the exact SQL before it runs; if the connection fails, call az_login_status "
    "then azure_login. Results are saved to a run with lineage.",
    {"sql": str, "purpose": str, "max_rows": int},
)
async def query(args):
    sql = (args.get("sql") or "").strip()
    purpose = (args.get("purpose") or "").strip() or "ad-hoc analysis"
    max_rows = int(args.get("max_rows") or 50_000)

    # Returns the OFF-CONTRACT tables rather than raising on them. Only a WRITE
    # raises now — being undocumented is a correctness caveat to report, not a
    # reason to block an investigation.
    try:
        off_contract = sqlguard.assert_read_only(sql, "query")
    except sqlguard.UnsafeSQL as e:
        return _ok(f"Refused: {e}\n\nThe query was not run. Only reads are permitted — "
                   f"rewrite it as a SELECT or WITH. Writing to the database or creating "
                   f"views is outside what this agent can do, by design.")
    if max_rows > sqlguard.MAX_ROWS:
        max_rows = sqlguard.MAX_ROWS

    from pipeline import pull  # lazy â€” pyodbc/azure-identity are pull-only deps
    import pandas as pd

    try:
        conn = pull.get_conn()
    except Exception as e:
        return _ok(f"Could not connect: {type(e).__name__}: {e}\n"
                   f"Check VPN, then run azure_login. az_login_status distinguishes the two.")
    try:
        df = pd.read_sql(sql, conn)
    except Exception as e:
        return _ok(f"Query failed: {type(e).__name__}: {e}")
    finally:
        conn.close()

    truncated = len(df) > max_rows
    if truncated:
        df = df.head(max_rows)

    with lineage.Run() as run:
        out = run.dir / "query_result.csv"
        df.to_csv(out, index=False)
        (run.dir / "query.sql").write_text(sql, encoding="utf-8")
        run.add_output(out, rows=len(df)).add_output(run.dir / "query.sql")
        run.headline(purpose=purpose, rows=len(df), columns=list(df.columns)[:20])
        run.warn("composed-query-not-a-standard-report")
        if off_contract:
            run.warn("query-touches-undocumented-tables")
        if truncated:
            run.warn("result-truncated")
        run_dir = run.dir

    head = df.head(30).to_string(index=False) if len(df) else "(no rows)"
    return _ok(
        f"{purpose}\n{len(df):,} rows x {len(df.columns)} columns"
        + (f"  (TRUNCATED at {max_rows:,})" if truncated else "")
        + f"\n\n{head}"
        + ("\n... (first 30 rows shown; full result in the run)" if len(df) > 30 else "")
        + (f"\n\nOFF CONTRACT: {', '.join(off_contract)} — no docs/tables/ entry, so no "
           f"documented contract covers these columns. The query ran; say so when "
           f"reporting figures from it." if off_contract else "")
        + f"\n\nThis was a composed query, not a standard report — state that when reporting "
          f"figures from it.\nRun stored: {run_dir}"
    )


# EXACTLY THREE, and the count is asserted in tests/test_pipeline_cli.py.
#
# v1 had fourteen: a tool per question, which meant the thinking happened in
# advance, by a human, and the agent executed it. v2 keeps only the three that
# are BOUNDARIES rather than conveniences:
#
#   query            sqlguard validates it and the user approves each statement.
#                    A capability that must not be expressible any other way.
#   az_login_status  } MFA needs an interactive browser launch, which a scratch
#   azure_login      } script cannot do.
#
# Everything else became ordinary code under pipeline/, runnable as
# `python -m pipeline.X` or importable by a scratch script — reviewable in a way
# a frozen tool interface is not. See pipeline/{targets,waterfall,slip,export}_cli.py.
GTM_TOOLS = [
    query,
    az_login_status,
    azure_login,
]

gtm_server = create_sdk_mcp_server(name="gtm", version="0.1.0", tools=GTM_TOOLS)

TOOL_NAMES = [f"mcp__gtm__{t.name}" for t in GTM_TOOLS]
