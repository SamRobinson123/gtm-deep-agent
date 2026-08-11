"""Entry point for the GTM deep agent.

    python main.py                 interactive chat loop
    python main.py -q "question"   one-shot
"""
from __future__ import annotations

import argparse
import os
import sys

import anyio
from dotenv import load_dotenv

load_dotenv()


def preflight():
    """Fail at startup with a clear message rather than mid-conversation."""
    problems = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        problems.append("ANTHROPIC_API_KEY is not set — add it to .env")
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

    for p in preflight():
        print(f"[startup] {p}", file=sys.stderr)
        if "ANTHROPIC_API_KEY" in p:
            sys.exit(1)

    if args.question:
        anyio.run(lambda: one_shot(args.question, args.model))
    else:
        from agent.loop import run
        anyio.run(lambda: run(args.model))


if __name__ == "__main__":
    cli()
