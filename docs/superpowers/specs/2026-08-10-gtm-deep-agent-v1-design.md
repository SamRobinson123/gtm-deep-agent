# GTM Deep Agent — v1 Design

**Date:** 2026-08-10
**Status:** Approved, not yet implemented
**Scope:** A Python Agent SDK chat application that answers questions about the
Pipe Create model from the `docs/` corpus, with citations.

> This file lives in `docs/superpowers/specs/`, which `docs/README.md` and the
> root `CLAUDE.md` both declare **non-context**. Nothing here may be cited as
> fact about Tricentis data or the models. It records decisions, not truth.

---

## Goal

Replace `hello.py` — a single-shot `query()` call — with a persistent chat
application that a Strategic Analytics lead can hold a conversation with about
the Pipe Create model, and that never answers from memory.

**In scope (v1):** reading `docs/`, answering with citations, a doc-retrieval
subagent, mechanical enforcement of the read-before-answer rule, **and computing
day-weighted target allocation from the real `data/Target_Monthly.csv`.**

### Scope revision — 2026-08-10, after data arrived

`data/Target_Monthly.csv` (63,171 x 36), `data/Headcount.xlsx`, and
`data/legacy/Pipeline Creation Quarter Product V20.xlsm` were added to the
project. This changes v1: the **target half** of the model is now computable
offline against real data, with no fixtures and no invented numbers.

The load path is already validated — it reproduces **$201,789,918** as the Q3 FY26
all-Geo `Pipeline` target, matching the figure quoted in the root `CLAUDE.md`
output conventions. That total is v1's regression anchor.

The **actuals half** stays deferred: it needs the snapshot feed from Synapse,
which is not on this machine. So v1 can answer "what is the target for W7?" but
not "what did we create in W7?".

### Scope revision 2 — 2026-08-10, data-pull capability

The agent is to be given the ability to pull from Synapse itself, rather than
waiting for data to appear. This adds to v1:

- `pipeline/config.py` and `pipeline/pull.py`, materialized from
  `docs/analysis/gtm-dashboard.md`. **Not** `pipe_create.py` — that comes once
  there is real data to test it against.
- Scoped custom tools for the data path, plus `Bash` allowlisted to Azure CLI
  session commands only.

This makes actuals reachable, so the deferrals below shrink accordingly.

**Out of scope (v1), with reasons:**

| Deferred | Why |
|---|---|
| `pipeline/pipe_create.py` | Materialize it **after** a successful pull, so it is verified against real data rather than written blind. Ordering only — it is in v1. |
| Writing xlsx / PNG to `workspace/exports/` | Nothing worth exporting until the model runs. |

**No fixtures, ever.** Confirmed by the user 2026-08-10: the model runs on real
data or it does not run. Synthetic snapshot data is not an acceptable substitute
for testing the actuals path — a fixture that drifts from the real schema teaches
the agent wrong things and produces numbers that look real. Tests cover the
target path (real `Target_Monthly.csv`) and the boundaries; the actuals path is
verified against a real pull.
| A verifier subagent | Its job is re-deriving numbers. There are no numbers yet. |
| Session persistence (`session_store`) | Adds state and failure modes before the loop itself is proven. |
| Synapse / VPN access | Every v1 question is answerable offline from `docs/`. |

Each deferral is a v2 candidate, gated on real data arriving.

---

## Context this design assumes

Established earlier on 2026-08-10, and already applied to the repo:

- The context corpus moved from `gtm-context/gtm/context/` to `docs/`
  (17 markdown files, ~4,700 lines). Internal links are relative and survived.
- `docs/README.md` is the **single** routing table. The root `CLAUDE.md` now
  points at it instead of maintaining a competing map.
- Rule precedence is explicit in both files: `docs/README.md` hard rules 1–10
  govern data and SQL; the `CLAUDE.md` invariants govern Pipe Create work.
  Genuine contradictions are surfaced, never silently resolved.
- `docs/models/pipe-create.md` (221 lines) was extracted from
  `docs/analysis/gtm-dashboard.md` (1,644 → 1,444 lines), which now carries a
  pointer stub. This is the primary doc for v1's subject matter.

**`<REPO>` does not exist on this machine.** The *source* of `config.py`,
`pull.py`, and `pipe_create.py` is recoverable from the docs; the *data* is not.

---

## Verified SDK facts

Checked against the installed `claude_agent_sdk` **0.2.134** rather than
recalled. Re-verify if the version changes.

- `ClaudeAgentOptions` exposes `agents`, `hooks`, `setting_sources`,
  `disallowed_tools`, `system_prompt`, `session_store`, `effort`.
- `AgentDefinition(description, prompt, tools, disallowedTools, model, ...)`.
- `ClaudeSDKClient(options=None, transport=None)` — an async context manager
  holding one session across turns. `query()` is one-shot and discards state.

### The CLAUDE.md loading gotcha

`setting_sources=["project"]` makes the SDK *eligible* to load `CLAUDE.md`, but
the file is injected as part of the **`claude_code` system prompt preset**.
`hello.py` sets `system_prompt=None` and therefore very likely runs with **no
`CLAUDE.md` and no invariants at all**.

v1 must set:

```python
system_prompt={"type": "preset", "preset": "claude_code", "append": OPERATING_RULES}
```

**This must be proven by a test, not assumed.** See Testing.

---

## Architecture

Four modules, each with one job:

```
main.py               entry point — argparse, load_dotenv(), launch the loop
agent/options.py      builds ClaudeAgentOptions. Pure function, no I/O.
agent/subagents.py    the doc-retrieval AgentDefinition.
agent/loop.py         the REPL — read, send, stream, print. /exit and /new.
agent/hooks.py        PreToolUse guards — read scope, and the Bash allowlist.
agent/tools.py        scoped SDK tools: az_login_status, run_pull. No SQL param.
agent/sqlguard.py     read-only assertion on rendered templates. Pure.
agent/lineage.py      run_id, manifest assembly, hashing, index append. Pure + fs.
pipeline/config.py    materialized from docs — quarters, paths, grain helpers.
pipeline/queries.py   the four named report queries, verbatim from the docs.
pipeline/pull.py      materialized from docs — Synapse → cached parquet.
```

`pipeline/queries.py` is the security boundary made physical: it is the complete
set of statements the agent can cause to run against Synapse. Reviewing the
agent's warehouse capability means reading one file.

`options.py` is a pure function of its arguments returning a
`ClaudeAgentOptions`. It holds every subtle configuration decision and can be
asserted against without spending a token — the highest-value test surface in
the project.

### Configuration

| Setting | Value | Reason |
|---|---|---|
| `cwd` | project root | Anchors relative doc paths |
| `setting_sources` | `["project"]` | Loads this project's `CLAUDE.md`; excludes global plugins and hooks, so behavior is reproducible |
| `model` | `claude-sonnet-5` | Current Sonnet. `hello.py` pins the stale `claude-sonnet-4-6` |
| `system_prompt` | `claude_code` preset + append | Required for `CLAUDE.md` to load — see gotcha above |
| `allowed_tools` | `Read`, `Glob`, `Grep`, `Task`, `Bash`, and the three scoped tools | Read docs, delegate, check the Azure session, pull |
| `disallowed_tools` | `Write`, `Edit`, `WebSearch`, `WebFetch` | The agent still cannot modify the repo, and answers still cannot come from the open web |
| `permission_mode` | `default` | **Changed, and confirmed by the user 2026-08-10.** `bypassPermissions` was justified only while every tool was read-only. The prompt is not just friction: it is a second control, ensuring a person without warehouse authority cannot use the agent to run queries on their behalf. Do not "optimize" it away. |
| `can_use_tool` | callback | Requires confirmation before any pull that writes cached parquet — `CLAUDE.md` requires asking before writing pipeline outputs |

`agents` carries the doc-retrieval definition; `hooks` carries the read-scope
guard and the Bash allowlist.

### The scoped tools

Built with `@tool` + `create_sdk_mcp_server`, so each has a typed schema and the
guardrails live in Python rather than in a prompt.

| Tool | Contract |
|---|---|
| `az_login_status()` | Reports whether the Azure CLI session is live. `pull.py` authenticates via `AzureCliCredential`, so a stale session is the most likely failure and should produce a clear message, not an ODBC error. |
| `run_pull(query_name, quarter)` | Re-runs **one of the four named report queries**. **Cache-first:** returns the existing parquet path without hitting Synapse when one exists, per `CLAUDE.md`. `force=True` requires confirmation. |

### No arbitrary SQL — the fixed query registry

**The agent cannot compose SQL.** It can only re-run the queries that already
define the GTM reports. `pipeline/queries.py` holds exactly four, materialized
verbatim from `docs/analysis/gtm-dashboard.md`:

| Name | Source table | Writes |
|---|---|---|
| `SKU_SQL` | `[src].[sku_nacv_fact]` | `data/sku_nacv.parquet` |
| `SNAP_SQL` | `[rep].[trf_opp_daily_snapshot_new]` | `data/snapshot.parquet` |
| `AGE_SQL` | `[rep].[trf_opp_daily_snapshot_new]` | `data/opp_ages.parquet` |
| `BTS_SQL` | `[sharepoint].[Map_Booking_Team_Static_live]` | `data/bts.parquet` |

`run_pull` takes a **name from that registry**, never a SQL string. There is no
tool that accepts SQL text. Parameters are limited to the quarter/date range and
are rendered by `config.py` — the same values `pull.py` uses in normal operation.

This is a deliberately narrow interface. The agent's entire warehouse capability
is *"re-run one of the four queries that build the reports."* It cannot write,
cannot create views, cannot read tables outside those four, and cannot express a
statement the reports do not already make.

**Guardrails that follow from the design, not from vigilance:**

| Risk | Why it cannot happen |
|---|---|
| Writing to the lake | No tool accepts SQL; the four templates are `SELECT`-only |
| Creating views / DDL | Same |
| Querying unrelated tables | The registry names four tables |
| SQL injection via parameters | Parameters are dates and quarter identifiers, validated and rendered by `config.py` |
| Silent template drift | `sqlguard` re-validates each rendered template at call time |

`sqlguard.py` therefore shrinks: it no longer validates agent-authored SQL, only
asserts that a rendered registry template is still read-only. That is
defence-in-depth against a bad edit to `queries.py`, not the primary control.
**The primary control is that no SQL-accepting interface exists.**

### `Bash` allowlist

A `PreToolUse` hook permits only Azure CLI session commands (`az login`,
`az account show`, `az account get-access-token`) and denies everything else with
a reason. Raw shell is not the interface for data access — the scoped tools are.

### Security posture

`pull.py` authenticates by taking a token from the user's `az login` session via
`AzureCliCredential`. **The agent therefore queries Synapse as the user, with the
user's permissions**, and holds no credential of its own. Two consequences:

1. Read-only enforcement is the agent's responsibility, not the warehouse's —
   the user's account may well have write permissions. Hence `sqlguard`.
2. `SYNAPSE_CONN_STR` and `ANTHROPIC_API_KEY` live in `.env` and must never be
   committed or echoed into a response. `.gitignore` covers `.env`; the Bash
   allowlist prevents `cat .env`.

### The doc-retrieval subagent

One `AgentDefinition` with `tools=["Read", "Glob", "Grep"]`. The main agent
delegates "find what the docs say about X" and receives **claims with file paths
and line numbers — not file contents**.

This is the core context-economics decision. `docs/analysis/gtm-dashboard.md` is
1,444 lines; loading it into the main window to answer one question crowds out
the reasoning. The subagent reads it in its own window and returns a citation.

Its prompt must state explicitly: return findings and paths, never paste whole
files, and say "not covered in the corpus" rather than inferring.

### Enforcing the citation rule

`CLAUDE.md` forbids answering from memory. A prompt instruction alone degrades
over a long session.

A `PreToolUse` hook inspects `Read`/`Glob`/`Grep` paths and denies any target
outside `docs/`, with `docs/superpowers/` denied as well — it is not context.
This makes "answered without reading a doc" mechanically visible instead of a
matter of good behavior.

**Open decision, deferred to implementation:** what happens when the agent
answers with no citation at all. Options are a `Stop` hook that rejects the
turn, a warning printed to the user, or prompt-only. This is a UX-versus-rigor
trade-off and is the user's call, not a default worth guessing.

---

## Run lineage — every model run is immutable and reviewable

**Requirement (user, 2026-08-10):** when the model is run, the output must be
stored with enough lineage that it can be reviewed later, **even after newer
iterations exist**. A run is never overwritten.

This also discharges the `CLAUDE.md` rule *"never overwrite an existing export"*
and makes it structural rather than a naming convention.

### Layout

```
workspace/runs/
├── index.jsonl                     append-only, one line per run
├── latest.json                     pointer to the most recent run_id
└── 2026-08-10T175700Z_a3f9c1/      immutable once written
    ├── manifest.json
    ├── gtm_pipe_create.parquet
    └── gtm_pipe_create.json
```

`run_id` is `<UTC timestamp>_<6-char random>`. A collision is a hard error, never
an overwrite. **A run directory is written once and never modified** — corrections
are new runs, so a superseded number stays reviewable alongside the one that
replaced it.

`latest.json` is a pointer *file*, not a symlink: symlinks on Windows require
elevation or developer mode, and this must work without either.

### `manifest.json`

Enough to answer "why did this run produce this number?" without rerunning it.

| Field | Purpose |
|---|---|
| `run_id`, `started_at`, `finished_at` | Identity and timing, UTC |
| `git_commit`, `git_dirty` | Which code version. `git_dirty: true` means uncommitted edits — the number is not reproducible, and the manifest says so |
| `quarter`, `quarter_start`, `quarter_end` | The grain being run |
| `month_columns` | The **derived** `M2026xx` list — recorded as evidence invariant 1 was honored, not hardcoded |
| `weeks`, `partial_weeks` | e.g. `14`, `[1, 14]` — invariant 3 made visible |
| `inputs[]` | Path, **sha256**, size, mtime for `Target_Monthly.csv`, `snapshot.parquet`, `Headcount.xlsx` |
| `code[]` | sha256 of `pipe_create.py`, `queries.py`, `config.py` |
| `outputs[]` | Path, sha256, row count |
| `headline` | QTD created, QTD target, attainment, opp count — the figures a reader checks first |
| `caveats[]` | Machine-emitted, e.g. `invariant-10-opportunities-unit`, `apac-asia-age-sea-no-target` |
| `warnings[]` | Data-quality conditions detected at runtime, e.g. `geoterritory-case-collision` |

Input hashing is what makes lineage real. "The target file changed" is otherwise
invisible, and two runs producing different numbers from an identically-named
input is exactly the situation this must explain.

### Why caveats are emitted by code

Root `CLAUDE.md` requires the invariant-10 caveat to travel with every opp-count
and ASP figure. A prompt instruction degrades over a long session; a `caveats[]`
array written by the code that produced the number does not. The agent reads the
manifest and reports what is in it.

### Retention

Runs are never auto-deleted. `workspace/exports/` is gitignored, so run history
is local and unbounded — acceptable given each run is a few hundred KB.

---

## Data flow

```
user question
  → ClaudeSDKClient (session held across turns)
    → main agent reads docs/README.md, follows the task→file map
    → delegates broad lookups to the doc-retrieval subagent (Task)
      → subagent greps/reads under docs/, returns claims + paths
    → main agent composes the answer
  → streamed to terminal, ending with the doc path(s) it came from
```

Every `Read` passes the `PreToolUse` scope guard.

---

## Error handling

| Failure | Behavior |
|---|---|
| `ANTHROPIC_API_KEY` missing | Fail at startup with a clear message. Never a stack trace mid-conversation. |
| `CLINotFoundError` | Tell the user to install the Claude Code CLI; the SDK requires it. |
| Question not covered by `docs/` | Say "the docs don't cover this" — per `CLAUDE.md`. Never infer. |
| Read blocked by the scope guard | Deny with a reason the agent can act on, redirecting it to `docs/`. |
| `CLAUDE.md` invariant conflicts with a README hard rule | Surface both and stop. Never reconcile silently. |
| Ctrl-C mid-stream | Cancel the turn, keep the session, return to the prompt. |
| `az login` stale or absent | `az_login_status()` reports it in plain language and tells the user to run `az login`. Never surface a raw ODBC error. |
| No VPN | Connection fails at `pull.py`. Distinguish "cannot reach Synapse" from "not authenticated" — they need different fixes. |
| Agent wants data the four queries don't return | Say so plainly: the question needs a query the reports don't currently make, and that is a change for a human to author in `pipeline/queries.py`. Do **not** work around it. |
| `sqlguard` rejects a rendered template | Treat as a defect in `queries.py`, not a user error. Fail loudly — a template that stopped being read-only is a serious problem. |
| Cached parquet already present | `run_pull` returns the cached path and says so. Re-pulling requires explicit confirmation. |

---

## Testing

Written test-first, per `superpowers:test-driven-development`.

**Unit — `agent/options.py`, no tokens spent:**

1. `Write`, `Edit`, and `Bash` appear in `disallowed_tools`.
2. `system_prompt` is the `claude_code` preset dict, not `None` — this is the
   regression test for the loading gotcha.
3. `setting_sources == ["project"]`.
4. `agents` contains the doc-retrieval definition, and its tools are read-only.

**Unit — `agent/hooks.py`, no tokens spent:**

5. A read of `docs/models/pipe-create.md` is allowed.
6. A read of `C:/Windows/System32/...` is denied.
7. A read of `docs/superpowers/specs/...` is denied.
8. Path traversal (`docs/../../etc/passwd`) is denied — resolve before comparing.

**Unit — `agent/targets.py`, real data, no tokens spent:**

9. Loading `data/Target_Monthly.csv` with the documented strip recipe yields
   **$201,789,918** for Q3 FY26 all-Geo `Pipeline`. The regression anchor.
10. Month columns are derived from the quarter start, never hardcoded
    (invariant 1). Test by asserting a different quarter start yields different
    columns.
11. Q3 FY26 allocates across **14 weeks**, W1 and W14 partial (invariant 3).
12. A not-yet-started week has `days_counted == 0` and a target of `0.0`, which
    collapses attainment to null with no special-casing (invariant 4).
13. `APAC Asia AGE` / `APAC Asia SEA` return `None`, not `0.0` — a missing team is
    absent, not zero (invariant 9).
14. ASP is derived as `Pipeline / Opportunities`; no `ASP` row is ever read
    (invariant 2).

**Unit — the warehouse boundary, no tokens, no network. Written first:**

15. `run_pull` rejects any `query_name` outside the four-entry registry.
16. No exposed tool accepts a SQL string — assert by inspecting the tool schemas,
    so adding one later fails the suite rather than passing silently.
17. Each of the four rendered templates passes `sqlguard` as read-only.
18. `sqlguard` rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `MERGE`,
    `ALTER`, `GRANT`, `EXEC`, `SELECT … INTO`, and stacked statements — the
    defence-in-depth check against a bad edit to `queries.py`.
19. Quarter/date parameters are validated; a non-date value is rejected before
    rendering.

**Unit — `agent/hooks.py`, Bash allowlist:**

20. `az account show` is allowed; `cat .env`, `rm -rf`, and `python -c …` are
    denied with a reason.

**Unit — `agent/tools.py`:**

21. `run_pull` returns the cached path without re-pulling when parquet exists —
    the `CLAUDE.md` never-re-pull rule, made mechanical.
22. `force=True` triggers the confirmation callback rather than pulling directly.

**Unit — `agent/lineage.py`, no tokens, no network:**

23. Two runs produce distinct `run_id`s, and writing into an existing run
    directory raises rather than overwrites.
24. `manifest.json` records the sha256 of every input; changing
    `Target_Monthly.csv` by one byte changes the recorded hash.
25. `git_dirty` is `true` when the working tree has uncommitted changes, and the
    manifest is still written — a non-reproducible run is recorded as such, not
    refused.
26. `month_columns` in the manifest is derived from the quarter start and matches
    what the model used.
27. `caveats[]` contains `invariant-10-opportunities-unit` whenever the run
    emits any opp-count or ASP figure.
28. `index.jsonl` gains exactly one line per run and is never rewritten.
29. `latest.json` points at the newest `run_id` and is a regular file, not a
    symlink.

**Integration — one live call:**

30. Ask *"Why does Pipe Create have no CloseDate filter?"* and assert the response
    cites `docs/models/pipe-create.md`. The answer is verifiably in the corpus
    (the module was written deliberately inverse to `coverage.py`), so this test
    proves the whole chain: `CLAUDE.md` loaded, routing followed, doc read,
    citation returned.

Tests 9 and 30 prove the design — one for compute, one for retrieval. Tests
10–14 turn five prose invariants into executable assertions, which is the point:
an invariant that only lives in a markdown file degrades, while one with a test
fails loudly. Tests 15–19 are the warehouse boundary and are written first.

Test 16 deserves note: it asserts a *negative* — that no tool anywhere exposes a
free-text SQL parameter. The narrow interface is the entire security model, so
the suite should fail the moment someone widens it, including a future me who
thinks an ad-hoc query tool would be convenient.

**Not tested automatically:** an actual Synapse pull. It needs VPN and a live
`az login`, and it hits production. It is verified manually, once, against a
known row count.

---

## Success criteria

v1 is done when:

- `python main.py` opens a conversation that survives multiple turns.
- Asking a Pipe Create question returns an answer citing a specific `docs/` path.
- Asking something outside the corpus returns "the docs don't cover this."
- Asking for a Q3 FY26 target returns a real number from
  `data/Target_Monthly.csv`, at the requested grain, with the invariant-10 caveat
  attached to any opp-count or ASP figure.
- The agent can report Azure session status, and can pull a quarter's snapshot
  parquet — returning the cached copy without re-pulling when one exists.
- The agent cannot write or edit repo files, cannot run arbitrary shell commands,
  and **cannot express any SQL statement at all** — its warehouse capability is
  limited to re-running the four named report queries.
- Running the model writes an immutable `workspace/runs/<run_id>/` with a
  manifest carrying input hashes, code hashes, git commit, derived month columns,
  headline figures, and machine-emitted caveats — and a second run leaves the
  first byte-for-byte intact.
- All 30 tests pass.

## Unresolved, carried into implementation

1. **The no-citation policy** — hard-reject via a `Stop` hook, warn, or
   prompt-only. A rigor-versus-friction call for the user.
2. **`GeoTerritory` case collisions** — `AMS DevOps`/`AMS Devops`,
   `EMEA DevOps`/`EMEA Devops`, `EMEA SeaLights`/`EMEA Sealights` are distinct
   groupby keys orphaning $8.7M across 24 months. Normalize or preserve? Until
   decided, `GeoTerritory`-grain output is checked against these pairs.
   See [`../../tables/target-monthly.md`](../../tables/target-monthly.md).
3. **`Headcount.xlsx` and `Pipeline AE` / `Pipeline AE Count`** — undocumented.
   The user will explain the capacity logic; the doc gets written from that,
   not inferred.

---

## Follow-up work, not part of v1

- `hello.py` is superseded by `main.py`. Delete or reduce it to a smoke test.
- The repo is not under git. Initialize before implementation so the move
  already performed and everything after it is recoverable.
- v2, gated on real data: compute tools, `workspace/exports/` writing, the
  verifier subagent, and pointing `<REPO>` at the real pipeline.
