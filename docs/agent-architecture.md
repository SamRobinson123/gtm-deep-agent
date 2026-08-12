# The agent — what it can do, and where the walls are

**Status:** current as of 2026-08-11, AFTER the v2 migration. This is the one
file that describes the agent *itself* rather than the data. Everything else in
`docs/` is the context corpus the agent reads; this is the machine that reads it.

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
| **`query`** | **Compose and run any read-only SQL.** The one capability that stays a tool, because it is a security boundary |
| `az_login_status` / `azure_login` | Azure session; `azure_login` opens the MFA prompt |

**Three tools, and the count is asserted** in `tests/test_pipeline_cli.py`.
Widening it is a test failure, not a drift.

Everything else is ordinary code — runnable as `python -m pipeline.X` or
importable by a scratch script, which is reviewable in a way a frozen tool
interface is not:

| Module | What it does |
|---|---|
| `pipeline.targets_cli` | PUBLISHED targets, read from `Target_Monthly.csv` |
| `pipeline.waterfall_cli` | `derive` / `whatif` / `assumptions` — the DERIVED side |
| `pipeline.slip_cli` | rate, destinations, pre_q, forecast, create-date cohorts |
| `pipeline.export_cli` | Excel and charts into `workspace/exports/` |
| `pipeline.checks` | `run_all(df)` — run before reporting any figure |
| `pipeline.derive` | the shared assembly the waterfall CLI and the UI both use |
| `agent.lineage` | `list_runs()`, and `Run` for recording a figure |

Plus the full thinking tool set: `Read`, `Write`, `Edit`, `Glob`, `Grep`,
`Bash`, `Task`, `TodoWrite`. **Only `WebSearch` and `WebFetch` are disallowed** —
a web result has no contract and no lineage.

The loop: **think in `workspace/scratch/`, remember in `workspace/notes/`,
report through lineage.** Write a script, run it, read the result, delete it.

### PUBLISHED vs DERIVED

The distinction the agent must never blur. `pipeline.targets_cli` reads a number
a previous planning cycle produced. `pipeline.waterfall_cli derive` computes what
the number *would be* from current data. Report which one, and when reporting a
derived figure give the published one and the delta beside it — the gap is the
finding.

---

## The six boundaries

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

### 4. `agent/hooks.py` — what no approval can grant
The v1 read confinement and Bash allowlist are **deleted**. General Bash is the
point, and the controls moved:

- **write-denied:** `docs/**`, `CLAUDE.md`, `.claude/settings.json`, `.env*`,
  `data/**`, and any *existing* `workspace/runs/<id>/`. Everything else —
  `pipeline/`, `agent/`, `tests/` — is editable with approval.
- **read-denied:** credential filenames, and `docs/superpowers/` (design
  history, not fact). The credential rule also applies to Bash command strings.

### 5. The environment — the real warehouse control
`SYNAPSE_CONN_STR` is never placed in `os.environ` (`main.load_secrets`, and
`pipeline/config.py` deliberately does not call `load_dotenv`). A scratch script
asking for it gets a KeyError, so no subprocess can inherit it.
`pipeline/pull.synapse_conn_str()` reads it from the file at call time.

### 6. `.claude/settings.json` — where the friction falls
`permission_mode` stays `"default"`. The allow rules make **thinking free and
acting on the world approved**: scratch scripts, `python -m pipeline.*` and
pytest run unprompted; repo edits, arbitrary Bash and the `query` tool still
ask. The agent cannot edit this file.

---

## Runs and lineage

Every `pipeline/` CLI that produces a figure opens a `lineage.Run` — and any
scratch computation whose number will be reported must do the same: input
hashes, code hashes, git commit and dirtiness, headline figures, caveats and
warnings, written to `workspace/runs/<id>/`. An earlier number stays inspectable
after newer iterations exist, and `git_dirty` marks a run that cannot be
reproduced.

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

## Verification — there are no golden numbers

Established 2026-08-11: no verified golden output figures exist for this model.
What is golden is the **process** — the docs. Four layers, strongest first:
invariant property tests; `pipeline/checks.py`; aggregate-level reconciliation to
the legacy workbook (a match is corroboration, a mismatch is a finding); and the
verifier subagent, which re-derives a run in its own context window.

**Every figure states which layer backed it.** An unverified number is allowed;
an unlabelled one is not.

## Adding capability — the checklist

1. **Behind which boundary?** New bulk query → `queries.py` (human review). Ad-hoc
   read → already possible via `query`. Computation → a scratch script, or a
   `pipeline/` module if it will be reused. New file type → `exports.py`.
2. **Declare the schema exactly.** Every key the handler reads must be declared
   and every declared key must be read —
   `tests/test_boundary.py::test_tool_schema_matches_the_args_the_handler_reads`
   enforces it. A key read but not declared can never be set; a key declared but
   not read is silently ignored. Both have happened.
3. **Open a lineage run** if it produces a figure.
4. **Update `OPERATING_RULES` in the same commit.** See "Why this file exists".
5. **Route any new doc from `docs/README.md`.** An unrouted doc is invisible.
