"""FastAPI server — SSE chat, run/file endpoints, permission relay.

Holds no SDK concepts; session.py holds no HTTP concepts.

Binds to 127.0.0.1 only. There is no auth and this process can query a production
warehouse — it must not be reachable from the network.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import lineage
from gtm_ui.session import ChatSession
from pipeline import config

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="GTM Pipe Analytics")
session = ChatSession()


class Message(BaseModel):
    message: str


class Decision(BaseModel):
    allow: bool


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@app.post("/api/chat")
async def chat(body: Message):
    async def gen():
        try:
            async for ev in session.ask(body.message):
                yield sse(ev)
        except Exception as e:  # never leave the UI on a dead spinner
            yield sse({"type": "error", "error": type(e).__name__, "message": str(e)})
            yield sse({"type": "done"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/permission/{pid}")
async def permission(pid: str, body: Decision):
    if not session.resolve_permission(pid, body.allow):
        # Never a silent allow.
        raise HTTPException(404, "unknown or already-resolved approval")
    return {"ok": True}


@app.post("/api/reset")
async def reset():
    await session.reset()
    return {"ok": True}


@app.get("/api/runs")
async def runs():
    return list(reversed(lineage.list_runs()))


@app.get("/api/runs/{run_id}")
async def run_manifest(run_id: str):
    try:
        m = lineage.load_manifest(run_id)
    except FileNotFoundError:
        raise HTTPException(404, "run not found")
    d = config.RUNS / run_id
    m["_files"] = sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else []
    return m


@app.get("/api/runs/{run_id}/file/{name}")
async def run_file(run_id: str, name: str):
    """Serve one file from a run directory.

    SECURITY: resolve and confirm containment before serving. Without this,
    `../../.env` is a file-read primitive on a process holding a Synapse
    connection string.
    """
    base = (config.RUNS / run_id).resolve()
    target = (base / name).resolve()
    if base != target and base not in target.parents:
        raise HTTPException(403, "path outside the run directory")
    if not target.is_file():
        raise HTTPException(404, "file not found")
    media = "image/png" if target.suffix == ".png" else "text/plain"
    if target.suffix == ".csv":
        media = "text/csv"
    return FileResponse(target, media_type=media, filename=target.name)


@app.get("/api/health")
async def health():
    import os
    return {
        "auth": "api-key" if os.environ.get("ANTHROPIC_API_KEY") else "claude.ai login (Pro/Max)",
        "targets_csv": config.TARGET_MONTHLY_CSV.exists(),
        "quarter": config.fq_label(config.QUARTER_START),
    }


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
