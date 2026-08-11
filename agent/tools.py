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
    from pipeline import pull  # imported lazily â€” pyodbc/azure-identity are pull-only deps
    try:
        r = pull.pull_one(name, force=force)
    except Exception as e:
        return _ok(f"Pull failed for {name!r}: {type(e).__name__}: {e}\n"
                   f"Check VPN, then `az login`. Use az_login_status to distinguish the two.")
    if r["cached"]:
        return _ok(f"{name}: already cached at {r['path']} â€” not re-pulled (CLAUDE.md: never re-pull "
                   f"what cached parquet can answer). Pass force=true to override.")
    return _ok(f"{name}: pulled {r['rows']:,} rows -> {r['path']}")


@tool("pipe_create_targets", "Day-weighted Pipe Create TARGET allocation by week, at Geo/Region/Territory/All grain, for ANY quarter present in Target_Monthly.csv (e.g. quarter='Q3 FY26' or '2026-10-01'). These are the PUBLISHED targets read from the CSV â€” to see how a target was derived from bookings and assumptions, read docs/analysis/pipe-create-waterfall.md. Writes an immutable run with lineage.", {"grain": str, "key": str, "as_of": str, "quarter": str})
async def pipe_create_targets(args):
    grain = args.get("grain") or "All"
    key = args.get("key") or None
    as_of = args.get("as_of") or None
    quarter = args.get("quarter") or None
    if grain not in ("All", "Geo", "Region", "Territory"):
        return _ok(f"Unknown grain {grain!r}. Use All, Geo, Region, or Territory.")

    # Any quarter whose months exist in the CSV is computable. QUARTER_STARTS[0]
    # governs the pre-quarter buffer for ACTUALS; it does not limit target reads.
    try:
        qs = targets.resolve_quarter(quarter)
    except ValueError as e:
        return _ok(str(e))

    try:
        df = targets.weekly_target_rows(grain=grain, key=key, as_of=as_of, quarter_start=qs)
        total = targets.quarter_total(qs)
    except Exception as e:
        return _ok(f"Failed: {type(e).__name__}: {e}")

    with lineage.Run(quarter_start=qs) as run:
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
        # Emitted by code, not by prompt â€” CLAUDE.md requires this caveat on every
        # opp-count and ASP figure, and a prompt instruction degrades over a session.
        run.caveat("invariant-10-opportunities-unit")
        if grain in ("Region", "Geo"):
            run.warn("offline-grain-rollup-not-bts")
        run_dir = run.dir

    body = df.to_string(index=False)
    return _ok(
        f"Pipe Create TARGETS â€” {total['quarter']}, grain={grain}"
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


async def _derive_frame(quarters: str, grain: str = "Territory", overrides=None,
                        as_of: str | None = None):
    """Assemble the derivation inputs and solve. Shared by the derive tool and the
    what-if, so an override is evaluated against exactly the same inputs as the
    baseline — two separate assembly paths would drift and make the delta a lie.

    Returns (frame, notes). Raises on anything that makes a number impossible.
    """
    import pandas as pd
    from agent import waterfall

    qs = [targets.resolve_quarter(q.strip())
          for q in quarters.replace(";", ",").split(",") if q.strip()]
    sku = waterfall.load_sku(grain)
    book = {q: config._target_by_team("Bookings", q).sum(axis=1) for q in qs}
    as_of = as_of or str(pd.Timestamp.today().date())

    # Slip is measured on the SAME QUARTER a year earlier, per quarter being
    # solved — Q3 FY26 from Q3 FY25, Q4 FY26 from Q4 FY25. Slip is seasonal, so
    # applying one quarter's rate to every quarter imports the wrong shape.
    notes, existing = [], {}
    for i, q in enumerate(qs):
        h = waterfall.prior_year_quarter(q)

        # Pre-Q slip: only a FUTURE quarter has any. For the in-flight quarter it
        # has already happened and sits inside the observed balance, so the
        # function returns empty and the term is a no-op. Not special-casing.
        pq = None
        try:
            pq = waterfall.pre_q_slip(q, as_of, grain=grain)
            if len(pq):
                notes.append(
                    f"PRE-Q SLIP for {config.fq_label(q)}: {pq.attrs['pooled_rate']:.1%} "
                    f"at {pq.attrs['lead_days']}d lead, from {pq.attrs['measured_on']}")
        except Exception as e:
            notes.append(f"PRE-Q SLIP NOT INCLUDED for {config.fq_label(q)} "
                         f"— {type(e).__name__}: {e}")

        # Slip inflow: existing open pipe pushed out of EARLIER quarters in this
        # solve and landing here. Only quarters being solved contribute — a
        # quarter outside the solve has no measured open pipe to forward.
        inflow = None
        for earlier in qs[:i]:
            try:
                f = waterfall.slip_inflow(earlier, q, grain=grain, as_of=as_of)
                if not len(f):
                    continue
                inflow = f if inflow is None else inflow.add(f, fill_value=0.0)
                notes.append(
                    f"SLIP INFLOW {config.fq_label(earlier)} -> {config.fq_label(q)}: "
                    f"${f.sum():,.0f} ({f.attrs['destination_share']:.1%} of "
                    f"${f.attrs['slipping_value']:,.0f} slipping)")
            except Exception as e:
                notes.append(f"SLIP INFLOW NOT INCLUDED {config.fq_label(earlier)} -> "
                             f"{config.fq_label(q)} — {type(e).__name__}: {e}")

        try:
            existing[q] = waterfall.existing_pipe_bookings(
                q, [h], sku=sku, grain=grain,
                slip_from_points={h: waterfall.slip_anchor(q, as_of, h)},
                slip_snapshot_file="snapshot_hist.parquet",
                pre_q_slip_rate=pq, slip_inflow_pipe=inflow)
        except Exception as e:
            notes.append(f"SLIP NOT INCLUDED for {config.fq_label(q)} "
                         f"(needs {config.fq_label(h)}) — {type(e).__name__}: {e}")
    existing = existing or None

    won = None
    try:
        won = {q: waterfall.closed_won_at(q, grain=grain) for q in qs}
    except Exception as e:
        notes.append(f"CLOSED WON NOT INCLUDED — {type(e).__name__}: {e}")

    df = waterfall.derive_targets(sku, book, qs, grain=grain,
                                  existing_pipe_bookings=existing, closed_won=won,
                                  overrides=overrides)
    return waterfall.flag_outliers(df, grain), notes


@tool(
    "what_if_assumption",
    "Recompute a derived pipe create target with one assumption replaced, for a "
    "single territory or all of them. Use when someone challenges an input — "
    "'I don't believe the in-quarter win rate is 3%, call it 40%, what does the "
    "target become?'. Assumptions: in_quarter_win_rate, later_win_rate, q0_weight, "
    "expected_from_existing_pipe, historic_floor. Rates are fractions (0.40, not 40).",
    {"key": str, "assumption": str, "value": float, "quarters": str},
)
async def what_if_assumption(args):
    import pandas as pd
    from agent import waterfall

    key = (args.get("key") or "").strip()
    assumption = (args.get("assumption") or "").strip()
    value = args.get("value")
    if assumption not in waterfall.ASSUMPTIONS:
        return _ok(f"Cannot override {assumption!r}. Available: {', '.join(waterfall.ASSUMPTIONS)}.")
    if value is None:
        return _ok("A value is required.")
    if assumption.endswith("win_rate") and not 0 <= float(value) <= 1:
        return _ok(f"{assumption} is a fraction between 0 and 1 — got {value}. "
                   f"40% is 0.40.")

    raw = (args.get("quarters") or "Q3 FY26, Q4 FY26").replace(";", ",")
    try:
        qs = [targets.resolve_quarter(q.strip()) for q in raw.split(",") if q.strip()]
    except ValueError as e:
        return _ok(str(e))

    try:
        base, notes = await _derive_frame(raw)
        what, _ = await _derive_frame(raw, overrides={key: {assumption: float(value)}})
    except Exception as e:
        return _ok(f"Could not recompute: {type(e).__name__}: {e}")

    if key not in set(base["Territory"]):
        near = [t for t in sorted(set(base["Territory"])) if key.lower() in t.lower()]
        return _ok(f"No territory named {key!r}." + (f" Did you mean: {', '.join(near)}?" if near else ""))

    b = base.set_index(["quarter", "Territory"])
    a = what.set_index(["quarter", "Territory"])
    was = b.xs(key, level="Territory")[assumption].iloc[0]
    lines = [f"WHAT IF — {key}: {assumption} {was:.4f} -> {float(value):.4f}", ""]

    for q in [config.fq_label(x) for x in qs]:
        if (q, key) not in b.index:
            continue
        before, after = b.loc[(q, key)], a.loc[(q, key)]
        t0, t1 = before["pipe_create_target"], after["pipe_create_target"]
        delta = f"{t1 - t0:+,.0f}" + (f", {(t1 / t0 - 1):+.1%}" if t0 else "")
        lines += [
            q,
            f"  yield per $       {before['yield_per_dollar']:.4f} -> {after['yield_per_dollar']:.4f}",
            f"  required by gap   ${before['required_by_gap']:,.0f} -> ${after['required_by_gap']:,.0f}",
            f"  historic floor    ${after['historic_floor']:,.0f}"
            f"   (binding: {before['binding']} -> {after['binding']})",
            f"  TARGET            ${t0:,.0f} -> ${t1:,.0f}   ({delta})",
        ]
        if before["binding"] == "gap" and after["binding"] == "floor":
            lines.append("  NOTE: the floor now binds, so further improvement to this "
                         "assumption cannot lower the target.")
        qb = base[base.quarter == q]["pipe_create_target"].sum()
        qa = what[what.quarter == q]["pipe_create_target"].sum()
        lines += [f"  {q} all territories: ${qb:,.0f} -> ${qa:,.0f} ({qa - qb:+,.0f})", ""]

    if notes:
        lines += notes + [""]

    lines.append("This is a what-if, not a published or derived target. The override "
                 "replaces a measured assumption and flows through yield, gap, the floor "
                 "comparison and the tail pushed into later quarters.")
    return _ok("\n".join(lines))


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
    "Compose and run a read-only SQL query against Synapse for analysis the four "
    "standard report queries cannot answer â€” e.g. win rates, sales cycle, or "
    "sales cycle curves over a chosen window. SELECT/WITH only, against tables "
    "documented in docs/tables/. Read the relevant docs/tables/ contract and "
    "docs/sql/conventions.md BEFORE composing. The user sees and approves the exact "
    "SQL before it runs. Results are saved to a run with lineage.",
    {"sql": str, "purpose": str, "max_rows": int},
)
async def query(args):
    sql = (args.get("sql") or "").strip()
    purpose = (args.get("purpose") or "").strip() or "ad-hoc analysis"
    max_rows = int(args.get("max_rows") or 50_000)

    try:
        sqlguard.assert_read_only(sql, "query")
    except sqlguard.UnsafeSQL as e:
        return _ok(f"Refused: {e}\n\nThe query was not run. Rewrite it as a read against "
                   f"documented tables, or ask the user to extend the allowlist.")
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
        if truncated:
            run.warn("result-truncated")
        run_dir = run.dir

    head = df.head(30).to_string(index=False) if len(df) else "(no rows)"
    return _ok(
        f"{purpose}\n{len(df):,} rows x {len(df.columns)} columns"
        + (f"  (TRUNCATED at {max_rows:,})" if truncated else "")
        + f"\n\n{head}"
        + ("\n... (first 30 rows shown; full result in the run)" if len(df) > 30 else "")
        + f"\n\nThis was a composed query, not a standard report â€” state that when reporting "
          f"figures from it.\nRun stored: {run_dir}"
    )


@tool(
    "derive_pipe_create_target",
    "DERIVE what the pipe create target SHOULD be from current data and assumptions "
    "— sales cycle, sales cycle curve, win rates and historic floor recomputed from "
    "sku_nacv_fact over a chosen window, then goal-seek against the given bookings "
    "target, solved chronologically so each quarter's sales cycle tail feeds the next. "
    "This is the DERIVED target. It is NOT the published target in Target_Monthly.csv "
    "(use pipe_create_targets for that). Requires a pull. Read "
    "docs/analysis/pipe-create-waterfall.md before interpreting the output.",
    {"quarters": str, "grain": str, "window_start": str, "window_end": str, "slip_quarters": str},
)
async def derive_pipe_create_target(args):
    import pandas as pd
    from agent import waterfall

    grain = args.get("grain") or "Territory"
    if grain not in waterfall.GRAIN_COLS:
        return _ok(f"Unknown grain {grain!r}. Use {', '.join(waterfall.GRAIN_COLS)}.")

    raw = (args.get("quarters") or "Q3 FY26").replace(";", ",")
    try:
        qs = [targets.resolve_quarter(q.strip()) for q in raw.split(",") if q.strip()]
    except ValueError as e:
        return _ok(str(e))

    window = None
    if args.get("window_start") and args.get("window_end"):
        window = (args["window_start"], args["window_end"])

    try:
        sku = waterfall.load_sku(grain)
    except waterfall.MissingData as e:
        return _ok(f"Cannot derive: {e}")

    # The bookings target is the GIVEN input. Today it is readable from the
    # Bookings rows; it is a parameter because it will be supplied directly.
    # Per quarter, not qs[0] reused: Q3 and Q4 carry materially different bookings
    # targets, and applying the first quarter's to all of them understates the rest.
    try:
        book = {q: config._target_by_team("Bookings", q).sum(axis=1) for q in qs}
    except Exception as e:
        return _ok(f"Could not read the given bookings target: {type(e).__name__}: {e}")

    if grain != "Territory":
        return _ok("Bookings targets are keyed by territory. Use grain='Territory' "
                   "until a mapping to Region/Geo keys is agreed.")

    # Slip is part of the assumptions, not an optional extra: it supplies expected
    # bookings from pipe that already exists, which the goal seek subtracts. Without
    # it the gap is the full bookings target and required create is overstated.
    #
    # Measured PER QUARTER on the same quarter a year earlier — Q3 FY26 from
    # Q3 FY25, Q4 FY26 from Q4 FY25 — because slip is seasonal. One window applied
    # to every quarter imports the wrong shape. This is the same assembly the
    # what-if uses; keeping two copies is how they drift.
    as_of = args.get("as_of") or str(pd.Timestamp.today().date())
    existing, slip_lines = {}, []
    for q in qs:
        h = waterfall.prior_year_quarter(q)
        try:
            e = waterfall.existing_pipe_bookings(
                q, [h], sku=sku, grain=grain, window=window,
                slip_from_points={h: waterfall.slip_anchor(q, as_of, h)},
                slip_snapshot_file="snapshot_hist.parquet")
            existing[q] = e
            slip_lines.append(
                f"  {config.fq_label(q)} <- {config.fq_label(h)}: mean slip "
                f"{e.attrs.get('mean_slip_rate'):.1%}, anchored "
                f"{waterfall.slip_anchor(q, as_of, h):%Y-%m-%d}")
        except Exception as ex:
            slip_lines.append(
                f"  {config.fq_label(q)} <- {config.fq_label(h)}: SLIP NOT INCLUDED "
                f"({type(ex).__name__}: {str(ex)[:120]}). Expected bookings from existing "
                f"pipe is zero for this quarter, which OVERSTATES its required create.")
    slip_note = "Slip, per quarter, from the same quarter a year earlier:\n" + "\n".join(slip_lines)
    existing = existing or None

    # Already-won bookings are banked: pipe create only covers what is left. For an
    # in-flight quarter this is the largest single term.
    won, won_note = None, ""
    try:
        won = {q: waterfall.closed_won_at(q, grain=grain) for q in qs}
        won_note = "Closed Won included: " + ", ".join(
            f"{config.fq_label(q)} ${won[q].sum():,.0f}" for q in qs) + "."
    except waterfall.MissingData as e:
        won_note = (f"CLOSED WON NOT INCLUDED — {e} An in-flight quarter's banked "
                    f"bookings are therefore treated as still to be created, which "
                    f"OVERSTATES the required create.")
    except Exception as e:
        won_note = f"CLOSED WON NOT INCLUDED — {type(e).__name__}: {e}. Required create is overstated."

    try:
        df = waterfall.derive_targets(sku, book, qs, grain=grain, window=window,
                                      existing_pipe_bookings=existing, closed_won=won)
        summary = waterfall.summarize(df)
    except Exception as e:
        return _ok(f"Derivation failed: {type(e).__name__}: {e}")

    with lineage.Run(quarter_start=qs[0]) as run:
        run.add_input(config.DATA / "sku_nacv.parquet").add_input(config.DATA / "bts.parquet")
        run.add_input(config.TARGET_MONTHLY_CSV)
        out = run.dir / "derived_pipe_create.csv"
        df.to_csv(out, index=False)
        run.add_output(out, rows=len(df))
        run.headline(**{k: v for k, v in summary.items() if k != "by_quarter"},
                     **{f"derived_{k}": v for k, v in summary["by_quarter"].items()})
        run.caveat("invariant-10-opportunities-unit")
        run.warn("derived-not-published-target")
        if not summary["existing_pipe_supplied"]:
            run.warn("existing-pipe-bookings-omitted-overstates-required-create")
        run_dir = run.dir

    published = {}
    for q in qs:
        try:
            published[config.fq_label(q)] = targets.quarter_total(q)["pipe_target"]
        except Exception:
            pass

    lines = ["DERIVED pipe create target (recomputed from source, not read from the CSV)", ""]
    for qlabel, derived in summary["by_quarter"].items():
        pub = published.get(qlabel)
        delta = f"  vs published ${pub:,.0f}  ({derived / pub - 1:+.1%})" if pub else ""
        lines.append(f"  {qlabel}: ${derived:,.0f}{delta}")
    lines += [
        "",
        f"Floor-driven: ${summary['floor_driven']:,.0f} "
        f"({summary['floor_driven_pct']:.1%} of total) across {summary['rows_floor_bound']} rows; "
        f"{summary['rows_gap_bound']} rows are gap-driven.",
        "  floor-driven = the team must not create less than the same quarter last year.",
        "  gap-driven   = the bookings target requires it.",
        "",
        df.head(25).to_string(index=False),
    ]
    if slip_note:
        lines += ["", slip_note]
    if won_note:
        lines += ["", won_note]
    lines += ["", "This is a derived figure, not the published target. State that when reporting it.",
              f"Run stored: {run_dir}"]
    return _ok("\n".join(lines))


GTM_TOOLS = [
    az_login_status,
    azure_login,
    derive_pipe_create_target,
    list_queries,
    run_pull,
    query,
    pipe_create_targets,
    what_if_assumption,
    list_runs,
    show_run,
]

gtm_server = create_sdk_mcp_server(name="gtm", version="0.1.0", tools=GTM_TOOLS)

TOOL_NAMES = [f"mcp__gtm__{t.name}" for t in GTM_TOOLS]
