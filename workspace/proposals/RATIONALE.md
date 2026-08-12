# One patch awaiting review — v2 step 7

`CLAUDE.md` is part of the human-curated corpus, so the agent cannot apply this
itself (`agent/hooks.py` denies the write and points here). Apply with:

    git apply workspace/proposals/claude-md-v2.patch

Then delete the patch file.

> **`agent-architecture-v2.patch` is gone: APPLIED 2026-08-11** by the operator,
> who reviewed the content and had it written to `docs/agent-architecture.md`
> directly. The doc now describes the v2 model — three tools, the six
> boundaries, and the scratch/notes/lineage loop.

---

## Why these are needed before testing the agent

The v2 migration changed the capability model, and **both documents currently
describe v1**. An agent reading them is told it has tools that were deleted in
step 4 and is told nothing about what replaced them. Measured after step 5:

- 5 files reference deleted tools (`slip_analysis`, `export_excel`, `run_pull`,
  `list_runs`, …)
- **0 files** mention `python -m pipeline.*_cli`, `checks.run_all`,
  `workspace/scratch/` or `workspace/notes/`

The operating rules in `agent/options.py` do point at scratch, checks and the
journal, so the agent is not blind — but the docs actively contradict them, and
`docs/README.md` routes to `agent-architecture.md` as the authority on what the
agent can do. Contradiction between the routed doc and the system prompt is the
worst of the available states.

---

## `claude-md-v2.patch`

| Change | Why |
|---|---|
| `workspace/scratch/`, `workspace/notes/`, `workspace/proposals/` rows added to the path table | The three directories the v2 loop runs on. Absent entirely today. |
| `workspace/exports/` row no longer credits `export_excel` / `export_chart` | Those tools no longer exist. The boundary is `agent/exports.py`. |
| `workspace/runs/` row points at `index.jsonl` / `lineage.list_runs()` | `list_runs` / `show_run` were deleted as tools. |
| `pipeline/` row lists `checks.py` and the four CLIs | Nothing currently tells the agent they exist. |
| "Producing files" names `python -m pipeline.export_cli` | Same; and adds "scratch scripts are ephemeral — delete them when the task ends", per the spec. |
| "Ask before running anything that writes into `<REPO>/output/`" replaced | `<REPO>` no longer exists as a separate thing — the repo *is* this project. Replaced with the compute-and-check rule. |

The `<REPO>` path-table rows the spec asks to delete were already removed
earlier on 2026-08-11; this is the last surviving `<REPO>` reference.

## `agent-architecture-v2.patch` — APPLIED, kept as a record of what changed

That document is *about* the capability model that changed, so it needed the
larger edit.

| Change | Why |
|---|---|
| Tool table: 11 rows → 3, plus a new table of `pipeline/` modules | Step 4. The doc currently advertises eleven tools that are gone. |
| "`Write` and `Edit` are disallowed" → the full thinking tool set | Step 3 inverted this. Stated the old way, it tells the agent not to do the thing v2 exists for. |
| Boundary 4 rewritten: read confinement and the Bash allowlist are **deleted** | Step 2. Replaced with write-protection + credential denial. |
| New boundary 5: the environment | The real warehouse control in v2, and it is documented nowhere. |
| New boundary 6: `.claude/settings.json` | Where the friction falls — thinking free, acting approved. |
| New "Verification" section | "No golden numbers" is a v2 decision the corpus does not record anywhere a reader would find it. |

---

## One thing to check when reviewing

`agent-architecture.md` is routed from `docs/README.md` as the answer to "what
the agent can do". If you would rather it stayed a *human* orientation document
and the agent got its capability picture only from `OPERATING_RULES`, say so —
the two would then need a stated division of labour, because right now they
overlap and one of them is wrong.
