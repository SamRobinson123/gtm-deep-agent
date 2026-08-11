"""The REPL — a persistent conversation, not a sequence of one-shots.

ClaudeSDKClient holds one session across turns; query() would discard state between
questions, which makes follow-ups ("break that down by Geo") impossible.
"""
from __future__ import annotations

import sys

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from agent.options import build_options

BANNER = """GTM Pipe Analytics Agent
Context: docs/  |  Targets: data/Target_Monthly.csv  |  Runs: workspace/runs/
Commands: /exit  /new (fresh session)  /help
"""

HELP = """Try:
  Why does Pipe Create have no CloseDate filter?
  What's the Q3 FY26 pipe create target?
  Show me weekly targets for EMEA Core Germany
  Is my az login live?
  What queries can you run?
  List previous runs
"""


async def _stream(client):
    """Print one response, surfacing tool calls so the user sees what it's doing."""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
                elif isinstance(block, ToolUseBlock):
                    print(f"\n  [{block.name}]", flush=True)
        elif isinstance(message, ResultMessage):
            cost = getattr(message, "total_cost_usd", None)
            print(f"\n{'-' * 60}" + (f"  ${cost:.4f}" if cost else ""), flush=True)


async def run(model=None):
    opts = build_options(**({"model": model} if model else {}))
    print(BANNER)
    while True:
        async with ClaudeSDKClient(options=opts) as client:
            restart = False
            while True:
                try:
                    q = input("\n> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nbye")
                    return
                if not q:
                    continue
                if q in ("/exit", "/quit"):
                    print("bye")
                    return
                if q == "/help":
                    print(HELP)
                    continue
                if q == "/new":
                    print("[new session]")
                    restart = True
                    break
                try:
                    await client.query(q)
                    await _stream(client)
                except KeyboardInterrupt:
                    # Cancel the turn, keep the session.
                    print("\n[interrupted]")
                except Exception as e:
                    print(f"\n[error] {type(e).__name__}: {e}", file=sys.stderr)
            if not restart:
                return
