# GTM Chat UI — Design

**Date:** 2026-08-10
**Status:** Approved, not yet implemented
**Depends on:** [`2026-08-10-gtm-deep-agent-v1-design.md`](2026-08-10-gtm-deep-agent-v1-design.md) — implemented and working

> Lives in `docs/superpowers/specs/`, which both `docs/README.md` and the root
> `CLAUDE.md` declare **non-context**. Nothing here may be cited as fact about
> Tricentis data or the models.

---

## Goal

A browser chat interface for the GTM agent: conversation in the middle, rendered
tables and charts inline, CSV downloads, and past runs one click away.

The terminal REPL works but cannot show a 14-row table well, cannot hand you a
file, and cannot render a chart at all.

**Audience: one user, on one machine.** No auth, no hosting, no multi-tenancy.

---

## The constraint that shapes everything

`permission_mode` is `default`, and that was a deliberate security decision: the
approval prompt means someone without warehouse authority cannot use the agent to
run queries on their behalf.

In the terminal that works because approval happens in the operator's own shell,
under their own `az login`. **A web UI has no shell.** If the UI simply ran the
agent server-side with prompts suppressed, the control would silently vanish —
every pull would execute under whoever started the server.

This design keeps the control by **relaying** it: the approval prompt becomes a
card in the browser, and the agent genuinely blocks until it is answered. §3 is
the mechanism. It is the most important part of this spec.

Localhost-only binding follows from the same reasoning: `127.0.0.1`, never
`0.0.0.0`. An unauthenticated UI that can query production must not be reachable
from the network.

---

## Architecture

Four new files and one new tool. **The existing agent is not modified** — the UI
is a second front end over the same `build_options()`.

```
gtm_ui/
├── server.py       FastAPI: SSE chat, run/file endpoints, permission relay
├── session.py      owns one ClaudeSDKClient; SDK messages -> typed UI events
└── static/
    ├── index.html  layout: sidebar (runs) + chat pane + composer
    ├── app.js      SSE consumer, event renderer, CSV table, download links
    └── style.css   no framework, no build step

agent/charts.py     chart tool -> PNG written into the run directory
```

`session.py` holds no HTTP concepts and `server.py` holds no SDK concepts. That
boundary is what makes event mapping testable without a browser or a live agent.

### Why no build step

No Node, no npm, no bundler. `python -m gtm_ui` and the app is running. A
toolchain would be a second thing to maintain, and the UI is a chat pane plus
tables — it does not need a component framework.

---

## Event protocol

`POST /api/chat` with `{"message": "..."}` returns an SSE stream. `session.py`
translates SDK messages into exactly these events:

| Event | Payload | Rendered as |
|---|---|---|
| `token` | `{text}` | Appended to the assistant bubble |
| `tool_use` | `{name}` | Inline chip, e.g. `[pipe_create_targets]` |
| `permission_request` | `{id, tool, input}` | Approve/deny card; **turn blocks** |
| `run_created` | `{run_id}` | Triggers an artifact card fetch |
| `error` | `{type, message}` | Error bubble — never a dead spinner |
| `done` | `{cost_usd}` | Ends the turn, re-enables the composer |

Showing `tool_use` matters: without it a pull looks like a hang. The user should
see the agent reach for a tool.

`run_created` is detected by watching `workspace/runs/index.jsonl` for new entries
during a turn, rather than by changing any tool's return contract. Tools stay
plain-text; the UI reads the run store.

---

## Permission relay

The mechanism that preserves the security control.

```
agent wants to run_pull
  -> can_use_tool callback fires (async)
  -> emits permission_request event, then awaits an asyncio.Future
  -> UI renders an approve/deny card
  -> user clicks -> POST /api/permission/{id} {"allow": true|false}
  -> server resolves the future
  -> callback returns PermissionResultAllow or PermissionResultDeny
  -> the turn continues, or the tool is refused with a reason
```

The turn genuinely blocks while awaiting. This is the same semantics as the
terminal prompt, relocated.

**Failure handling:**

| Case | Behaviour |
|---|---|
| No answer within 5 minutes | Future resolves to deny with reason "approval timed out" |
| Browser closes mid-request | Connection loss cancels the turn; the future is cancelled |
| Duplicate POST for one id | Second is ignored; a future resolves once |
| Unknown id | 404 — never a silent allow |

**A denied tool is not an error.** The agent is told it was refused and continues,
which is exactly what should happen when someone declines a pull.

---

## Artifact cards

Endpoints, all read-only:

| Endpoint | Returns |
|---|---|
| `GET /api/runs` | Run list from `index.jsonl`, newest first |
| `GET /api/runs/{id}` | The full manifest |
| `GET /api/runs/{id}/file/{name}` | One file from the run directory |

**Path safety:** `{name}` is resolved and checked to be inside that run's
directory before serving. Without it, `../../.env` is a file-read primitive on a
server that already holds a Synapse connection string.

A card shows:

1. **Table** — CSV parsed client-side, sortable by column header. Over 200 rows,
   the card shows the first 200 and says so; the download is always complete.
2. **Chart** — any PNG in the run directory, rendered inline.
3. **Downloads** — one button per file.
4. **Lineage** (collapsed) — input paths with sha256, git commit, `git_dirty`,
   derived `month_columns`, and `caveats[]`.

**Caveats render as a visible banner on the card, not buried in the expander.**
Root `CLAUDE.md` requires the invariant-10 caveat to travel with every opp-count
and ASP figure; a UI that hides it in a collapsed panel breaks that rule.

The sidebar lists past runs. Selecting one opens its card without a new agent
turn — a superseded number stays reviewable, which is the point of lineage.

---

## Charts

New tool in `agent/charts.py`, writing a 150 dpi PNG into the run directory per
the `CLAUDE.md` output conventions, titled with quarter and grain.

v1 chart set, deliberately small:

| Chart | Shows |
|---|---|
| Weekly target vs actual | Bars by week; actuals omitted until a pull exists |
| Attainment by grain | Horizontal bars, one per Geo/Region/Territory |

**Null weeks must render as gaps, not zeros.** A not-yet-started week has no
target (invariant 4); drawing it at zero would read as a catastrophic miss. This
is the single most likely way a chart lies about this data.

Charts live in the run directory, so a chart is reproducible from its manifest
like any other output.

---

## Errors

| Failure | Behaviour |
|---|---|
| Agent raises mid-turn | `error` event, error bubble, composer re-enabled |
| SSE connection drops | Turn cancelled server-side; no orphaned agent task |
| Run directory missing | Card shows "run not found" instead of failing the turn |
| CSV unparseable | Offer the download; skip the table |
| Port 8000 busy | Fail at startup with the port number, not a stack trace |
| Auth unavailable | Startup states which auth path is in use — API key or claude.ai login |

---

## Testing

**Unit — `gtm_ui/session.py`, no browser, no live agent:**

1. An `AssistantMessage` with a `TextBlock` maps to one `token` event.
2. A `ToolUseBlock` maps to a `tool_use` event carrying the tool name.
3. A `ResultMessage` maps to `done` with cost when present.
4. An exception maps to `error`, never a swallowed failure.
5. A new line in `index.jsonl` during a turn produces `run_created`.

**Unit — permission relay:**

6. Resolving the future with allow returns `PermissionResultAllow`.
7. Deny returns `PermissionResultDeny` with the reason.
8. Timeout resolves to deny.
9. A second POST for the same id is ignored.

**Endpoint — FastAPI `TestClient`:**

10. `GET /api/runs` returns runs newest-first.
11. `GET /api/runs/{id}/file/../../.env` is refused. **The security test.**
12. Unknown run id returns 404.

**Manual:** the browser layer is verified by using it. A screenshot of a rendered
run card is the acceptance evidence.

---

## Success criteria

- `python -m gtm_ui` serves `127.0.0.1:8000` and streams a reply token by token.
- Asking for Q3 FY26 targets renders a sortable 14-row table with a working CSV
  download and a visible invariant-10 caveat banner.
- Asking for a pull shows an approve/deny card; the agent blocks until answered,
  and denial is handled as a refusal rather than an error.
- The sidebar opens a past run's card without a new agent turn.
- Tests 1–12 pass.

---

## Out of scope for v1

| Deferred | Why |
|---|---|
| Conversation persistence across restarts | The runs are the durable artifact; chat history is not yet worth a store |
| Full markdown rendering | Tables and code blocks only — enough for this content |
| Multi-user / auth / hosting | Single user by decision. Would require redesigning whose identity queries run under |
| Editing or re-running past runs | Runs are immutable by design |
