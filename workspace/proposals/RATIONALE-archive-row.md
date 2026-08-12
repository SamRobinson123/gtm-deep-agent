# Rationale — `claude-md-archive-row.patch`

`CLAUDE.md` is human-curated, so this row goes through a proposal. Apply with
`git apply workspace/proposals/claude-md-archive-row.patch`, then delete both files.

Six dashboard-era docs were archived to `archive/docs/` on 2026-08-12 and
`agent/hooks.py` now read- and write-denies `archive/**` — but `CLAUDE.md`'s
"Where things live" table, the agent's first map of the repo, does not mention
the directory at all. One row beside the `docs/superpowers/specs/` exclusion
tells the agent what the denial means before it ever hits it.
