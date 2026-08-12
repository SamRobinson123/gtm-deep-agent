"""Entry point for the GTM deep agent.

    python main.py                 interactive chat loop
    python main.py -q "question"   one-shot
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
DEFAULT_ENV = ROOT / ".env"

# The ONLY variables that reach os.environ, and therefore the only ones any
# subprocess can inherit. Each entry needs a reason recorded here.
#
#   ANTHROPIC_API_KEY — read by the Claude Code CLI, which the SDK spawns as a
#   CHILD PROCESS. It cannot be passed in-band; the environment is the interface.
#
# SYNAPSE_CONN_STR is deliberately absent. v1 stopped `cat .env` with a Bash
# prefix allowlist; v2 gives the agent general Bash, so that lever is gone and
# the boundary lives here instead. Nothing the agent spawns can inherit a
# variable that was never exported — a scratch script reaching for it gets a
# KeyError. pipeline/pull.py reads it from the file at call time.
#
# If the spawned CLI ever needs another variable, add it here WITH a reason.
# Never restore a blanket load_dotenv(): that re-opens the hole silently.
EXPORTED_TO_CLI = ("ANTHROPIC_API_KEY",)


def load_secrets(env_path=None) -> list[str]:
    """Export only EXPORTED_TO_CLI from the .env. Returns the NAMES exported.

    Names, never values — the return lands in logs and error paths, so it has to
    be safe to print.
    """
    values = dotenv_values(env_path or DEFAULT_ENV)
    exported = []
    for name in EXPORTED_TO_CLI:
        v = values.get(name)
        if v:                       # an empty key authenticates AS empty and
            os.environ[name] = v    # 401s, rather than falling back to claude.ai
            exported.append(name)
    return exported


def preflight():
    """Fail at startup with a clear message rather than mid-conversation."""
    problems = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Not fatal. The SDK spawns the Claude Code CLI, which falls back to a
        # claude.ai (Pro/Max) login when no key is set. A key, when present, always
        # takes precedence — so an exhausted key SHADOWS working subscription auth.
        problems.append(
            "ANTHROPIC_API_KEY not set — using your claude.ai login (Pro/Max) instead. "
            "Set it in .env to bill the API instead."
        )
    from pipeline import config
    if not config.TARGET_MONTHLY_CSV.exists():
        problems.append(f"missing {config.TARGET_MONTHLY_CSV} — target figures will fail")
    if not (config.ROOT / "docs" / "README.md").exists():
        problems.append("missing docs/README.md — the agent has no context index")
    return problems


async def one_shot(question, model=None):
    from claude_agent_sdk import AssistantMessage, ClaudeSDKClient, TextBlock
    from agent.options import build_options

    opts = build_options(**({"model": model} if model else {}))
    async with ClaudeSDKClient(options=opts) as client:
        await client.query(question)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text, end="", flush=True)
    print()


def cli():
    ap = argparse.ArgumentParser(description="GTM pipe analytics agent")
    ap.add_argument("-q", "--question", help="ask one question and exit")
    ap.add_argument("--model", help="override the model id")
    args = ap.parse_args()

    load_secrets()
    for p in preflight():
        print(f"[startup] {p}", file=sys.stderr)

    if args.question:
        anyio.run(lambda: one_shot(args.question, args.model))
    else:
        from agent.loop import run
        anyio.run(lambda: run(args.model))


if __name__ == "__main__":
    cli()
