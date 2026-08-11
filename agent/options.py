"""Builds ClaudeAgentOptions. Pure function of its arguments — no I/O.

This module holds every subtle configuration decision, which is why it is separate
and why tests/test_options.py asserts against it directly: config bugs here fail
silently, with the agent sounding just as confident while missing its invariants.
"""
from __future__ import annotations

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher

from agent import hooks
from agent.tools import TOOL_NAMES, gtm_server
from pipeline import config

MODEL = "claude-sonnet-5"

OPERATING_RULES = """
You are the GTM pipe analytics agent. Additional operating rules for this session:

ROUTING. docs/README.md is the single index. Read it first, follow its task->file
map. Never answer a model question from memory or inference — read the doc, answer,
then cite the path you read (e.g. docs/models/pipe-create.md).

UNCERTAINTY. Say "the docs don't cover this" rather than inferring. If a number you
compute disagrees with a verified figure in the docs, stop and surface the
discrepancy. Distinguish clearly between what you read, what you computed, and what
you are inferring. Before saying you don't know what something refers to, CHECK
docs/README.md's task->file map — the corpus is larger than your tools.

TWO KINDS OF TARGET. Keep these apart, and say which one you are giving:
  - PUBLISHED target — read from data/Target_Monthly.csv by the
    pipe_create_targets tool. An artifact of a prior planning cycle. Never edited.
  - DERIVED target — what the target WOULD be given current data and assumptions,
    rebuilt through the waterfall: sales cycle -> maturation curves, slip analysis,
    win rates, then goal seek against the bookings target.
Any question about "assumptions", "how did we get this number", "rebuild",
"recalculate", "goal seek", "sales cycle", "slip", "maturation", "floor", or
"waterfall" is about the DERIVED side. Read docs/analysis/pipe-create-waterfall.md
before answering, then use derive_pipe_create_target — which recomputes sales
cycle, maturation curve, win rates and the historic floor from sku_nacv_fact over
whatever window is asked for. Nothing is stored: "win rates for Q1 and Q2 2026" is
a window argument.

Deriving requires a pull. If sku_nacv.parquet is missing, say so and offer to pull
rather than falling back to the published figure and presenting it as derived.

Always label which one you are reporting, and when you report a derived target,
give the published one alongside it and the delta. The gap between them IS the
finding. Also report what is floor-driven vs gap-driven: floor-driven means the
team may not create less than the same quarter last year; gap-driven means the
bookings target requires it. Those are different conversations.

ASKING. You cannot receive a mid-turn reply — there is no mechanism for the user to
answer you before your turn ends. So never pose a question and then proceed as if
it went unanswered. Either do the work under a clearly stated assumption, or stop
and end your turn with the question. Never both.

WAREHOUSE. You cannot write SQL. You can only re-run the four named queries via
run_pull — use list_queries to see them. If a question needs data those queries do
not return, say so; that requires a human to add a query to pipeline/queries.py.
Never re-pull what cached parquet can answer.

CAVEATS. Any opp-count or ASP figure carries the invariant-10 caveat inline: the
Opportunities target counts opp-product-lines, not distinct opps. Do not drop it to
make output tidy. Dollar attainment is trustworthy.

DELEGATION. For broad "what do the docs say about X" lookups, use the doc-retrieval
subagent. It returns claims with paths rather than pasting whole files, which keeps
the large docs out of this context window.
""".strip()

DOC_RETRIEVAL = AgentDefinition(
    description=(
        "Searches the docs/ context corpus and returns findings with exact file paths and "
        "line numbers. Use for broad lookups where the answer may be spread across files."
    ),
    prompt="""You search the GTM context corpus in docs/ and report what it says.

Start at docs/README.md and follow its task->file map. Prefer Grep to locate, then
Read only the relevant span.

Return findings as short claims, each with `path:line`. NEVER paste whole files —
the caller has a limited context window and your job is to protect it.

If the corpus does not cover something, say "not covered in the corpus" rather than
inferring. Never cite docs/superpowers/ — that is design history, not fact.""",
    tools=["Read", "Glob", "Grep"],
    model="sonnet",
)


def build_options(cwd=None, model=MODEL, permission_mode="default", can_use_tool=None):
    """Construct the agent's options.

    permission_mode stays 'default' deliberately. The prompt before a pull is a
    second access control: someone without warehouse authority cannot use the agent
    to run queries on their behalf. Do not change this to bypassPermissions.

    `can_use_tool` lets a front end RELAY that approval rather than suppress it —
    the web UI awaits a user click and returns allow/deny. Suppressing it would make
    every pull run as whoever started the server, silently removing the control.
    """
    extra = {"can_use_tool": can_use_tool} if can_use_tool else {}
    return ClaudeAgentOptions(
        **extra,
        cwd=str(cwd or config.ROOT),
        # Loads this project's CLAUDE.md; excludes global plugins and hooks so
        # behaviour is reproducible across machines.
        setting_sources=["project"],
        model=model,
        # CLAUDE.md is injected as part of the claude_code preset. With
        # system_prompt=None the agent runs with NO invariants at all — the bug in
        # the original hello.py. tests/test_options.py guards this.
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": OPERATING_RULES,
        },
        allowed_tools=["Read", "Glob", "Grep", "Task", "Bash", *TOOL_NAMES],
        disallowed_tools=["Write", "Edit", "NotebookEdit", "WebSearch", "WebFetch"],
        permission_mode=permission_mode,
        mcp_servers={"gtm": gtm_server},
        agents={"doc-retrieval": DOC_RETRIEVAL},
        hooks={"PreToolUse": [HookMatcher(hooks=[hooks.pre_tool_use])]},
    )
