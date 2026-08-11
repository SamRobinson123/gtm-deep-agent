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


# Column names that have been renamed since runs were first written. Runs are
# immutable lineage artifacts and older ones must stay readable, so stored CSVs
# are migrated forward on read rather than rewritten on disk.
LEGACY_COLUMNS = {
    "maturation_tail_from_earlier_quarters": "sales_cycle_tail_from_earlier_quarters",
    # "later" was an internal coinage for the Pre Q win rate. Renamed 2026-08-11 to
    # the business's own term; runs written before then still carry the old header.
    "later_win_rate": "pre_q_win_rate",
}


def load_derivation(run_id: str):
    """Read a run's derivation, mapping legacy column names forward.

    Both derivation endpoints go through here. Reading the CSV directly means a
    rename either raises (the lookup fails) or, worse, silently drops the column
    from a filtered list and shows an incomplete waterfall.
    """
    import pandas as pd

    path = (config.RUNS / run_id).resolve() / "derived_pipe_create.csv"
    if not path.is_file():
        raise HTTPException(404, "this run has no derivation")
    df = pd.read_csv(path)
    renamed = {a: b for a, b in LEGACY_COLUMNS.items() if a in df.columns}
    return df.rename(columns=renamed), sorted(renamed.values())


@app.get("/api/runs/{run_id}/derivation")
async def run_derivation(run_id: str):
    """The logic chain behind a derived target, as ordered steps.

    The UI renders this as a ledger so each term can be inspected and challenged.
    Provenance is part of the payload, not a presentation detail: a term that is
    measured, one that is modelled, and one that is missing must not look alike.
    """
    df, _ = load_derivation(run_id)

    def money(x):
        return float(round(x, 2))

    out = []
    for qi, (q, g) in enumerate(df.groupby("quarter", sort=False)):
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
             "formula": "(open pipe x (1 - Pre Q slip) + slip inflow) x (1 - In Q slip) x Pre Q win rate",
             "note": "Slip and win rate apply in sequence, not as alternatives. "
                     "Pre Q slip is zero for the quarter in flight — it has already "
                     "happened and is inside the observed balance."},
            {"kind": "deduct", "label": "Sales cycle tail from earlier quarters",
             "value": money(-tail),
             # A zero tail on the FIRST quarter of the solve is correct, not
             # suspect: pipe created earlier and destined for this quarter is
             # already dated into it and sits in the observed open pipe above.
             # Modelling it again would double-count. A zero on a LATER quarter
             # is a real anomaly, because that quarter's feeder IS in the solve.
             "provenance": "modelled" if tail else ("given" if qi == 0 else "suspect"),
             "formula": "sum over prior quarters of created pipe x weight x Pre Q win rate",
             "note": ("Zero, and correct. Pipe created in earlier quarters that is due "
                      "to close in this one already exists and is already counted in "
                      "'Expected from existing pipe' above. Modelling a tail on top "
                      "would double-count it. The workbook reaches back 8 quarters "
                      "because it runs at annual planning time, when none of this is "
                      "observable yet.") if (tail == 0 and qi == 0) else
                     ("Zero despite an earlier quarter being in this solve — that "
                      "quarter's created pipe should be maturing into this one. "
                      "Investigate.") if tail == 0 else
                     "Pipe created in earlier quarters of this solve, maturing now."},
            {"kind": "subtotal", "label": "Bookings still to find", "value": money(gap),
             "provenance": "derived",
             "formula": "target - closed won - existing pipe - tail"},
            {"kind": "divide", "label": "Bookings yield per $ created",
             "value": float(round(yields.mean(), 6)) if len(yields) else None,
             "provenance": "modelled",
             "formula": "Q0 weight x in-quarter win rate",
             "note": ("Only the Q0 slice counts. The Q+1 and beyond slices book in "
                      "later quarters "
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

    # The CSV is the substance; the manifest only adds provenance. A run whose
    # manifest is missing or unreadable still has a chain worth showing.
    try:
        manifest = lineage.load_manifest(run_id)
    except (FileNotFoundError, ValueError):
        manifest = {}
    return {"run_id": run_id, "grain": manifest.get("grain", "Territory"),
            "quarters": out, "caveats": manifest.get("caveats", []),
            "warnings": manifest.get("warnings", [])}


@app.get("/api/runs/{run_id}/waterfall")
async def run_waterfall(run_id: str):
    """Every row of the solve, in workbook column order, with outlier flags.

    The aggregate ledger says what the number is; this says which territories
    made it that way and which of them rest on a questionable assumption.
    """
    from agent import waterfall as wf

    raw, migrated = load_derivation(run_id)
    df = wf.flag_outliers(raw, "Territory")
    df = df.replace({float("nan"): None})
    order = ["quarter", "Territory", "bookings_target", "closed_won",
             "expected_from_existing_pipe", "sales_cycle_tail_from_earlier_quarters",
             "gap", "q0_weight", "in_quarter_win_rate", "pre_q_win_rate",
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
    missing = [c for c in order if c not in df.columns]
    return {"run_id": run_id, "columns": cols, "quarters": quarters,
            "overridable": list(wf.ASSUMPTIONS),
            # Say so rather than quietly showing a narrower table.
            "migrated_columns": migrated, "missing_columns": missing}


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


@app.middleware("http")
async def no_store_static(request, call_next):
    """Never let the browser cache the app shell.

    This is a local dev tool whose JS and CSS change constantly. A cached app.js
    means edits appear not to take effect, which is indistinguishable from a bug
    in the code you just changed.
    """
    response = await call_next(request)
    if request.url.path in ("/", "/index.html") or request.url.path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
