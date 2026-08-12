# The agent — what it can do, and where the walls are

**Status:** current as of 2026-08-11. This is the one file that describes the
agent *itself* rather than the data. Everything else in `docs/` is the context
corpus the agent reads; this is the machine that reads it.

Read this when you are changing what the agent can do, when a capability seems
missing, or when you are deciding whether something belongs in a tool or a doc.

---

## Why this file exists

Between 2026-08-10 and 2026-08-11 the agent had a guarded SQL tool and an
operating rule that said *"You cannot write SQL."* It refused work it was
equipped for, and nothing failed — no test, no error, no wrong number. **From the
agent's side, a rule denying a capability is indistinguishable from the tool not
existing.**

So: when a tool is added or a boundary moves, `agent/options.py::OPERATING_RULES`
and this file change in the same commit. That is the whole point of it.

---

## The tools

| Tool | What it does |
|---|---|
| `list_queries` | The registry of cacheable bulk pulls |
| `run_pull` | Run one registry query, cache as parquet. Cache-first |
| **`query`** | **Compose and run any read-only SQL.** The main investigation tool |
| `pipe_create_targets` | PUBLISHED targets, read from `Target_Monthly.csv` |
| `derive_pipe_create_target` | DERIVED target — the full waterfall solve |
| `show_assumptions` | The inputs a target rests on, without solving |
| `slip_analysis` | Slip: rate, destinations, Pre Q, forecast, create-date cohorts |
| `what_if_assumption` | Re-solve with one assumption replaced |
| `export_excel` / `export_chart` | Deliverables into `workspace/exports/` |
| `list_runs` / `show_run` | Previous runs and their manifests |
| `az_login_status` / `azure_login` | Azure session; `azure_login` opens the MFA prompt |

Plus `Read`, `Glob`, `Grep`, `Task` and a narrow `Bash`. **`Write` and `Edit` are
disallowed** — files leave through the export tools or not at all.

### PUBLISHED vs DERIVED

The distinction the agent must never blur. `pipe_create_targets` reads a number a
previous planning cycle produced. `derive_pipe_create_target` computes what the
number *would be* from current data. Report which one, and when reporting a
derived figure give the published one and the delta beside it — the gap is the
finding.

---

## The four boundaries

Each is a single chokepoint, enforced in code rather than by the agent
remembering. If you are adding capability, put it behind the matching one.

### 1. `pipeline/queries.py` — bulk pulls
The registry of named queries cached as parquet. **Adding one requires human
review**; the agent cannot extend it. Read-only is re-asserted at call time by
`sqlguard`, as defence against a bad edit here rather than a check on the agent.

### 2. `agent/sqlguard.py` — composed SQL
Two jobs, deliberately not equally hard:

- **SAFETY, absolute.** Single statement; must begin `SELECT` or `WITH`; no
  `INSERT`/`UPDATE`/`DELETE`/`DROP`/`CREATE`/`MERGE`/`EXEC`/… The agent may run
  any read; it may not write to the database or create views.
- **CONTRACT, advisory.** A table with no `docs/tables/` entry may be queried;
  `assert_read_only()` *returns* the off-contract tables so the caller can report
  that the figure rests on no documented contract. It used to raise, which
  blocked legitimate investigation on a correctness concern. `strict_tables=True`
  restores the refusal.

Second control, outside this module: every composed statement is shown to the
user and does not run until approved.

### 3. `agent/exports.py` — writes
`export_path()` confines every write to `workspace/exports/`. **The agent
supplies a NAME, never a path.** `safe_stem()` whitelists characters rather than
trying to detect traversal, so `../../etc/passwd` and `C:\Windows\evil` collapse
to flat tokens; `_confine()` re-checks the resolved path. Never overwrites — a
collision gets a date, then a counter.

### 4. `agent/hooks.py` — read scope and shell
Reads allowed under `docs/`, `data/`, `workspace/`, `pipeline/`, `agent/`,
`tests/`. Denied: `docs/superpowers/` (design history, not fact) and `.env` and
friends. Bash is restricted to `az` session commands — data access goes through
the tools, and the allowlist checks for chained commands so
`az account show && cat .env` cannot smuggle a second one through.

---

## Runs and lineage

Every tool that produces a figure opens a `lineage.Run`: input hashes, code
hashes, git commit and dirtiness, headline figures, caveats and warnings, written
to `workspace/runs/<id>/`. An earlier number stays inspectable after newer
iterations exist, and `git_dirty` marks a run that cannot be reproduced.

Exports and the UI default to the **latest** run. Always state which `run_id`
produced a figure — a run made in another window is a real possibility and the
numbers genuinely differ.

---

## The UI

`python -m gtm_ui` serves a local chat UI on **127.0.0.1 only, with no auth**,
in a process that can reach production. It relays tool approval to the browser
rather than suppressing it: `permission_mode` stays `"default"` so someone
without warehouse authority cannot use the agent to query on their behalf. Do not
change it to `bypassPermissions`.

Endpoints beyond chat: `/api/runs`, `/api/runs/{id}/derivation` (the ledger) and
`/api/runs/{id}/waterfall` (per-row, with outlier flags and overridable cells).

---

## Adding capability — the checklist

1. **Behind which boundary?** New bulk query → `queries.py` (human review). Ad-hoc
   read → already possible via `query`. New file type → `exports.py`.
2. **Declare the schema exactly.** Every key the handler reads must be declared
   and every declared key must be read —
   `tests/test_boundary.py::test_tool_schema_matches_the_args_the_handler_reads`
   enforces it. A key read but not declared can never be set; a key declared but
   not read is silently ignored. Both have happened.
3. **Open a lineage run** if it produces a figure.
4. **Update `OPERATING_RULES` in the same commit.** See "Why this file exists".
5. **Route any new doc from `docs/README.md`.** An unrouted doc is invisible.
