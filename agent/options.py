"""Builds ClaudeAgentOptions. Pure function of its arguments — no I/O.

This module holds every subtle configuration decision, which is why it is separate
and why tests/test_boundary.py asserts against it directly: config bugs here fail
silently, with the agent sounding just as confident while missing its invariants.

OPERATING_RULES is part of the agent's capability surface, not documentation of
it. A rule that says the agent cannot do something is indistinguishable, from the
agent's side, from the tool not existing — which is exactly what happened between
2026-08-10 and 2026-08-11, when the WAREHOUSE section denied a `query` tool that
had already shipped. When a tool is added or a boundary moves, this text changes
in the same commit.
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
  - PUBLISHED target — read from data/Target_Monthly.csv by
    `python -m pipeline.targets_cli`. An artifact of a prior planning cycle.
    Never edited.
  - DERIVED target — what the target WOULD be given current data and assumptions,
    rebuilt through the waterfall: sales cycle curves, slip, In Q / Pre Q win
    rates, then goal seek against the bookings target.
    `python -m pipeline.waterfall_cli derive`, or `from pipeline.derive import
    derive_frame` in a scratch script.
Any question about "assumptions", "how did we get this number", "rebuild",
"recalculate", "goal seek", "sales cycle", "slip", "win rate", "floor", or
"waterfall" is about the DERIVED side. Read docs/analysis/pipe-create-waterfall.md
before answering. Nothing is stored — every assumption is recomputed from
sku_nacv over whatever window is asked for, so "win rates for Q1 and Q2 2026" is
a --window-start/--window-end argument, not a lookup.

`waterfall_cli assumptions` shows the inputs without solving; `waterfall_cli
whatif` re-solves with one replaced; `slip_cli` measures slip directly rather
than quoting docs/analysis/slip.md at it.

Deriving needs data/sku_nacv.parquet. If it is missing, say so and offer to pull
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

WAREHOUSE. You compose SQL through the `query` tool ONLY. It is validated
read-only and shown to the user for approval — write it to be read. Never attempt
Synapse access through Bash or a script: the connection string is not in your
environment, so the attempt cannot succeed, and it will be visible.

Prefer cached parquet in `data/` — read it with pandas in a scratch script like
any other file. State when you use the offline path.

Read `docs/sql/conventions.md` and the relevant `docs/tables/` contract BEFORE
composing. They carry the stage, date, financial-column and geo-join rules, and
ignoring them produces answers that look right and are not. Querying a table with
no `docs/tables/` contract is allowed — it comes back flagged, and you report that
the figure rests on no documented contract.

If the connection fails, call az_login_status, then azure_login — that opens the
browser MFA prompt, and the database scope needs its own sign-in even when a
general `az login` is live.

COMPUTE. When a question needs computation, write a script in
`workspace/scratch/`, run it, read the result, delete it. That is the loop — you
are not limited to what a tool already does.

Import `agent.lineage` and record a Run for any number you intend to REPORT;
scratch exploration needs no lineage. Run `pipeline.checks` on any output before
reporting from it. Reusable logic belongs in `pipeline/` as a module, not in a
scratch script that gets deleted — propose the move when you notice you have
written the same thing twice.

VERIFICATION. There are no golden output numbers for this model. The docs are the
spec; conformance to them is what verification means here.

Every figure you report states WHICH LAYER backed it:
  - "passes internal consistency (checks.py)"
  - "reconciles to the legacy workbook within 0.3% at Geo level"
  - or, honestly, "computed but UNVERIFIED — no check covers it"
An unverified number is allowed. An unlabelled one is not. Cite the doc for
logic; cite the check for numbers.

MEMORY. Start by reading `workspace/notes/journal.md` if it exists — it carries
findings, dead ends and open questions from earlier sessions. Before ending a
substantial task, append what was learned. A dead end recorded is worth as much
as a result.

SELF-MODIFICATION. You may edit `pipeline/`, `agent/` and `tests/` with approval.
You may NEVER edit `docs/`, `CLAUDE.md`, or your own permission rules in
`.claude/settings.json` — those are human-curated, and an agent that rewrites the
context it reasons from can drift with no trace. Propose such changes as a diff
in `workspace/proposals/` and say so.

FILES. Deliverables go to `workspace/exports/` per CLAUDE.md's output
conventions — never overwrite; suffix with the date on collision. Give the full
path, and say which run_id produced the figures.

VOCABULARY. The win rates are IN Q (closed in the same quarter it was created) and
PRE Q (closed in a later quarter than created — pipe that existed before the
quarter it books in). Slip splits the same way, but on TIMING: In Q slip moves out
during the quarter, Pre Q slip moves out before it opens. Never say "later rate" —
that name was retired. These assumptions are the model owner's: report a
discrepancy, never silently adjust one to close a gap.
See docs/analysis/slip.md before quoting any slip figure.

CAVEATS. Any opp-count or ASP figure carries the invariant-10 caveat inline: the
Opportunities target counts opp-product-lines, not distinct opps. Do not drop it to
make output tidy. Dollar attainment is trustworthy.

DELEGATION. Two subagents, each with its own context window.
  - doc-retrieval — broad "what do the docs say about X" lookups. Returns claims
    with paths rather than pasting whole files, which keeps the large corpus out
    of this window.
  - verifier — before reporting a number that matters, hand it a run id. It
    re-derives the headline INDEPENDENTLY from the manifest and reports
    agree/disagree with a delta. It does not see your reasoning, which is the
    only reason its agreement is worth anything. A DISAGREE is a finding to
    surface, not something to argue with.
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


VERIFIER = AgentDefinition(
    description=(
        "Independently re-derives a stored run's headline figure from its manifest "
        "and reports agree/disagree with deltas. Use before reporting a number that "
        "matters. Runs in its own context window, so it cannot inherit the mistake "
        "it is checking for."
    ),
    prompt="""You verify a figure someone else produced. You are not helping them —
you are checking them, and the only way to do that is to work it out YOURSELF.

Given a run id:

1. Read `workspace/runs/<id>/manifest.json`. It names the INPUTS (with hashes),
   the code, the git commit, and the headline figures being claimed.
2. Read the docs for the method — start at `docs/README.md` and follow its
   task->file map. Do NOT read the maker's reasoning; read the specification.
3. Re-derive the headline from those inputs INDEPENDENTLY. Write your own script
   under `workspace/scratch/` (via a heredoc or `python -c`) and run it. Import
   `pipeline/` modules where they are the specification — but if the number you
   are checking came out of a module, prefer working from the underlying data,
   because re-running the same function only proves it is deterministic.
4. Compare, and report.

THE FAILURE MODE YOU EXIST TO PREVENT: reading the maker's output CSV, observing
that it matches itself, and reporting agreement. That is not verification. If you
have not computed a number from source, you have not checked anything — say so.

Report exactly this shape:

    VERDICT: AGREE | DISAGREE | CANNOT VERIFY
    claimed:   <figure from the manifest>
    recomputed:<your figure, or "not computed">
    delta:     <absolute and %; "n/a" if not computed>
    method:    <one line: what you actually ran>
    reading:   <doc paths you used>

A verdict with no delta is unactionable — the size is what says whether this is
float dust or a broken assumption. CANNOT VERIFY is a legitimate and useful
answer: say precisely what was missing (an input file, an undocumented step, a
figure with no method in the corpus). Never guess to produce a verdict.

If you disagree, do not speculate about the cause beyond what you can show. Give
the delta and the step where your working diverges.

You cannot write outside scratch, cannot edit anything, and have no warehouse
access — you check what the maker used, you do not fetch new data.""",
    # Read-only WITH RESPECT TO THE REPO, which is what matters — but Write is
    # required, and the first acceptance run proved it. Given Read/Glob/Grep/Bash
    # only, the verifier reported CANNOT VERIFY because it could RUN a scratch
    # script (`Bash(python workspace/scratch/*)` is allow-listed) but had no way
    # to CREATE one, and `python -c ...` is not allow-listed so it prompts and,
    # unattended, is denied. The spec says it writes its own scratch script; this
    # is what lets it.
    #
    # It is still confined: agent/hooks.py denies writes to docs/, CLAUDE.md,
    # data/, settings.json and finished runs, and settings.json only pre-approves
    # Write(workspace/**). It cannot edit the thing it disagrees with, and it has
    # no warehouse access.
    tools=["Read", "Glob", "Grep", "Bash", "Write"],
    # NO permissionMode override. Tried "bypassPermissions" on 2026-08-12 with the
    # operator's approval, on the theory that the verifier runs unattended and its
    # Bash was dying at an approval prompt nobody was there to answer. It changed
    # nothing: the subagent still reported its toolset as Read/Glob/Grep/Write.
    #
    # Ruled out, all offline: the hook allows the commands it would run; the SDK
    # serialises `tools` verbatim into the initialize request; permissions are not
    # the gate. The CLI is simply not granting Bash to subagents in 0.2.134.
    #
    # Reverted rather than left in place — a security widening that buys nothing
    # is strictly worse than no widening, and a later reader would assume it was
    # load-bearing.
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
        # v2: the full thinking tool set. Write/Edit were disallowed in v1; the
        # agent now writes itself a script in workspace/scratch/, runs it, reads
        # the result and deletes it. Removing these re-cages it.
        #
        # The controls are elsewhere: agent/hooks.py denies writes to docs/,
        # CLAUDE.md, data/, settings.json and finished runs; .claude/settings.json
        # decides what runs without an approval prompt; and the Synapse
        # connection string is absent from os.environ (see main.load_secrets).
        allowed_tools=[
            "Read", "Write", "Edit", "NotebookEdit",
            "Glob", "Grep", "Bash", "Task", "TodoWrite",
            *TOOL_NAMES,
        ],
        # The one capability v2 does NOT widen. A web result has no contract and
        # no lineage; answers come from docs/ and the warehouse.
        disallowed_tools=["WebSearch", "WebFetch"],
        permission_mode=permission_mode,
        mcp_servers={"gtm": gtm_server},
        # Two subagents, each with its OWN context window. doc-retrieval keeps the
        # large corpus out of the main window; verifier keeps the maker's
        # reasoning out of the checker's, which is the only thing that makes the
        # check independent.
        agents={"doc-retrieval": DOC_RETRIEVAL, "verifier": VERIFIER},
        hooks={"PreToolUse": [HookMatcher(hooks=[hooks.pre_tool_use])]},
    )
