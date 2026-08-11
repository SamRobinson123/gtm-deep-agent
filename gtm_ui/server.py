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


@app.get("/api/runs/{run_id}/derivation")
async def run_derivation(run_id: str):
    """The logic chain behind a derived target, as ordered steps.

    The UI renders this as a ledger so each term can be inspected and challenged.
    Provenance is part of the payload, not a presentation detail: a term that is
    measured, one that is modelled, and one that is missing must not look alike.
    """
    import pandas as pd

    path = (config.RUNS / run_id).resolve() / "derived_pipe_create.csv"
    if not path.is_file():
        raise HTTPException(404, "this run has no derivation")
    df = pd.read_csv(path)

    def money(x):
        return float(round(x, 2))

    out = []
    for q, g in df.groupby("quarter", sort=False):
        target = g["bookings_target"].sum()
        won = g["closed_won"].sum() if "closed_won" in g else 0.0
        existing = g["expected_from_existing_pipe"].sum()
        tail = g["sales_cycle_tail_from_earlier_quarters"].sum()
        gap = g["gap"].clip(lower=0).sum()
        required = g["required_by_gap"].sum()
        total = g["pipe_create_target"].sum()
        yields = g.loc[g["yield_per_dollar"] > 0, "yield_per_dollar"]
        floor_rows = int((g["binding"] == "floor").sum())

        steps = [
            {"kind": "given", "label": "Bookings target", "value": money(target),
             "provenance": "given",
             "formula": "Target_Monthly.csv, Target_Type = 'Bookings', summed to grain",
             "note": "Supplied by finance planning. Not derived here."},
            {"kind": "deduct", "label": "Closed Won to date", "value": money(-won),
             "provenance": "measured",
             "formula": "snapshot, Raw_Stage matching 'Closed.*Won', CloseDate in quarter",
             "note": "Already banked. Pipe create does not have to cover it."},
            {"kind": "deduct", "label": "Expected from existing pipe", "value": money(-existing),
             "provenance": "modelled",
             "formula": "open pipe x (1 - slip rate) x pre-Q win rate",
             "note": "Slip and win rate apply in sequence, not as alternatives."},
            {"kind": "deduct", "label": "Sales cycle tail from earlier quarters",
             "value": money(-tail),
             "provenance": "suspect" if tail == 0 else "modelled",
             "formula": "sum over prior quarters of created pipe x weight x pre-Q win rate",
             "note": ("Zero because no earlier quarter is in this solve. The workbook "
                      "feeds this from up to 8 prior quarters, so the gap below is "
                      "overstated and the target with it.") if tail == 0 else
                     "Pipe created in earlier quarters of this solve, maturing now."},
            {"kind": "subtotal", "label": "Bookings still to find", "value": money(gap),
             "provenance": "derived",
             "formula": "target - closed won - existing pipe - tail"},
            {"kind": "divide", "label": "Bookings yield per $ created",
             "value": float(round(yields.mean(), 6)) if len(yields) else None,
             "provenance": "modelled",
             "formula": "Q0 weight x in-quarter win rate",
             "note": ("Only the Q0 slice counts. Later slices book in later quarters "
                      "and are propagated forward, so counting them here would book "
                      "the same dollars twice."),
             "spread": {"min": float(round(yields.min(), 6)),
                        "max": float(round(yields.max(), 6))} if len(yields) else None},
            {"kind": "result", "label": "Required to close the gap", "value": money(required),
             "provenance": "derived", "formula": "gap / yield, per grain key, then summed"},
            {"kind": "clamp", "label": "Historic floor uplift",
             "value": money(total - required),
             "provenance": "constraint",
             "formula": "max(required, prior-year same-quarter actual creation)",
             "note": f"Floor binds on {floor_rows} of {len(g)} rows. Where it binds, the "
                     f"target is last year's level, not what the bookings math asks for."},
            {"kind": "total", "label": "Pipe create target", "value": money(total),
             "provenance": "derived"},
        ]
        out.append({"quarter": q, "rows": int(len(g)), "steps": steps})

    manifest = lineage.load_manifest(run_id)
    return {"run_id": run_id, "grain": manifest.get("grain", "Territory"),
            "quarters": out, "caveats": manifest.get("caveats", []),
            "warnings": manifest.get("warnings", [])}


@app.get("/api/runs/{run_id}/waterfall")
async def run_waterfall(run_id: str):
    """Every row of the solve, in workbook column order, with outlier flags.

    The aggregate ledger says what the number is; this says which territories
    made it that way and which of them rest on a questionable assumption.
    """
    import pandas as pd

    from agent import waterfall as wf

    path = (config.RUNS / run_id).resolve() / "derived_pipe_create.csv"
    if not path.is_file():
        raise HTTPException(404, "this run has no derivation")

    df = wf.flag_outliers(pd.read_csv(path), "Territory")
    df = df.replace({float("nan"): None})
    order = ["quarter", "Territory", "bookings_target", "closed_won",
             "expected_from_existing_pipe", "sales_cycle_tail_from_earlier_quarters",
             "gap", "q0_weight", "in_quarter_win_rate", "later_win_rate",
             "yield_per_dollar", "required_by_gap", "historic_floor",
             "pipe_create_target", "binding", "outlier_flags", "outlier_reasons"]
    cols = [c for c in order if c in df.columns]

    quarters = []
    for q, g in df.groupby("quarter", sort=False):
        g = g.sort_values("pipe_create_target", ascending=False)
        quarters.append({
            "quarter": q,
            "rows": g[cols].to_dict("records"),
            "flagged": int((g["outlier_flags"] != "").sum()),
            "total": float(g["pipe_create_target"].sum()),
        })
    return {"run_id": run_id, "columns": cols, "quarters": quarters,
            "overridable": list(wf.ASSUMPTIONS)}


@app.get("/api/auth/status")
async def auth_status():
    """Whether a Synapse token can be obtained right now. No side effects."""
    from pipeline import pull
    return pull.auth_status()


@app.post("/api/auth/login")
async def auth_login():
    """Trigger interactive MFA. Opens the user's browser; blocks until complete."""
    import anyio
    from pipeline import pull
    try:
        await anyio.to_thread.run_sync(pull.interactive_login)
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    return pull.auth_status()


@app.get("/api/health")
async def health():
    import os
    return {
        "auth": "api-key" if os.environ.get("ANTHROPIC_API_KEY") else "claude.ai login (Pro/Max)",
        "targets_csv": config.TARGET_MONTHLY_CSV.exists(),
        "quarter": config.fq_label(config.QUARTER_START),
    }


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
