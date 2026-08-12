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


@tool("list_queries", "List the named report queries that can be pulled and cached as parquet, with what each returns. These are the CACHED bulk pulls — for anything else, compose SQL with the `query` tool rather than concluding the data is unavailable.", {})
async def list_queries(args):
    lines = ["The complete set of queries that can be run (no others are possible):"]
    for name, (_, filename, desc) in queries.REGISTRY.items():
        path = config.DATA / filename
        state = "cached" if path.exists() else "not pulled"
        lines.append(f"  {name:10} [{state:10}] {desc}")
    lines.append("\nA question needing data outside these requires a human to add a query to pipeline/queries.py.")
    return _ok("\n".join(lines))


@tool("run_pull", "Re-run one of the named registry queries against Synapse and cache it as parquet (see list_queries). Cache-first: returns the existing file without querying unless force=True. Requires VPN and a live `az login`.", {"query_name": str, "force": bool})
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
                        as_of: str | None = None, window=None):
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
                window=window, slip_snapshot_file="snapshot_hist.parquet",
                pre_q_slip_rate=pq, slip_inflow_pipe=inflow)
        except Exception as e:
            notes.append(f"SLIP NOT INCLUDED for {config.fq_label(q)} "
                         f"(needs {config.fq_label(h)}) — {type(e).__name__}: {e}")

        # Open pipe on a key the solve never visits is dropped, because the solve
        # iterates the bookings target's keys. This is USUALLY CORRECT: BTS_SQL
        # filters ActiveTeam='Active', so a disbanded team's residual pipe has no
        # target and rightly earns no create (confirmed with the model owner
        # 2026-08-11 — AMS Specialty and the DevOps teams are all inactive).
        #
        # Reported anyway, as information rather than a defect, because the same
        # path would silently swallow a LIVE team that is merely missing from the
        # mapping table — and a smaller existing-pipe term inflates required
        # create with nothing else in the output to reveal why.
        e_q = existing.get(q)
        if e_q is not None:
            orphan = e_q.index.difference(book[q].index)
            if len(orphan):
                pipe = e_q.attrs.get("open_pipe")
                lost = float(pipe.reindex(orphan).sum()) if pipe is not None else 0.0
                notes.append(
                    f"UNTARGETED PIPE EXCLUDED from {config.fq_label(q)}: "
                    f"{', '.join(map(str, orphan))} — ${lost:,.0f} of open pipe, "
                    f"${float(e_q.reindex(orphan).sum()):,.0f} of expected bookings. "
                    f"Expected for INACTIVE teams, which carry no target. Only "
                    f"investigate if a currently selling team appears here.")
    existing = existing or None

    won = None
    try:
        won = {q: waterfall.closed_won_at(q, grain=grain) for q in qs}
    except Exception as e:
        notes.append(f"CLOSED WON NOT INCLUDED — {type(e).__name__}: {e}")

    df = waterfall.derive_targets(sku, book, qs, grain=grain, window=window,
                                  existing_pipe_bookings=existing, closed_won=won,
                                  overrides=overrides)
    return waterfall.flag_outliers(df, grain), notes


@tool(
    "what_if_assumption",
    "Recompute a derived pipe create target with one assumption replaced, for a "
    "single territory or all of them. Use when someone challenges an input — "
    "'I don't believe the in-quarter win rate is 3%, call it 40%, what does the "
    "target become?'. Assumptions: in_quarter_win_rate, pre_q_win_rate, q0_weight, "
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


@tool(
    "derive_pipe_create_target",
    "DERIVE what the pipe create target SHOULD be from current data and assumptions "
    "— sales cycle, sales cycle curve, win rates and historic floor recomputed from "
    "sku_nacv_fact over a chosen window, then goal-seek against the given bookings "
    "target, solved chronologically so each quarter's sales cycle tail feeds the next. "
    "This is the DERIVED target. It is NOT the published target in Target_Monthly.csv "
    "(use pipe_create_targets for that). Requires a pull. Read "
    "docs/analysis/pipe-create-waterfall.md before interpreting the output.",
    # as_of is what selects the run REGIME: at or after a quarter's start it is
    # in flight (Pre Q slip already happened, closed won is observed); before it,
    # the quarter is a future one and carries a Pre Q slip haircut. It was read
    # but not declared until 2026-08-11, so the regime could never be set.
    # slip_quarters was declared but no longer read — removed rather than left to
    # be passed and silently ignored.
    {"quarters": str, "grain": str, "window_start": str, "window_end": str, "as_of": str},
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

    # Fail early with a readable message rather than deep inside the assembly.
    # _derive_frame loads this again; the second read is cached by pandas' parquet
    # reader and the clearer error is worth it.
    try:
        waterfall.load_sku(grain)
    except waterfall.MissingData as e:
        return _ok(f"Cannot derive: {e}")

    if grain != "Territory":
        return _ok("Bookings targets are keyed by territory. Use grain='Territory' "
                   "until a mapping to Region/Geo keys is agreed.")

    # ONE assembly path, shared with the what-if and the UI. This function used to
    # keep its own copy "so the what-if compares like with like" — and then the
    # copy drifted: pre_q_slip() and slip_inflow() were added to _derive_frame on
    # 2026-08-11 and this path silently kept returning the old numbers. A comment
    # saying "keeping two copies is how they drift" does not prevent the drift.
    try:
        df, notes = await _derive_frame(raw, grain=grain, as_of=args.get("as_of"),
                                        window=window)
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
    if notes:
        lines += ["", "Assumptions and exclusions:"] + [f"  {n}" for n in notes]
    lines += ["", "This is a derived figure, not the published target. State that when reporting it.",
              f"Run stored: {run_dir}"]
    return _ok("\n".join(lines))


def _resolve_run(run_id: str | None):
    """A run id, defaulting to the most recent. Returns (run_id, csv_path)."""
    runs = lineage.list_runs()
    if not runs:
        raise ValueError("No runs recorded yet. Derive a target first, then export it.")
    rid = run_id or runs[-1]["run_id"]
    d = config.RUNS / rid
    if not d.exists():
        raise ValueError(f"Run {rid!r} not found. Use list_runs to see what exists.")
    csvs = sorted(d.glob("*.csv"))
    if not csvs:
        raise ValueError(f"Run {rid!r} stored no CSV to export.")
    return rid, csvs[0]


@tool(
    "export_excel",
    "Write a stored run to a formatted Excel workbook in workspace/exports/. "
    "One sheet per quarter plus a Summary sheet with the derivation ledger. "
    "Dollars formatted #,##0, rates 0.0%, header frozen, columns auto-width. "
    "Never overwrites: a name collision gets a date suffix. Defaults to the most "
    "recent run. Use this whenever the user asks for the waterfall 'as Excel', "
    "'as a file', or to send on.",
    {"run_id": str, "name": str},
)
async def export_excel(args):
    import pandas as pd
    from agent import exports

    try:
        rid, csv = _resolve_run(args.get("run_id") or None)
        df = pd.read_csv(csv)
    except Exception as e:
        return _ok(f"Cannot export: {e}")

    # Legacy runs carry pre-rename headers. Same migration the UI applies, so a
    # run exported today reads the same as the same run viewed today.
    from gtm_ui.server import LEGACY_COLUMNS
    renamed = {a: b for a, b in LEGACY_COLUMNS.items() if a in df.columns}
    df = df.rename(columns=renamed)

    name = args.get("name") or f"{csv.stem}_{rid[:17]}"
    try:
        path = exports.export_path(name, ".xlsx")
    except Exception as e:
        return _ok(f"Cannot export: {e}")

    sheets = []
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        if "quarter" in df.columns:
            # Summary first so the workbook opens on the totals, not row 1 of
            # a 27-row territory table.
            agg = {c: "sum" for c in df.columns
                   if pd.api.types.is_numeric_dtype(df[c]) and not exports.RATE.search(c)}
            if agg:
                summary = df.groupby("quarter", sort=False).agg(agg).reset_index()
                exports.write_sheet(w, summary, "Summary")
                sheets.append(("Summary", len(summary)))
            for q, g in df.groupby("quarter", sort=False):
                exports.write_sheet(w, g.reset_index(drop=True), str(q))
                sheets.append((str(q), len(g)))
        else:
            exports.write_sheet(w, df, "Data")
            sheets.append(("Data", len(df)))

    total = ""
    if "pipe_create_target" in df.columns and "quarter" in df.columns:
        per_q = df.groupby("quarter", sort=False)["pipe_create_target"].sum()
        total = " | ".join(f"{q} ${v:,.0f}" for q, v in per_q.items())

    return _ok(
        f"{path}\n"
        f"{len(df)} rows from run {rid}, across {len(sheets)} sheet(s): "
        f"{', '.join(n for n, _ in sheets)}.\n"
        + (f"Derived pipe create: {total}. DERIVED, not the published target.\n" if total else "")
        + (f"Migrated legacy columns: {', '.join(sorted(renamed.values()))}.\n" if renamed else "")
    )


@tool(
    "export_chart",
    "Write a PNG chart from a stored run to workspace/exports/, 150 dpi, titled "
    "with quarter and grain. kind='pipe_create' bars the derived target by "
    "territory; kind='waterfall' shows the bookings bridge (target -> closed won "
    "-> existing pipe -> sales cycle tail -> gap) for one quarter. Defaults to "
    "the most recent run.",
    {"run_id": str, "kind": str, "quarter": str, "name": str},
)
async def export_chart(args):
    import pandas as pd
    import matplotlib
    matplotlib.use("Agg")            # no display in this process
    import matplotlib.pyplot as plt
    from agent import exports

    kind = (args.get("kind") or "pipe_create").strip()
    try:
        rid, csv = _resolve_run(args.get("run_id") or None)
        df = pd.read_csv(csv)
    except Exception as e:
        return _ok(f"Cannot export: {e}")

    grain = next((c for c in ("Territory", "Region", "Geo") if c in df.columns), None)
    quarters = list(dict.fromkeys(df["quarter"])) if "quarter" in df.columns else []
    q = args.get("quarter") or (quarters[0] if quarters else "")
    sub = df[df["quarter"] == q] if q and "quarter" in df.columns else df

    fig, ax = plt.subplots(figsize=(11, 6.5))
    money = matplotlib.ticker.FuncFormatter(
        lambda v, _: ("-" if v < 0 else "") + f"${abs(v)/1e6:,.1f}M")

    if kind == "waterfall":
        terms = [("bookings_target", "Bookings target"),
                 ("closed_won", "less Closed Won"),
                 ("expected_from_existing_pipe", "less existing pipe"),
                 ("sales_cycle_tail_from_earlier_quarters", "less sales cycle tail"),
                 ("gap", "= Gap to fill")]
        have = [(c, l) for c, l in terms if c in sub.columns]
        raw = [sub[c].sum() for c, _ in have]
        # Deltas: first bar and last bar are TOTALS resting on zero; the middle
        # ones are deductions that float between the running balance. Drawing
        # everything from zero (the obvious version) is a grouped bar chart, not
        # a bridge — the eye cannot follow the subtraction.
        deltas = [raw[0]] + [-v for v in raw[1:-1]] + [raw[-1]]
        bases, run, last = [], 0.0, len(deltas) - 1
        for i, d in enumerate(deltas):
            if i in (0, last):
                bases.append(0.0)                 # totals sit on the axis
                if i == 0:
                    run = d
            else:
                run += d                          # d is negative for a deduction
                # A deduction HANGS DOWN from the old balance to the new one, so
                # its base is the NEW (lower) balance. Using `run - d` puts the
                # base at the old balance and the bar floats upward — it looks
                # plausible and is exactly backwards.
                bases.append(run if d < 0 else run - d)
        heights = [abs(d) for d in deltas]
        colors = ["#3f6f9f"] + ["#b4553f"] * (len(deltas) - 2) + ["#4a7c59"]
        labels = [l for _, l in have]
        ax.bar(labels, heights, bottom=bases, color=colors, width=0.6)

        # Connectors trace the running balance from one bar to the next.
        for i in range(last):
            y = bases[i] if deltas[i] < 0 else bases[i] + heights[i]
            ax.plot([i + 0.3, i + 1.3], [y, y], color="#999", lw=1, ls="--", zorder=0)

        for i, d in enumerate(deltas):
            top = bases[i] + heights[i]
            ax.text(i, top + max(heights) * 0.015,
                    ("$0" if abs(d) < 1 else f"${d:,.0f}"),
                    ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("Bookings $")
        ax.yaxis.set_major_formatter(money)
        ax.set_ylim(0, max(b + h for b, h in zip(bases, heights)) * 1.12)
        plt.setp(ax.get_xticklabels(), rotation=12, ha="right")
    else:
        if grain is None or "pipe_create_target" not in sub.columns:
            plt.close(fig)
            return _ok("Cannot chart: the run has no grain column or no pipe_create_target.")
        d = sub.groupby(grain)["pipe_create_target"].sum().sort_values()
        ax.barh(d.index, d.values, color="#3f6f9f")
        ax.set_xlabel("Derived pipe create target $")
        ax.xaxis.set_major_formatter(money)
        ax.tick_params(axis="y", labelsize=8)

    ax.set_title(f"{q or 'All quarters'} — {kind.replace('_', ' ')} by "
                 f"{grain or 'run'} (DERIVED, not published)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    name = args.get("name") or f"{kind}_{str(q).replace(' ', '_')}_{rid[:17]}"
    try:
        path = exports.export_path(name, ".png")
    except Exception as e:
        plt.close(fig)
        return _ok(f"Cannot export: {e}")
    fig.savefig(path, dpi=150)
    plt.close(fig)

    return _ok(f"{path}\n"
               f"{kind.replace('_', ' ')} chart for {q or 'all quarters'} at "
               f"{grain or 'run'} grain, {len(sub)} rows, 150 dpi.\n"
               f"DERIVED figures — state that when sharing.")


GTM_TOOLS = [
    az_login_status,
    azure_login,
    derive_pipe_create_target,
    list_queries,
    run_pull,
    query,
    pipe_create_targets,
    what_if_assumption,
    export_excel,
    export_chart,
    list_runs,
    show_run,
]

gtm_server = create_sdk_mcp_server(name="gtm", version="0.1.0", tools=GTM_TOOLS)

TOOL_NAMES = [f"mcp__gtm__{t.name}" for t in GTM_TOOLS]
