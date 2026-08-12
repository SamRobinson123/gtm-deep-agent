# GTM Deep Agent — v2 Design: Generic Capability, Judgment in Context

**Date:** 2026-08-11
**Status:** Approved, not yet implemented
**Supersedes:** the capability model of
[`2026-08-10-gtm-deep-agent-v1-design.md`](2026-08-10-gtm-deep-agent-v1-design.md).
The lineage, secrets, and warehouse-approval decisions from v1 survive; the
tool-per-question capability model does not.

> Lives in `docs/superpowers/specs/`, which `docs/README.md` and the root
> `CLAUDE.md` declare **non-context**. Nothing here may be cited as fact about
> Tricentis data or the models. It records decisions, not truth.

---

## The philosophy shift

v1 was built cage-first: the agent could only do what a hand-built tool let it
do. Every capability was a Python function written in advance — which means the
thinking happened in advance, by the human, and the agent executed it.

v2 inverts this to the architecture Claude Code itself uses:

**Capability is generic. Judgment lives in context.**

The agent gets the full Claude Code tool set — Read, Write, Edit, Glob, Grep,
Bash, Task, TodoWrite. When a question needs computation, it writes itself a
script in `workspace/scratch/`, runs it, inspects the result, and deletes the
script. Nothing about *how* it computes is frozen into tools.

What makes it *this* agent rather than generic Claude Code is everything else:

- `docs/` — 17 files of data contracts, SQL conventions, and model logic that
  no generic agent has
- `CLAUDE.md` invariants + `docs/README.md` hard rules — the instincts
- `agent/lineage.py` — the reporting discipline, now a library its own scripts
  import
- `workspace/notes/` — persistent memory across sessions
- the guarded `query` tool — the one capability that stays a tool, because it
  is a security boundary, not a convenience

This is context-tuning, not weight-tuning. Every improvement to a doc improves
every future session, and nothing is frozen.

---

## What changes

### Tools

| v1 | v2 |
|---|---|
| `allowed_tools`: Read, Glob, Grep, Task, Bash + 9 bespoke gtm tools | Read, Write, Edit, Glob, Grep, Bash, Task, TodoWrite, NotebookEdit + **3** gtm tools |
| `disallowed_tools`: Write, Edit, NotebookEdit, WebSearch, WebFetch | WebSearch, WebFetch only |
| Bash restricted to an `az` prefix allowlist | Bash is general; `permission_mode="default"` prompts for approval |
| Read confined to project subdirs | Reads are open **except** credentials (see Secrets) |

The three surviving gtm tools: `query` (guarded SQL, never auto-approved),
`az_login_status`, `azure_login`. Everything else in `agent/tools.py` is
**demoted from tool to code**:

- `pipe_create_targets`, `derive_pipe_create_target` → ordinary modules under
  `pipeline/`, runnable as `python -m pipeline.targets_cli ...` or imported by
  a scratch script. Reviewable code, not frozen interface.
- `list_runs`, `show_run` → the agent reads `workspace/runs/index.jsonl` and
  manifests directly, or calls `agent.lineage.list_runs()` from a script.
- `run_pull` / `list_queries` → **deleted.** The four-query registry was the
  old security model. Warehouse access is now exclusively the `query` tool;
  `pipeline/pull.py` becomes a caching helper the `query` path uses, and cached
  parquet under `data/` is read like any other file.

### Approval friction, tuned by zone

`permission_mode` stays `"default"` — the approval prompt remains the second
access control, per the v1 decision confirmed 2026-08-10. But blanket prompting
on every scratch run would make thinking unbearable, so `.claude/settings.json`
(loaded because `setting_sources=["project"]`) carries allow rules:

```json
{
  "permissions": {
    "allow": [
      "Bash(python workspace/scratch/*)",
      "Bash(python -m pipeline.*)",
      "Bash(python -m pytest*)",
      "Write(workspace/**)",
      "Edit(workspace/**)"
    ]
  }
}
```

Everything else — repo edits, arbitrary Bash, the `query` tool — prompts.
The result: **thinking is free, acting on the world is approved.**

### Write protection — what the agent may never modify

A `PreToolUse` hook (the surviving half of `agent/hooks.py`) denies Write/Edit
to:

| Path | Why |
|---|---|
| `docs/**` and `CLAUDE.md` | The context corpus is the golden asset and it is **human-curated**. An agent that can rewrite its own constitution can drift silently. It proposes changes as diff files in `workspace/proposals/` for the human to review and apply. |
| `data/**` | Inputs are read-only, as in v1. |
| `.env`, `.env.*`, `.claude/settings.json` | Credentials and its own permission rules. Self-widening permissions is the classic failure. |
| `workspace/runs/<existing run>/**` | Run immutability survives — enforced by `lineage.Run` raising on collision, and by the hook refusing writes into any existing run dir. |

Everything else in the repo (`pipeline/`, `agent/`, `tests/`) is editable
**with approval** — that is the "build" half of think-and-build. The agent can
propose and, once approved, apply changes to its own pipeline code.

### Secrets — the boundary made mechanical again

v1's Bash allowlist mechanically prevented `cat .env`. v2's general Bash cannot,
so the boundary moves into process environment:

1. **`main.py` stops calling `load_dotenv()` globally.** It reads
   `dotenv_values(".env")` and exports **only** `ANTHROPIC_API_KEY` (needed by
   the spawned CLI). `SYNAPSE_CONN_STR` never enters `os.environ`, so no Bash
   subprocess — scratch script or otherwise — can inherit it.
2. `pipeline/pull.py` reads `SYNAPSE_CONN_STR` via `dotenv_values` **at call
   time, inside the function**, and never assigns it to `os.environ`.
3. The hook denies any Read/Bash whose path or command string references
   `.env`, `id_rsa`, or `credentials` — crude, but it converts an accident into
   a visible denial with a reason.
4. `.gitignore` already covers `.env`; unchanged.

Net: a scratch script that tries `os.environ["SYNAPSE_CONN_STR"]` gets a
KeyError, not a connection string. That is a real mechanical guarantee, not a
prompt rule.

---

## Verification without golden numbers

**Established 2026-08-11:** no verified golden output figures exist for this
instance of Pipe Create. The $201,789,918 load anchor may be retained *if it
still reproduces*, but the design cannot lean on expected values. What is
golden is the **process** — the docs.

So verification is **process conformance**, in four layers, strongest first:

### 1. Invariant property tests (exist, keep, extend)

The v1 tests already assert *properties*, not magic numbers: month columns are
derived not hardcoded; 14 weeks with W1/W14 partial; unstarted weeks yield 0
target and null attainment; missing teams are `None` not `0.0`; ASP is derived,
never read. These survive unchanged and gain siblings as the actuals path
lands.

### 2. Internal consistency checks (new — `pipeline/checks.py`)

Cheap assertions any output must pass, needing no external truth:

- weekly targets sum to the quarter total at every grain (within $1 rounding)
- day-weight shares sum to 1.0 for every completed week
- Territory rows roll up to their Region, Regions to Geo, Geos to All —
  discrepancies reported, never silently absorbed (invariant 7 caveat applies
  to CSV-hierarchy rollups)
- no negative targets; attainment null exactly where `days_counted == 0`
- actuals: first-seen dedup means an opp counts in exactly one week

`checks.run_all(df)` returns a list of failures. **The operating rules require
it before any figure is reported.**

### 3. Legacy-workbook reconciliation (aggregate-level, caveated)

`data/legacy/Pipeline Creation Quarter Product V20.xlsm` remains the only
artifact showing how these numbers were produced before. Per
`docs/reference/legacy-pipe-create-xlsm.md`: reconcile at aggregate level only,
never join on product name, always state the workbook's formulas are unaudited.
A match is *corroboration*; a mismatch is a **finding to surface**, not an
error to fix.

### 4. Verifier subagent (v1 deferral now expired)

A second `AgentDefinition`, read-only tools, own context window. Given a run's
`manifest.json`, it independently re-derives the headline from the manifest's
inputs — reading the docs itself, writing its own scratch script — and reports
agree/disagree with deltas. Maker and checker never share a context window, so
the checker cannot inherit the maker's mistake.

### The reporting rule that binds it together

Every figure the agent reports states **which layer backed it**:

> "QTD target $X — passes internal consistency (checks.py), reconciles to the
> legacy workbook within 0.3% at Geo level."

or, honestly:

> "This figure is computed but **unverified** — no check covers it."

An unverified number is allowed; an unlabeled one is not. This replaces v1's
citation-only rule with something stronger: cite the doc for *logic*, cite the
check for *numbers*.

---

## Memory and thinking space

| Path | Role | Lifecycle |
|---|---|---|
| `workspace/scratch/` | Ephemeral thinking — scripts written, run, deleted | Agent cleans up when the task ends; anything left is disposable and gitignored |
| `workspace/notes/journal.md` | Persistent memory — findings, dead ends, decisions, open questions | Append-only by convention; read at session start (operating rule); never auto-deleted |
| `workspace/proposals/` | Proposed edits to `docs/` or `CLAUDE.md`, as diffs | Human reviews, applies or rejects, deletes |
| `workspace/exports/` | Deliverables — xlsx, png, csv per `CLAUDE.md` output conventions | Never overwritten; date-suffixed on collision |
| `workspace/runs/` | Immutable lineage — unchanged from v1 | Never modified, never deleted |

The operating rules add: *start every session by reading
`workspace/notes/journal.md` if it exists; before ending a substantial task,
append what was learned.* The scratch/notes split is the deep-agent loop:
**think in scratch, remember in notes, report through lineage.**

---

## Operating rules — the rewritten append

`OPERATING_RULES` in `agent/options.py` is rewritten. Sections that survive
verbatim: ASKING, CAVEATS, DELEGATION, the derived-vs-published target
labeling. Sections replaced:

- **WAREHOUSE** — "You compose SQL through the `query` tool only. It is
  validated read-only and shown to the user for approval — write it to be read.
  Never attempt Synapse access through Bash or a script; the connection string
  is not in your environment and the attempt will be visible. Prefer cached
  parquet in `data/`; state when you use the offline path."
- **COMPUTE (new)** — "When a question needs computation, write a script in
  `workspace/scratch/`, run it, read the result, delete it. Import
  `agent.lineage` and record a Run for any number you intend to report;
  scratch exploration needs no lineage. Run `pipeline.checks` on any output
  before reporting from it."
- **VERIFICATION (new)** — the which-layer-backed-it rule from above, plus:
  "There are no golden output numbers. The docs are the spec; conformance to
  them is what verification means here."
- **MEMORY (new)** — the journal rule from above.
- **SELF-MODIFICATION (new)** — "You may edit `pipeline/`, `agent/`, and
  `tests/` with approval. You may never edit `docs/`, `CLAUDE.md`, or your own
  permission rules — propose those changes in `workspace/proposals/`."

`CLAUDE.md` gains matching edits (human applies them): the `<REPO>` rows in the
path table are deleted (the repo *is* this project now), `workspace/scratch/`
and `workspace/notes/` rows are added, and the "Producing files" section gains
"scratch scripts are ephemeral — delete them when the task ends."

---

## What is deleted, explicitly

| Deleted | Was | Why it goes |
|---|---|---|
| Bash prefix allowlist (`ALLOWED_BASH_PREFIXES`, `SHELL_CHAINING`, `check_bash`) | `agent/hooks.py` | General Bash is the point. Approval mode + secrets isolation replace it. |
| Read confinement (`ALLOWED_READ_ROOTS`) | `agent/hooks.py` | The agent may read broadly; only credentials are denied. `docs/superpowers/` **stays denied** as context. |
| `run_pull`, `list_queries`, `pipe_create_targets`, `derive_pipe_create_target`, `list_runs`, `show_run` as tools | `agent/tools.py` | Demoted to modules/CLI per above. Logic is preserved; the frozen interface is not. |
| The four-query registry as a security boundary | `pipeline/queries.py` | The `query` tool + sqlguard + approval is the boundary. The registry may survive as convenience templates in docs. |
| **Test 16** (no tool accepts free-text SQL) | `tests/` | Already obsolete since the `query` tool landed; retired *deliberately* here so its absence is a decision, not an accident. |
| Bash-allowlist tests, read-confinement tests | `tests/test_hooks.py` | Their subject no longer exists. Replaced, not just removed — see Testing. |

---

## Testing

Test-first, per `superpowers:test-driven-development`. No fixtures for model
data — unchanged from v1.

**Survive unchanged:** `test_lineage.py` (all), `test_sqlguard.py` (all),
`test_targets.py` invariant properties (9–14, with the $201.8M anchor kept
only if it reproduces on current data — demote to a warning if the CSV has
been reissued).

**Updated — `test_options.py`:**

1. `Write`, `Edit`, `Bash`, `TodoWrite` are in `allowed_tools`.
2. `WebSearch`/`WebFetch` remain in `disallowed_tools`.
3. `system_prompt` is still the `claude_code` preset dict — the loading gotcha
   regression test survives every redesign.
4. Exactly three `mcp__gtm__` tools are exposed: `query`, `az_login_status`,
   `azure_login`. (The spiritual successor to test 16: the tool surface is
   asserted, so widening it is a test failure, not a drift.)

**New — `test_hooks.py` (rewritten):**

5. Write to `docs/models/pipe-create.md` → denied with the propose-instead
   reason. Write to `CLAUDE.md` → denied. Write to `.claude/settings.json` →
   denied.
6. Write to `workspace/scratch/x.py` → allowed. Edit of `pipeline/config.py`
   → allowed (approval is the runtime control, not the hook).
7. Write into an existing `workspace/runs/<id>/` → denied.
8. Read of `.env` → denied; Bash command containing `.env` → denied.

**New — `test_env_isolation.py`:**

9. After `main.py` startup logic runs, `SYNAPSE_CONN_STR` is **not** in
   `os.environ`. The single most important new test in v2.
10. `pull` obtains the connection string via `dotenv_values` and does not
    export it.

**New — `test_checks.py`:**

11. A frame whose weeks don't sum to the quarter total fails `checks.run_all`.
12. Shares summing to 1.0 pass; a 0.98 week fails with the week named.
13. A rollup discrepancy is reported with grain and delta, not raised as a
    bare assert.

**Integration — one live call:**

14. Ask a question requiring computation not covered by any module (e.g. "what
    share of the Q3 target falls in August, by Geo?") and assert: a scratch
    script was written under `workspace/scratch/`, the answer cites both a doc
    path and a check, and the scratch file is gone at turn end.

---

## Honest trade-offs — read before building

1. **The SQL boundary is now procedural, not mathematical.** In v1, "the agent
   cannot express SQL" was a fact of the interface. In v2, an agent with
   general Python *could* attempt warehouse access outside the `query` tool.
   What stops it: the connection string is mechanically absent from its
   environment (the real control), the approval prompt covers non-scratch
   Bash, and the operating rules forbid it. This is the same trust model
   Claude Code runs under all day on this same machine, accepted deliberately
   for a single-operator local setup. It is still a downgrade in guarantee
   from v1, and this paragraph exists so nobody later pretends otherwise.
2. **Invariants can now be violated by fresh code.** In v1 the invariants
   lived in code written once and tested. In v2 every scratch script is new
   code. The counterweights are `pipeline/checks.py`, the property tests, the
   verifier subagent, and the which-check-backed-it reporting rule. If wrong
   numbers start appearing, the fix is a new check, not a new cage.
3. **Cost.** A thinking agent runs more tokens than a tool-calling one —
   scratch iterations, verifier passes, doc reads. Acceptable, but watch it;
   the doc-retrieval subagent exists precisely to keep the main window lean,
   and it matters more in v2, not less.
4. **The web UI spec** (`2026-08-10-gtm-chat-ui-design.md`) assumed the v1
   tool surface and the four-query permission relay. Its permission-relay
   *mechanism* survives; its card assumptions need revision after v2 lands.
   Do not build it first.

---

## Build order

1. **Secrets isolation** — `main.py` selective env, `pull.py` call-time read,
   env-isolation tests. Land this before any capability widens.
2. **Hooks rewrite** — write-protection + credential denial; delete allowlist
   and read confinement; rewrite `test_hooks.py`.
3. **Options rewrite** — tool lists, settings allow rules, OPERATING_RULES
   rewrite; update `test_options.py`.
4. **Tool demotion** — move `pipe_create_targets` / waterfall logic to
   `pipeline/` CLI modules; shrink `agent/tools.py` to three tools; delete
   `run_pull`/`list_queries`.
5. **`pipeline/checks.py`** + tests.
6. **Verifier subagent** definition + a manual acceptance pass on one run.
7. **Workspace scaffolding** — `scratch/`, `notes/`, `proposals/` dirs,
   `.gitignore` entries, `CLAUDE.md` edits (human applies).
8. **Integration test 14**, then a week of real use before touching the UI.

Each step lands green before the next starts. Steps 1–2 are the security
migration and are not skippable or reorderable.
