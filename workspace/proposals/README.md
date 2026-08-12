# proposals — changes the agent may not apply itself

`docs/` and `CLAUDE.md` are the human-curated context corpus. The agent reasons
FROM them, so an agent that can rewrite them can drift with no trace — the hook
in `agent/hooks.py` denies the write and points here instead.

A proposal is a diff plus a short rationale. A human reviews it, applies or
rejects it, and deletes the file.
