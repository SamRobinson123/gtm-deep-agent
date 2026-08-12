"""DERIVED Pipe Create targets, what-ifs, and the assumptions behind them.

Demoted from three tools in `agent.tools` in v2 step 4 — `derive_pipe_create_target`,
`what_if_assumption` and `show_assumptions`. Same logic, same lineage, same
caveat slugs.

    python -m pipeline.waterfall_cli derive --quarters "Q3 FY26, Q4 FY26"
    python -m pipeline.waterfall_cli whatif --key "AMS Corporate" \\
        --assumption in_quarter_win_rate --value 0.40
    python -m pipeline.waterfall_cli assumptions --quarter "Q3 FY26"

or from a scratch script: `from pipeline.waterfall_cli import derive, what_if`.

DERIVED, not published. These figures are what the target WOULD be given current
data and assumptions; `pipeline/targets_cli.py` reads what a prior planning cycle
actually published. Report which one, and give the other alongside it — the gap
between them is the finding.
"""
from __future__ import annotations

import argparse

from agent import lineage, targets, waterfall
from pipeline import config
from pipeline.derive import derive_frame


def derive(quarters="Q3 FY26", grain="Territory", window=None, as_of=None):
    """Solve for the required pipe create. Returns (frame, notes, summary, run_dir)."""
    raw = (quarters or "Q3 FY26").replace(";", ",")
    qs = [targets.resolve_quarter(q.strip()) for q in raw.split(",") if q.strip()]

    if grain != "Territory":
        raise ValueError("Bookings targets are keyed by territory. Use grain='Territory' "
                         "until a mapping to Region/Geo keys is agreed.")
    # Fail early with a readable message rather than deep inside the assembly.
    waterfall.load_sku(grain)

    df, notes = derive_frame(raw, grain=grain, as_of=as_of, window=window)
    summary = waterfall.summarize(df)

    with lineage.Run(quarter_start=qs[0]) as run:
        run.add_input(config.DATA / "sku_nacv.parquet").add_input(config.DATA / "bts.parquet")
        run.add_input(config.TARGET_MONTHLY_CSV)
        out = run.dir / "derived_pipe_create.csv"
        df.to_csv(out, index=False)
        run.add_output(out, rows=len(df))
        run.headline(**{k: v for k, v in summary.items() if k != "by_quarter"},
                     **{f"derived_{k}": v for k, v in summary["by_quarter"].items()})
        run.caveat("invariant-10-opportunities-unit")
        run.warn("derived-not-published-target")
        if not summary["existing_pipe_supplied"]:
            run.warn("existing-pipe-bookings-omitted-overstates-required-create")
        run_dir = run.dir

    return df, notes, summary, run_dir


def render_derive(df, notes, summary, run_dir, quarters):
    published = {}
    for q in [targets.resolve_quarter(x.strip())
              for x in quarters.replace(";", ",").split(",") if x.strip()]:
        try:
            published[config.fq_label(q)] = targets.quarter_total(q)["pipe_target"]
        except Exception:
            pass

    lines = ["DERIVED pipe create target (recomputed from source, not read from the CSV)", ""]
    for qlabel, derived_total in summary["by_quarter"].items():
        pub = published.get(qlabel)
        delta = f"  vs published ${pub:,.0f}  ({derived_total / pub - 1:+.1%})" if pub else ""
        lines.append(f"  {qlabel}: ${derived_total:,.0f}{delta}")
    lines += [
        "",
        f"Floor-driven: ${summary['floor_driven']:,.0f} "
        f"({summary['floor_driven_pct']:.1%} of total) across {summary['rows_floor_bound']} rows; "
        f"{summary['rows_gap_bound']} rows are gap-driven.",
        "  floor-driven = the team must not create less than the same quarter last year.",
        "  gap-driven   = the bookings target requires it.",
        "",
        df.head(25).to_string(index=False),
    ]
    if notes:
        lines += ["", "Assumptions and exclusions:"] + [f"  {n}" for n in notes]
    lines += ["", "This is a derived figure, not the published target. State that when reporting it.",
              f"Run stored: {run_dir}"]
    return "\n".join(lines)


def what_if(key, assumption, value, quarters="Q3 FY26, Q4 FY26"):
    """Re-solve with one assumption replaced. Returns the rendered comparison."""
    if assumption not in waterfall.ASSUMPTIONS:
        raise ValueError(f"Cannot override {assumption!r}. "
                         f"Available: {', '.join(waterfall.ASSUMPTIONS)}.")
    if value is None:
        raise ValueError("A value is required.")
    if assumption.endswith("win_rate") and not 0 <= float(value) <= 1:
        raise ValueError(f"{assumption} is a fraction between 0 and 1 — got {value}. 40% is 0.40.")

    raw = (quarters or "Q3 FY26, Q4 FY26").replace(";", ",")
    qs = [targets.resolve_quarter(q.strip()) for q in raw.split(",") if q.strip()]

    # Baseline and what-if go through the SAME assembly, so the delta is a real
    # comparison rather than two different calculations put side by side.
    base, notes = derive_frame(raw)
    what, _ = derive_frame(raw, overrides={key: {assumption: float(value)}})

    if key not in set(base["Territory"]):
        near = [t for t in sorted(set(base["Territory"])) if key.lower() in t.lower()]
        raise ValueError(f"No territory named {key!r}."
                         + (f" Did you mean: {', '.join(near)}?" if near else ""))

    b = base.set_index(["quarter", "Territory"])
    a = what.set_index(["quarter", "Territory"])
    was = b.xs(key, level="Territory")[assumption].iloc[0]
    lines = [f"WHAT IF — {key}: {assumption} {was:.4f} -> {float(value):.4f}", ""]

    for q in [config.fq_label(x) for x in qs]:
        if (q, key) not in b.index:
            continue
        before, after = b.loc[(q, key)], a.loc[(q, key)]
        t0, t1 = before["pipe_create_target"], after["pipe_create_target"]
        delta = f"{t1 - t0:+,.0f}" + (f", {(t1 / t0 - 1):+.1%}" if t0 else "")
        lines += [
            q,
            f"  yield per $       {before['yield_per_dollar']:.4f} -> {after['yield_per_dollar']:.4f}",
            f"  required by gap   ${before['required_by_gap']:,.0f} -> ${after['required_by_gap']:,.0f}",
            f"  historic floor    ${after['historic_floor']:,.0f}"
            f"   (binding: {before['binding']} -> {after['binding']})",
            f"  TARGET            ${t0:,.0f} -> ${t1:,.0f}   ({delta})",
        ]
        if before["binding"] == "gap" and after["binding"] == "floor":
            lines.append("  NOTE: the floor now binds, so further improvement to this "
                         "assumption cannot lower the target.")
        qb = base[base.quarter == q]["pipe_create_target"].sum()
        qa = what[what.quarter == q]["pipe_create_target"].sum()
        lines += [f"  {q} all territories: ${qb:,.0f} -> ${qa:,.0f} ({qa - qb:+,.0f})", ""]

    if notes:
        lines += notes + [""]
    lines.append("This is a what-if, not a published or derived target. The override "
                 "replaces a measured assumption and flows through yield, gap, the floor "
                 "comparison and the tail pushed into later quarters.")
    return "\n".join(lines)


def assumptions(quarter=None, grain="Territory", window=None):
    """The inputs a derived target rests on, without solving for one."""
    q = targets.resolve_quarter(quarter)
    sku = waterfall.load_sku(grain)

    rates = waterfall.win_rates(sku, window, grain).rename(
        columns={"in_quarter": "In Q win rate", "pre_q": "Pre Q win rate"})
    curve = waterfall.sales_cycle_weights(sku, window, grain)
    floor = waterfall.historic_floor(sku, q, grain)

    parts = [f"ASSUMPTIONS for {config.fq_label(q)}, grain={grain}"
             + (f", window {window[0]}..{window[1]}" if window else ", full history"),
             "",
             "WIN RATES — In Q closes in its create quarter, Pre Q closes later.",
             rates.to_string(float_format=lambda x: f"{x:.1%}"),
             "",
             "SALES CYCLE CURVE — share of created pipe closing at each quarter "
             "offset. Q0 x In Q win rate is the yield the goal seek divides by.",
             curve.to_string(float_format=lambda x: f"{x:.3f}"),
             "",
             "HISTORIC FLOOR — same quarter last year's actual creation.",
             floor.to_string(float_format=lambda x: f"{x:,.0f}")]

    # Observed state is optional: it needs a snapshot pull, and the fitted
    # assumptions above are still worth showing without one.
    for name, fn in (("OPEN PIPE dated into the quarter", waterfall.open_pipe_at),
                     ("CLOSED WON to date", waterfall.closed_won_at)):
        try:
            s = fn(q, grain)
            parts += ["", f"{name} — ${s.sum():,.0f} total",
                      s.sort_values(ascending=False).head(15).to_string(
                          float_format=lambda x: f"{x:,.0f}")]
        except Exception as e:
            parts += ["", f"{name}: unavailable ({type(e).__name__}: {str(e)[:100]})"]

    parts += ["", "These are the DERIVED side's inputs. Challenge one with the whatif "
                  "command. Method: docs/analysis/pipe-create-waterfall.md."]
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("derive", help="solve for the required pipe create")
    d.add_argument("--quarters", default="Q3 FY26")
    d.add_argument("--grain", default="Territory")
    d.add_argument("--as-of", dest="as_of")
    d.add_argument("--window-start", dest="window_start")
    d.add_argument("--window-end", dest="window_end")

    w = sub.add_parser("whatif", help="re-solve with one assumption replaced")
    w.add_argument("--key", required=True)
    w.add_argument("--assumption", required=True)
    w.add_argument("--value", type=float, required=True)
    w.add_argument("--quarters", default="Q3 FY26, Q4 FY26")

    s = sub.add_parser("assumptions", help="show the inputs, without solving")
    s.add_argument("--quarter")
    s.add_argument("--grain", default="Territory")
    s.add_argument("--window-start", dest="window_start")
    s.add_argument("--window-end", dest="window_end")

    a = ap.parse_args(argv)
    window = None
    if getattr(a, "window_start", None) and getattr(a, "window_end", None):
        window = (a.window_start, a.window_end)

    try:
        if a.cmd == "derive":
            df, notes, summary, run_dir = derive(a.quarters, a.grain, window, a.as_of)
            print(render_derive(df, notes, summary, run_dir, a.quarters))
        elif a.cmd == "whatif":
            print(what_if(a.key, a.assumption, a.value, a.quarters))
        else:
            print(assumptions(a.quarter, a.grain, window))
    except Exception as e:
        print(f"Failed: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
