"""Deliverables — formatted Excel and charts from a stored run.

Demoted from `agent.tools.export_excel` / `export_chart` in v2 step 4. The write
boundary is unchanged and still lives in `agent/exports.py`: a NAME is supplied,
never a path, and `export_path()` confines every write to workspace/exports/.

    python -m pipeline.export_cli excel --name q3_q4_waterfall
    python -m pipeline.export_cli chart --kind waterfall --quarter "Q3 FY26"

Defaults to the LATEST run. Always state which run_id was exported — a run made
in another window is a real possibility and the numbers genuinely differ.
"""
from __future__ import annotations

import argparse

import pandas as pd

from agent import exports, lineage
from pipeline import config


def resolve_run(run_id=None):
    """A run id, defaulting to the most recent. Returns (run_id, csv_path).

    Lists and reads from the SAME root (config.RUNS). lineage.list_runs()
    defaults to its own module constant, which is captured at import — letting
    the two diverge meant the index could name a run this function then failed
    to find on disk.
    """
    runs = lineage.list_runs(config.RUNS)
    if not runs:
        raise ValueError("No runs recorded yet. Derive a target first, then export it.")
    rid = run_id or runs[-1]["run_id"]
    d = config.RUNS / rid
    if not d.exists():
        raise ValueError(f"Run {rid!r} not found. Read workspace/runs/index.jsonl to see what exists.")
    csvs = sorted(d.glob("*.csv"))
    if not csvs:
        raise ValueError(f"Run {rid!r} stored no CSV to export.")
    return rid, csvs[0]


# Legacy headers, kept in step with the UI's own migration so a run exported
# today reads the same as that run viewed today.
LEGACY_COLUMNS = {
    "maturation_tail_from_earlier_quarters": "sales_cycle_tail_from_earlier_quarters",
    "later_win_rate": "pre_q_win_rate",
}


def to_excel(run_id=None, name=None):
    rid, csv = resolve_run(run_id)
    df = pd.read_csv(csv)
    renamed = {a: b for a, b in LEGACY_COLUMNS.items() if a in df.columns}
    df = df.rename(columns=renamed)

    path = exports.export_path(name or f"{csv.stem}_{rid[:17]}", ".xlsx")
    sheets = []
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        if "quarter" in df.columns:
            # Summary first so the workbook opens on the totals, not row 1 of a
            # 27-row territory table.
            agg = {c: "sum" for c in df.columns
                   if pd.api.types.is_numeric_dtype(df[c]) and not exports.RATE.search(c)}
            if agg:
                summary = df.groupby("quarter", sort=False).agg(agg).reset_index()
                exports.write_sheet(w, summary, "Summary")
                sheets.append("Summary")
            for q, g in df.groupby("quarter", sort=False):
                exports.write_sheet(w, g.reset_index(drop=True), str(q))
                sheets.append(str(q))
        else:
            exports.write_sheet(w, df, "Data")
            sheets.append("Data")

    total = ""
    if "pipe_create_target" in df.columns and "quarter" in df.columns:
        per_q = df.groupby("quarter", sort=False)["pipe_create_target"].sum()
        total = " | ".join(f"{q} ${v:,.0f}" for q, v in per_q.items())

    return (f"{path}\n"
            f"{len(df)} rows from run {rid}, across {len(sheets)} sheet(s): "
            f"{', '.join(sheets)}.\n"
            + (f"Derived pipe create: {total}. DERIVED, not the published target.\n" if total else "")
            + (f"Migrated legacy columns: {', '.join(sorted(renamed.values()))}.\n" if renamed else ""))


def to_chart(run_id=None, kind="pipe_create", quarter=None, name=None):
    import matplotlib
    matplotlib.use("Agg")            # no display in this process
    import matplotlib.pyplot as plt

    rid, csv = resolve_run(run_id)
    df = pd.read_csv(csv)
    grain = next((c for c in ("Territory", "Region", "Geo") if c in df.columns), None)
    quarters = list(dict.fromkeys(df["quarter"])) if "quarter" in df.columns else []
    q = quarter or (quarters[0] if quarters else "")
    sub = df[df["quarter"] == q] if q and "quarter" in df.columns else df

    fig, ax = plt.subplots(figsize=(11, 6.5))
    money = matplotlib.ticker.FuncFormatter(
        lambda v, _: ("-" if v < 0 else "") + f"${abs(v)/1e6:,.1f}M")

    if kind == "waterfall":
        terms = [("bookings_target", "Bookings target"),
                 ("closed_won", "less Closed Won"),
                 ("expected_from_existing_pipe", "less existing pipe"),
                 ("sales_cycle_tail_from_earlier_quarters", "less sales cycle tail"),
                 ("gap", "= Gap to fill")]
        have = [(c, l) for c, l in terms if c in sub.columns]
        raw = [sub[c].sum() for c, _ in have]
        deltas = [raw[0]] + [-v for v in raw[1:-1]] + [raw[-1]]
        bases, run, last = [], 0.0, len(deltas) - 1
        for i, d in enumerate(deltas):
            if i in (0, last):
                bases.append(0.0)
                if i == 0:
                    run = d
            else:
                run += d
                # A deduction HANGS DOWN from the old balance to the new one, so
                # its base is the NEW (lower) balance. `run - d` puts the base at
                # the old balance and the bar floats upward — plausible-looking
                # and exactly backwards.
                bases.append(run if d < 0 else run - d)
        heights = [abs(d) for d in deltas]
        colors = ["#3f6f9f"] + ["#b4553f"] * (len(deltas) - 2) + ["#4a7c59"]
        ax.bar([l for _, l in have], heights, bottom=bases, color=colors, width=0.6)
        for i in range(last):
            y = bases[i] if deltas[i] < 0 else bases[i] + heights[i]
            ax.plot([i + 0.3, i + 1.3], [y, y], color="#999", lw=1, ls="--", zorder=0)
        for i, d in enumerate(deltas):
            ax.text(i, bases[i] + heights[i] + max(heights) * 0.015,
                    ("$0" if abs(d) < 1 else f"${d:,.0f}"),
                    ha="center", va="bottom", fontsize=9)
        ax.set_ylabel("Bookings $")
        ax.yaxis.set_major_formatter(money)
        ax.set_ylim(0, max(b + h for b, h in zip(bases, heights)) * 1.12)
        plt.setp(ax.get_xticklabels(), rotation=12, ha="right")
    else:
        if grain is None or "pipe_create_target" not in sub.columns:
            plt.close(fig)
            raise ValueError("the run has no grain column or no pipe_create_target")
        d = sub.groupby(grain)["pipe_create_target"].sum().sort_values()
        ax.barh(d.index, d.values, color="#3f6f9f")
        ax.set_xlabel("Derived pipe create target $")
        ax.xaxis.set_major_formatter(money)
        ax.tick_params(axis="y", labelsize=8)

    ax.set_title(f"{q or 'All quarters'} — {kind.replace('_', ' ')} by "
                 f"{grain or 'run'} (DERIVED, not published)")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()

    path = exports.export_path(
        name or f"{kind}_{str(q).replace(' ', '_')}_{rid[:17]}", ".png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return (f"{path}\n"
            f"{kind.replace('_', ' ')} chart for {q or 'all quarters'} at "
            f"{grain or 'run'} grain, {len(sub)} rows, 150 dpi.\n"
            f"DERIVED figures — state that when sharing.")


def to_pdf(run_id=None, name=None, title=None):
    """A stored run as a PDF report: title, caveat block, per-quarter tables.

    A PDF is the output most likely to be forwarded to someone with no context
    around it, so the caveats travel IN the document: the DERIVED-not-published
    warning, the run id (traceability), and every caveat slug the manifest
    carries. Same write boundary as everything else — a NAME goes in, never a
    path.
    """
    import json

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    rid, csv = resolve_run(run_id)
    df = pd.read_csv(csv)
    df = df.rename(columns={a: b for a, b in LEGACY_COLUMNS.items() if a in df.columns})

    manifest = {}
    mpath = config.RUNS / rid / "manifest.json"
    if mpath.exists():
        manifest = json.loads(mpath.read_text(encoding="utf-8"))

    path = exports.export_path(name or f"report_{csv.stem}_{rid[:17]}", ".pdf")

    styles = getSampleStyleSheet()
    caveat_style = ParagraphStyle(
        "caveat", parent=styles["Normal"], fontSize=8.5,
        textColor=colors.HexColor("#8a3324"), leading=11)
    meta_style = ParagraphStyle(
        "meta", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    story = [Paragraph(title or f"GTM report — {csv.stem}", styles["Title"]),
             Paragraph(f"run {rid}", meta_style),
             Spacer(1, 6)]

    # The caveats are the first content, not a footnote. DERIVED-not-published
    # is stated even when the manifest predates warning slugs, because this
    # exporter cannot know the reader's context.
    story.append(Paragraph(
        "DERIVED figures unless a row says otherwise — computed from current "
        "data and assumptions, NOT the published target.", caveat_style))
    for slug in manifest.get("caveats", []) + manifest.get("warnings", []):
        story.append(Paragraph(f"caveat: {slug}", caveat_style))
    story.append(Spacer(1, 12))

    money = MONEY_COLS = [c for c in df.columns if exports.MONEY.search(c)
                          and not exports.RATE.search(c)]

    def rendered(col, v):
        if pd.isna(v):
            return "—"
        if exports.RATE.search(col) and isinstance(v, float):
            return f"{v:.1%}"
        if col in MONEY_COLS and isinstance(v, (int, float)):
            return f"${v:,.0f}"
        return str(v)

    groups = df.groupby("quarter", sort=False) if "quarter" in df.columns else [("All", df)]
    for q, g in groups:
        # Cap the column count so the table fits a landscape page; the Excel
        # export carries the full width, and the PDF is a report, not a dump.
        cols = [c for c in g.columns if c != "quarter"][:8]
        story.append(Paragraph(str(q), styles["Heading2"]))
        data = [[c.replace("_", " ") for c in cols]]
        for _, row in g.iterrows():
            data.append([rendered(c, row[c]) for c in cols])
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(t)
        story.append(Spacer(1, 14))

    doc = SimpleDocTemplate(str(path), pagesize=landscape(letter),
                            leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
                            title=title or csv.stem)
    doc.build(story)

    return (f"{path}\n"
            f"{len(df)} rows from run {rid} as a PDF report.\n"
            f"DERIVED figures — the caveat is printed in the document itself.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("excel", "chart", "pdf"):
        p = sub.add_parser(cmd)
        p.add_argument("--run-id", dest="run_id")
        p.add_argument("--name")
        if cmd == "chart":
            p.add_argument("--kind", default="pipe_create",
                           choices=["pipe_create", "waterfall"])
            p.add_argument("--quarter")
        if cmd == "pdf":
            p.add_argument("--title")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "excel":
            print(to_excel(a.run_id, a.name))
        elif a.cmd == "chart":
            print(to_chart(a.run_id, a.kind, a.quarter, a.name))
        else:
            print(to_pdf(a.run_id, a.name, a.title))
    except Exception as e:
        print(f"Failed: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
