"""PreToolUse guards — the things no approval should be able to grant.

v1's hook was a cage: reads confined to project subdirs, Bash limited to an `az`
prefix allowlist. v2 deletes both. Generic capability is the point — the agent
writes and runs its own scripts — and the controls move elsewhere:

  - approval mode gates Bash and repo edits at runtime (`.claude/settings.json`
    decides what runs unprompted)
  - the connection string is absent from os.environ, so no subprocess inherits
    it (see tests/test_env_isolation.py — that is the real warehouse control)

What is left here is the short list that no approval prompt should be able to
authorise, because a human clicking "allow" in the middle of a task is not a
considered decision about the agent's constitution:

  WRITE-DENIED   docs/**, CLAUDE.md   the context corpus is human-curated
                 .claude/settings.json  it must not widen its own permissions
                 .env*                  credentials
                 data/**                inputs are read-only
                 workspace/runs/<existing>/**   lineage is immutable

  READ-DENIED    credential filenames
                 docs/superpowers/     decision history, NOT context

Everything else — pipeline/, agent/, tests/, the rest of workspace/ — is
editable with approval. That is the "build" half of think-and-build.

The credential rule applies to Bash command STRINGS as well as Read paths. It is
crude and an agent determined to defeat it could; it exists to turn an accident
into a visible denial, not to stop an adversary. The mechanical control is the
environment isolation.
"""
from __future__ import annotations

from pathlib import Path

from pipeline import config

# Filenames that are never read and never written, wherever they appear.
CREDENTIAL_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", ".netrc"}
CREDENTIAL_PREFIXES = (".env.",)          # .env.local, .env.production, ...

# Design specs record decisions, including rejected ones. Citing them as fact
# about the data is exactly the failure mode docs/README.md warns about. This
# denial outlives the read confinement that used to surround it.
DENIED_READ_SUBPATHS = (config.ROOT / "docs" / "superpowers",)


def _deny(reason: str):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _resolve(p: str) -> Path:
    # Resolve before comparing, or `docs/../.env` slips past a name check.
    return (config.ROOT / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()


def _is_credential(name: str) -> bool:
    return name in CREDENTIAL_NAMES or name.startswith(CREDENTIAL_PREFIXES)


def _under(target: Path, root: Path) -> bool:
    return target == root or root in target.parents


def check_write(path: str):
    """Deny dict if this path may not be written or edited, else None."""
    if not path:
        return None
    target = _resolve(path)

    if _is_credential(target.name):
        return _deny(f"Refused: {target.name} holds credentials and is never writable.")

    if target == (config.ROOT / ".claude" / "settings.json").resolve():
        return _deny(
            "Refused: .claude/settings.json holds your own permission rules. An agent "
            "that can widen its permissions has none. Ask the operator to change it."
        )

    if _under(target, (config.ROOT / "docs").resolve()) or target == (config.ROOT / "CLAUDE.md").resolve():
        rel = target.name
        return _deny(
            f"Refused: {rel} is part of the human-curated context corpus. You reason FROM "
            f"these files, so rewriting them lets you drift with no trace. Write your "
            f"change as a diff in workspace/proposals/ and tell the operator to review it."
        )

    if _under(target, (config.ROOT / "data").resolve()):
        return _deny("Refused: data/ is read-only input. Write outputs to workspace/exports/.")

    # Immutability, but only for runs that ALREADY exist — a path under a run id
    # that has not been created yet must not be pre-emptively denied, or the
    # guard would depend on the order operations happen in. lineage.Run writes
    # through Python file I/O and never reaches this hook at all.
    runs = Path(config.RUNS).resolve()
    if _under(target, runs) and target != runs:
        rel = target.relative_to(runs)
        if rel.parts:
            run_dir = runs / rel.parts[0]
            if run_dir.exists():
                return _deny(
                    f"Refused: {rel.parts[0]} is a completed run and lineage is immutable — "
                    f"a run that can be edited afterwards cannot defend a number. "
                    f"Record a NEW run with agent.lineage.Run instead."
                )
    return None


def check_read(path: str):
    """Deny dict if this path may not be read, else None."""
    if not path:
        return None
    target = _resolve(path)

    if _is_credential(target.name):
        return _deny(f"Refused: {target.name} holds credentials and is never readable.")

    for root in DENIED_READ_SUBPATHS:
        if _under(target, root.resolve()):
            return _deny(
                "Refused: docs/superpowers/ holds design specs and decision history, not "
                "context. It must never be cited as fact about the data or the models. "
                "Use docs/README.md to find the right context file."
            )
    return None


def check_bash(command: str):
    """Deny dict if this command reaches for a credential, else None.

    No allowlist. v1 denied shell chaining because `&&` defeated prefix matching;
    with no prefixes to match there is nothing for chaining to defeat, so a
    pipeline is just a shell command. The credential check runs against the whole
    string, so chaining cannot smuggle one past it either.
    """
    cmd = command or ""
    if not cmd.strip():
        return None
    for name in sorted(CREDENTIAL_NAMES) + list(CREDENTIAL_PREFIXES):
        if name in cmd:
            return _deny(
                f"Refused: this command references {name!r}. Credentials are not readable, "
                f"and the Synapse connection string is not in your environment — use the "
                f"`query` tool for warehouse access."
            )
    return None


async def pre_tool_use(input_data, tool_use_id, context):
    """PreToolUse hook. Routes to the right guard by tool name."""
    tool = input_data.get("tool_name", "")
    args = input_data.get("tool_input", {}) or {}

    if tool in ("Write", "Edit", "NotebookEdit"):
        denied = check_write(args.get("file_path") or args.get("notebook_path") or "")
    elif tool in ("Read", "Glob", "Grep"):
        denied = check_read(args.get("file_path") or args.get("path") or "")
    elif tool == "Bash":
        denied = check_bash(args.get("command", ""))
    else:
        denied = None
    return denied or {}
