"""Owns one ClaudeSDKClient and translates SDK messages into typed UI events.

Holds no HTTP concepts — that boundary is what makes event mapping testable
without a browser or a live agent.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)

from agent.options import build_options
from pipeline import config

PERMISSION_TIMEOUT_S = 300

# Tools that never need approval: reading docs, listing what exists. The prompt
# exists for actions that touch the warehouse or produce a stored run.
AUTO_ALLOW = {
    "Read", "Glob", "Grep", "Task",
    "mcp__gtm__az_login_status",
    "mcp__gtm__list_queries",
    "mcp__gtm__list_runs",
    "mcp__gtm__show_run",
}


def event(kind, **data):
    return {"type": kind, **data}


def tool_detail(name: str, tool_input: dict) -> str:
    """One human-readable line about what a tool call is doing.

    Shown live in the UI's activity feed — the point is orientation ("it is
    reading the waterfall doc"), not a faithful dump of the input.
    """
    i = tool_input or {}
    detail = ""
    if name == "Bash":
        detail = i.get("command", "")
    elif name in ("Read", "Write", "Edit", "NotebookEdit"):
        detail = i.get("file_path", "") or i.get("notebook_path", "")
    elif name in ("Glob", "Grep"):
        detail = i.get("pattern", "")
    elif name == "Task":
        detail = i.get("description", "") or i.get("subagent_type", "")
    elif name == "mcp__gtm__query":
        detail = i.get("purpose", "") or "composing SQL"
    elif isinstance(i, dict) and i:
        first = next(iter(i.values()))
        if isinstance(first, str):
            detail = first
    detail = " ".join(str(detail).split())  # collapse newlines in e.g. Bash commands
    return detail[:117] + "…" if len(detail) > 120 else detail


@dataclass
class Pending:
    """One awaited approval."""
    id: str
    tool: str
    input: dict
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class ChatSession:
    """One conversation. Reused across turns so follow-ups keep context."""

    def __init__(self, model=None):
        self.model = model
        self.pending: dict[str, Pending] = {}
        self._client: ClaudeSDKClient | None = None
        self._queue: asyncio.Queue | None = None

    # --- permission relay ---------------------------------------------------

    async def _can_use_tool(self, tool_name, tool_input, context):
        if tool_name in AUTO_ALLOW:
            return PermissionResultAllow()

        p = Pending(id=uuid.uuid4().hex[:12], tool=tool_name, input=tool_input or {})
        self.pending[p.id] = p
        if self._queue:
            await self._queue.put(event("permission_request", id=p.id, tool=p.tool, input=p.input))
        try:
            allowed = await asyncio.wait_for(p.future, timeout=PERMISSION_TIMEOUT_S)
        except asyncio.TimeoutError:
            return PermissionResultDeny(message="Approval timed out after 5 minutes — not run.")
        except asyncio.CancelledError:
            return PermissionResultDeny(message="Cancelled.")
        finally:
            self.pending.pop(p.id, None)

        if allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Declined by the user.")

    def resolve_permission(self, pid: str, allow: bool) -> bool:
        """Called by the HTTP layer. Returns False for unknown/already-resolved."""
        p = self.pending.get(pid)
        if p is None or p.future.done():
            return False
        p.future.set_result(bool(allow))
        return True

    # --- run detection ------------------------------------------------------

    @staticmethod
    def _run_ids() -> list[str]:
        idx = config.RUNS / "index.jsonl"
        if not idx.exists():
            return []
        out = []
        for line in idx.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line)["run_id"])
                except Exception:
                    pass
        return out

    # --- the turn -----------------------------------------------------------

    async def _ensure_client(self):
        if self._client is None:
            opts = build_options(
                can_use_tool=self._can_use_tool,
                **({"model": self.model} if self.model else {}),
            )
            self._client = ClaudeSDKClient(options=opts)
            await self._client.__aenter__()
        return self._client

    async def reset(self):
        if self._client is not None:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None

    async def ask(self, message: str):
        """Async generator of UI events for one turn."""
        self._queue = asyncio.Queue()
        before = set(self._run_ids())

        try:
            client = await self._ensure_client()
        except Exception as e:
            yield event("error", error=type(e).__name__, message=str(e))
            yield event("done")
            return

        async def pump():
            try:
                await client.query(message)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                await self._queue.put(event("token", text=block.text))
                            elif isinstance(block, ThinkingBlock):
                                # No content is forwarded — reasoning stays private.
                                # The UI only needs to know the agent is thinking.
                                await self._queue.put(event("thinking"))
                            elif isinstance(block, ToolUseBlock):
                                await self._queue.put(event(
                                    "tool_use",
                                    name=block.name,
                                    detail=tool_detail(block.name, block.input),
                                ))
                    elif isinstance(msg, ResultMessage):
                        await self._queue.put(
                            event("result", cost_usd=getattr(msg, "total_cost_usd", None))
                        )
            except Exception as e:
                await self._queue.put(event("error", error=type(e).__name__, message=str(e)))
            finally:
                await self._queue.put(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                ev = await self._queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            if not task.done():
                task.cancel()
            # Any approval still awaited would hang forever once the stream ends.
            for p in list(self.pending.values()):
                if not p.future.done():
                    p.future.cancel()

        for rid in self._run_ids():
            if rid not in before:
                yield event("run_created", run_id=rid)
        yield event("done")
