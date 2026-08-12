"""Spec test 14 — the one live integration test. Spends tokens; run with -m live.

Asks the real agent a question no module answers directly and asserts the v2
loop actually happened: it computed in scratch, it cited both a doc (logic) and
a check (numbers), and scratch is clean afterwards. This is the whole redesign
exercised once, end to end, against real data.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live

QUESTION = (
    "What share of the Q3 FY26 pipe create target falls in August, by Geo? "
    "Published target, not derived."
)


@pytest.fixture(scope="module")
def transcript():
    """One live turn, shared by every assertion — tokens are spent once."""
    import anyio

    import main
    from agent.options import build_options
    from claude_agent_sdk import (AssistantMessage, ClaudeSDKClient, TextBlock,
                                  ToolUseBlock)
    from pipeline import config

    main.load_secrets()
    scratch = config.ROOT / "workspace" / "scratch"
    before = {p.name for p in scratch.glob("*")}

    text, tool_uses = [], []

    async def go():
        async with ClaudeSDKClient(options=build_options()) as client:
            await client.query(QUESTION)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            text.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append((block.name, str(block.input)))

    anyio.run(go)
    after = {p.name for p in scratch.glob("*")}
    # -s shows this: the transcript IS the acceptance artifact.
    print()
    print("=" * 72)
    print("LIVE ANSWER:")
    print("".join(text))
    print("=" * 72)
    return {"answer": "".join(text), "tools": tool_uses,
            "scratch_before": before, "scratch_after": after}


def test_it_computed_in_scratch(transcript):
    """The COMPUTE rule in action: a script under workspace/scratch/, not a
    hand-waved figure and not a frozen tool."""
    touched = [(n, i) for n, i in transcript["tools"]
               if "workspace/scratch" in i.replace("\\\\", "/").replace("\\", "/")]
    assert touched, (
        f"no tool call referenced workspace/scratch — tools used: "
        f"{[n for n, _ in transcript['tools']]}")


def test_the_answer_cites_a_doc_and_a_check(transcript):
    """Cite the doc for logic, label the number with its verification layer.

    The VERIFICATION rule names three valid labels — a checks.py pass, a
    reconciliation, or an honest "unverified". The first version of this
    assertion only accepted the word "check" and failed a live answer that said
    "reconciles to the $201,789,918 regression figure ... which is what
    verifies this read" — a correct layer-3 label. The test was wrong, not the
    agent; all four labels' vocabulary is accepted now."""
    a = transcript["answer"].lower()
    assert "docs/" in a, "no doc path cited"
    assert any(w in a for w in ("check", "unverified", "reconcil", "verif")), (
        "the answer neither claims a verification layer nor admits to being "
        "unverified")


def test_scratch_is_clean_at_turn_end(transcript):
    """Scratch scripts are ephemeral — delete them when the task ends."""
    leftovers = transcript["scratch_after"] - transcript["scratch_before"]
    assert not leftovers, f"scratch not cleaned up: {sorted(leftovers)}"


def test_the_answer_actually_answers_by_geo(transcript):
    """August share by Geo means Geo names and percentages, not a total."""
    a = transcript["answer"]
    assert "%" in a
    named = [g for g in ("AMS", "EMEA", "APAC") if g in a]
    assert len(named) >= 3, f"expected Geo breakdown, saw only {named}"
