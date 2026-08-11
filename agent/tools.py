"""Scoped SDK tools — the agent's entire non-file capability.

DESIGN RULE, load-bearing: no tool here takes a SQL string. The agent can only
name one of the four queries in pipeline/queries.py. Adding a free-text SQL
parameter would remove the security model, and tests/test_boundary.py fails if
anyone does.
"""
from __future__ import annotations

import json
import subprocess

from claude_agent_sdk import create_sdk_mcp_server, tool

from agent import lineage, targets
from pipeline import config, queries


def _ok(text: str):
    return {"content": [{"type": "text", "text": text}]}


@tool("az_login_status", "Check whether the Azure CLI session is live. Synapse pulls authenticate via `az login`, so a stale session is the most likely pull failure.", {})
async def az_login_status(args):
    try:
        r = subprocess.run(
            ["az", "account", "show", "--output", "json"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return _ok("Azure CLI is not installed or not on PATH. Pulls will fail until it is.")
    except subprocess.TimeoutExpired:
        return _ok("`az account show` timed out after 30s.")
    if r.returncode != 0:
        return _ok(f"NOT logged in. Run `az login` before pulling.\n{r.stderr.strip()[:400]}")
    acct = json.loads(r.stdout)
    return _ok(f"Logged in as {acct.get('user', {}).get('name')} on subscription {acct.get('name')!r} ({acct.get('id')}).")


@tool("list_queries", "List the four named report queries the agent is permitted to run against Synapse, with what each returns.", {})
async def list_queries(args):
    lines = ["The complete set of queries that can be run (no others are possible):"]
    for name, (_, filename, desc) in queries.REGISTRY.items():
        path = config.DATA / filename
        state = "cached" if path.exists() else "not pulled"
        lines.append(f"  {name:10} [{state:10}] {desc}")
    lines.append("\nA question needing data outside these requires a human to add a query to pipeline/queries.py.")
    return _ok("\n".join(lines))


@tool("run_pull", "Re-run one of the four named report queries against Synapse and cache it as parquet. Cache-first: returns the existing file without querying unless force=True. Requires VPN and a live `az login`.", {"query_name": str, "force": bool})
async def run_pull(args):
    name = args.get("query_name")
    force = bool(args.get("force", False))
    if name not in queries.REGISTRY:
        return _ok(f"Refused: {name!r} is not a known query. Permitted: {', '.join(queries.QUERY_NAMES)}.")
    from pipeline import pull  # imported lazily — pyodbc/azure-identity are pull-only deps
    try:
        r = pull.pull_one(name, force=force)
    except Exception as e:
        return _ok(f"Pull failed for {name!r}: {type(e).__name__}: {e}\n"
                   f"Check VPN, then `az login`. Use az_login_status to distinguish the two.")
    if r["cached"]:
        return _ok(f"{name}: already cached at {r['path']} — not re-pulled (CLAUDE.md: never re-pull "
                   f"what cached parquet can answer). Pass force=true to override.")
    return _ok(f"{name}: pulled {r['rows']:,} rows -> {r['path']}")


@tool("pipe_create_targets", "Day-weighted Pipe Create TARGET allocation by week for a quarter, at Geo/Region/Territory/All grain. Targets only — actuals need the snapshot feed. Writes an immutable run with lineage.", {"grain": str, "key": str, "as_of": str})
async def pipe_create_targets(args):
    grain = args.get("grain") or "All"
    key = args.get("key") or None
    as_of = args.get("as_of") or None
    if grain not in ("All", "Geo", "Region", "Territory"):
        return _ok(f"Unknown grain {grain!r}. Use All, Geo, Region, or Territory.")
    try:
        df = targets.weekly_target_rows(grain=grain, key=key, as_of=as_of)
        total = targets.quarter_total()
    except Exception as e:
        return _ok(f"Failed: {type(e).__name__}: {e}")

    with lineage.Run() as run:
        run.add_input(config.TARGET_MONTHLY_CSV)
        out = run.dir / "pipe_create_targets.csv"
        df.to_csv(out, index=False)
        run.add_output(out, rows=len(df))
        run.headline(
            grain=grain, key=key or "All",
            quarter_pipe_target=total["pipe_target"],
            quarter_opp_target=total["opp_target"],
            quarter_asp=total["asp"],
            weeks=len(df),
        )
        # Emitted by code, not by prompt — CLAUDE.md requires this caveat on every
        # opp-count and ASP figure, and a prompt instruction degrades over a session.
        run.caveat("invariant-10-opportunities-unit")
        if grain in ("Region", "Geo"):
            run.warn("offline-grain-rollup-not-bts")
        run_dir = run.dir

    body = df.to_string(index=False)
    return _ok(
        f"Pipe Create TARGETS — {total['quarter']}, grain={grain}"
        + (f", key={key}" if key else "")
        + f"\nQuarter target: ${total['pipe_target']:,.0f} | "
          f"{total['opp_target']:,.0f} opps | ASP ${total['asp']:,.0f}\n\n"
        + body
        + "\n\nCAVEAT (invariant 10): the Opportunities target counts opp-product-lines, "
          "not distinct opps. Opp-count and ASP figures are provisional; dollars are trustworthy."
        + (f"\nWARNING: Region/Geo rollup uses Target_Monthly.csv's own hierarchy, not the BTS "
           f"mapping (invariant 7). May differ from pipe_create.py." if grain in ("Region", "Geo") else "")
        + f"\n\nRun stored: {run_dir}"
    )


@tool("list_runs", "List previous model runs with their headline figures, so an earlier number can be reviewed even after newer iterations exist.", {})
async def list_runs(args):
    runs = lineage.list_runs()
    if not runs:
        return _ok("No runs recorded yet.")
    lines = [f"{len(runs)} run(s), oldest first:"]
    for r in runs:
        dirty = " [DIRTY TREE - not reproducible]" if r.get("git_dirty") else ""
        lines.append(f"  {r['run_id']}  {r['quarter']}  {json.dumps(r.get('headline', {}))}{dirty}")
    return _ok("\n".join(lines))


@tool("show_run", "Show the full manifest for one run: input hashes, code hashes, git commit, derived month columns, headline figures, caveats.", {"run_id": str})
async def show_run(args):
    try:
        m = lineage.load_manifest(args["run_id"])
    except Exception as e:
        return _ok(f"Could not load run: {e}")
    return _ok(json.dumps(m, indent=2))


GTM_TOOLS = [
    az_login_status,
    list_queries,
    run_pull,
    pipe_create_targets,
    list_runs,
    show_run,
]

gtm_server = create_sdk_mcp_server(name="gtm", version="0.1.0", tools=GTM_TOOLS)

TOOL_NAMES = [f"mcp__gtm__{t.name}" for t in GTM_TOOLS]
