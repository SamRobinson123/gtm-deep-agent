"""PreToolUse guards — read scope and the Bash allowlist.

These make two CLAUDE.md rules mechanical rather than a matter of the agent
remembering them mid-session:
  - answers come from docs/, so reads outside it are denied
  - shell is not the interface for data access; the scoped tools are
"""
from __future__ import annotations

from pathlib import Path

from pipeline import config

# Reads are allowed only inside these. data/ is included because the model must
# load parquet and the targets CSV — but see DENIED_NAMES: it can never read .env.
ALLOWED_READ_ROOTS = (
    config.ROOT / "docs",
    config.ROOT / "data",
    config.ROOT / "workspace",
    config.ROOT / "pipeline",
    config.ROOT / "agent",
    config.ROOT / "tests",
)

# Design specs record decisions, including ones that were rejected. Citing them as
# fact about the data is exactly the failure mode docs/README.md warns about.
DENIED_READ_SUBPATHS = (config.ROOT / "docs" / "superpowers",)

DENIED_NAMES = {".env", ".env.local", "id_rsa", "credentials"}

# Only Azure CLI session commands. Everything else goes through the scoped tools.
ALLOWED_BASH_PREFIXES = (
    "az account show",
    "az account get-access-token",
    "az login",
    "az logout",
    "az --version",
)


def _deny(reason: str):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _resolve(p: str) -> Path:
    # Resolve before comparing, or `docs/../../etc/passwd` walks straight out.
    return (config.ROOT / p).resolve() if not Path(p).is_absolute() else Path(p).resolve()


def check_read_scope(path: str):
    """Return a deny dict if this path may not be read, else None."""
    if not path:
        return None
    target = _resolve(path)

    if target.name in DENIED_NAMES:
        return _deny(f"Refused: {target.name} holds credentials and is never readable.")

    for denied in DENIED_READ_SUBPATHS:
        if denied == target or denied in target.parents:
            return _deny(
                "Refused: docs/superpowers/ holds design specs and decision history, not context. "
                "It must never be cited as fact about the data or the models. Use docs/README.md "
                "to find the right context file."
            )

    for root in ALLOWED_READ_ROOTS:
        if root == target or root in target.parents:
            return None

    return _deny(
        f"Refused: {target} is outside the project. Answers must come from docs/ — "
        "start at docs/README.md, which routes to the right file."
    )


# Shell metacharacters that chain, redirect, or substitute another command.
# Without this check, prefix-matching alone is defeated by `az account show && cat .env`
# — the command starts with an allowed prefix and smuggles a second one after it.
SHELL_CHAINING = (";", "&&", "||", "|", "`", "$(", ">", "<", "&", "\n")


def check_bash(command: str):
    """Return a deny dict if this shell command is not allowed, else None."""
    cmd = (command or "").strip()
    for meta in SHELL_CHAINING:
        if meta in cmd:
            return _deny(
                f"Refused: shell chaining/redirection ({meta!r}) is not permitted. "
                "Run a single Azure CLI command, or use the gtm tools for data access."
            )
    if any(cmd == p or cmd.startswith(p + " ") for p in ALLOWED_BASH_PREFIXES):
        return None
    return _deny(
        "Refused: shell is limited to Azure CLI session commands "
        f"({', '.join(ALLOWED_BASH_PREFIXES)}). Data access goes through the gtm tools — "
        "use list_queries to see what can be run."
    )


async def pre_tool_use(input_data, tool_use_id, context):
    """PreToolUse hook. Routes to the right guard by tool name."""
    tool = input_data.get("tool_name", "")
    args = input_data.get("tool_input", {}) or {}

    if tool in ("Read", "Glob", "Grep"):
        path = args.get("file_path") or args.get("path") or ""
        if path:
            denied = check_read_scope(path)
            if denied:
                return denied
    elif tool == "Bash":
        denied = check_bash(args.get("command", ""))
        if denied:
            return denied
    return {}
