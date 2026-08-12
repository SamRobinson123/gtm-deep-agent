> **ARCHIVED — not context.** See `archive/README.md`. Describes the old
> dashboard project, not this repo.

# Loop Engineering — Context

**When to load**: Designing an autonomous loop or scheduled automation — anything where a system prompts the agent on a cadence instead of you prompting it turn-by-turn. Also load when deciding whether a task should be a one-off prompt or a standing loop.
**Requires**: nothing in this pack — this is a tool-agnostic reference.
**Used by**: any task that runs unattended (scheduled pipeline refresh, data-quality triage, PR drafting).
**This is NOT**: a prompt-engineering guide, and not a licence to stop reviewing output. The loop changes the work; it does not remove you from it.

**Source**: Addy Osmani, *Loop Engineering*, June 2026 (`AddyOsmani.com - Loop Engineering.html` in the repo root). Peter Steinberger and Boris Cherny (head of Claude Code, Anthropic) frame the same shift: "I don't prompt Claude anymore. I have loops running that prompt Claude. My job is to write loops."

---

## Core idea

**Loop engineering is replacing yourself as the person who prompts the agent — you design the system that does it instead.**

A loop is a *recursive goal*: you define a purpose and the agent iterates until it is complete. Instead of typing a prompt, reading the reply, and typing the next one, you build a small system that finds the work, hands it out, checks it, records what is done, and decides the next thing — then lets that system poke the agents.

It sits one floor above the *agent harness* (the environment a single agent runs inside). The harness, but on a timer, spawning helpers, feeding itself.

> Be honest about cost. Token usage varies wildly and loops run while you are not watching. Design them, don't just start them.

---

## The five primitives + memory

A loop needs five things and one place to remember state.

| Primitive | Job in the loop | Claude Code form |
|-----------|-----------------|------------------|
| **Automations** | discovery + triage on a schedule — the heartbeat | `/loop`, `/goal`, scheduled/cron tasks, lifecycle hooks, GitHub Actions |
| **Worktrees** | isolate parallel agents so they don't collide | `git worktree`, `--worktree`, `isolation: worktree` on a subagent |
| **Skills** | codify project knowledge so intent isn't re-guessed every run | Agent Skills (`SKILL.md`), invoked by name or implicitly |
| **Plugins / connectors** | let the loop act in your real tools | MCP servers + plugins for distribution |
| **Sub-agents** | separate the *maker* from the *checker* | Task subagents in `.claude/agents/`, agent teams |
| **Memory / state** | track what's done and what's next, outside the conversation | Markdown progress file (`AGENTS.md`, state files) or Linear via MCP |

> The memory is the one that looks too dumb to matter. It isn't. The model forgets everything between runs, so state must live **on disk, not in the context**. The agent forgets; the repo doesn't.

---

## Notes on each piece

**Automations — the heartbeat.** What makes a loop a loop and not one run you did once. Define an autonomous task, give it a cadence, and let findings come to you. `/loop` re-runs on an interval; `/goal` keeps going until a condition you wrote is actually true — and a *separate* small model checks "are we done?" after each turn, so the agent that wrote the code isn't the one grading it. Prefer having an automation call a **skill** over pasting a wall of instructions into a schedule nobody will maintain.

**Worktrees — parallel without chaos.** The moment more than one agent runs, files start colliding — same headache as two engineers editing the same lines with no coordination. A git worktree is a separate working directory on its own branch sharing the repo history, so one agent's edits can't touch another's checkout. The worktree removes the *mechanical* collision; **your review bandwidth is still the ceiling** on how many you can run.

**Skills — stop re-explaining the project.** A skill is intent written down on the outside: the conventions, the build steps, the "we don't do it this way because of that one incident." Without skills the loop re-derives your whole project from zero every cycle; with skills it compounds. A tight, boring description beats a clever one, because that's what triggers implicit invocation. (The skill is the *authoring* format; a **plugin** is how you *ship* it across repos.)

**Plugins / connectors — touch real tools.** A loop that can only see the filesystem is a tiny loop. Connectors (built on MCP) let it read the issue tracker, query a DB, hit a staging API, post to Slack. This is the difference between an agent that says "here is the fix" and a loop that opens the PR, links the ticket, and pings the channel once CI is green.

**Sub-agents — keep the maker away from the checker.** The single most useful structural move. The model that wrote the code is far too generous grading its own homework; a second agent with different instructions (and sometimes a different model) catches what the first talked itself into. The usual split: one explores, one implements, one verifies against the spec. This is what `/goal` does under the hood — a fresh model decides if the loop is done. Sub-agents burn more tokens, so spend them where a second opinion is worth paying for.

---

## What one loop looks like

A single thread becomes a small control panel:

1. An **automation** runs each morning on the repo.
2. Its prompt calls a **triage skill** that reads yesterday's CI failures, open issues, and recent commits, and writes findings into a **state file** (markdown or Linear).
3. For each finding worth doing, the thread opens an isolated **worktree** and sends a **sub-agent** to draft the fix.
4. A second **sub-agent** reviews that draft against the project **skills** and existing tests.
5. **Connectors** open the PR and update the ticket. Anything the loop can't handle lands in a triage inbox for you.
6. The **state file** is the spine — it remembers what was tried, what passed, what's still open, so tomorrow's run picks up where today stopped.

You designed it once. You prompted none of the individual steps.

---

## What the loop still does NOT do for you

These three problems get *sharper* as the loop gets better, not easier:

- **Verification is still on you.** A loop running unattended is also a loop making mistakes unattended. Splitting the verifier sub-agent from the maker makes "it's done" mean something — but "done" is a claim, not a proof. Ship code you confirmed works.
- **Understanding rots if you let it.** The faster the loop ships code you didn't write, the bigger the gap between what exists and what you understand (*comprehension debt*). A smooth loop grows that gap faster unless you read what it made.
- **Comfort is the trap.** When the loop runs itself it's tempting to stop having an opinion and take whatever it returns (*cognitive surrender*). Designing the loop is the cure when done with judgement, and the accelerant when done to avoid thinking — same action, opposite result.

> Two people can build the identical loop and get opposite results: one moves faster on work they understand deeply, the other avoids understanding the work at all. The loop doesn't know the difference. You do.

**Build the loop. Stay the engineer.**

---

## Handoff

- This is a standalone reference — it has no dependencies inside this pack.
- Applying a loop to this repo's pipeline (scheduled re-pull / re-score / data-quality triage) → start from [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md) (the runnable pipeline) and wrap its steps in an automation + state file following the shape above.
