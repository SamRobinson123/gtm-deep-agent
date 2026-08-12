"""PUBLISHED Pipe Create targets — read from Target_Monthly.csv, never derived.

Demoted from `agent.tools.pipe_create_targets` in v2 step 4. Same logic, same
lineage, same caveat slugs; reachable now as

    python -m pipeline.targets_cli --grain Territory --quarter "Q3 FY26"

or, from a scratch script, `from pipeline.targets_cli import published_targets`.

The distinction this module owns: these are PUBLISHED figures — an artifact of a
prior planning cycle, read from the CSV. What the target *would be* given current
data is the DERIVED side, in pipeline/waterfall_cli.py. Reporting one as the
other is the mistake this whole codebase is arranged to prevent.
"""
from __future__ import annotations

import argparse

from agent import lineage, targets
from pipeline import config

GRAINS = ("All", "Geo", "Region", "Territory")


def published_targets(grain="All", key=None, as_of=None, quarter=None):
    """Day-weighted weekly target allocation. Returns (frame, totals, run_dir).

    Writes an immutable run, exactly as the tool did — a figure without lineage
    cannot be defended later.
    """
    if grain not in GRAINS:
        raise ValueError(f"Unknown grain {grain!r}. Use one of {', '.join(GRAINS)}.")

    # Any quarter whose months exist in the CSV is computable. QUARTER_STARTS[0]
    # governs the pre-quarter buffer for ACTUALS; it does not limit target reads.
    qs = targets.resolve_quarter(quarter)
    df = targets.weekly_target_rows(grain=grain, key=key, as_of=as_of, quarter_start=qs)
    total = targets.quarter_total(qs)

    with lineage.Run(quarter_start=qs) as run:
        run.add_input(config.TARGET_MONTHLY_CSV)
        out = run.dir / "pipe_create_targets.csv"
        df.to_csv(out, index=False)
        run.add_output(out, rows=len(df))
        run.headline(
            grain=grain, key=key or "All",
            quarter_pipe_target=total["pipe_target"],
            quarter_opp_target=total["opp_target"],
            quarter_asp=total["asp"],
            weeks=len(df),
        )
        # Emitted by code, not by prompt — CLAUDE.md requires this caveat on every
        # opp-count and ASP figure, and a prompt instruction degrades over a session.
        run.caveat("invariant-10-opportunities-unit")
        if grain in ("Region", "Geo"):
            run.warn("offline-grain-rollup-not-bts")
        run_dir = run.dir

    return df, total, run_dir


def render(df, total, run_dir, grain, key=None):
    """The text the tool used to return. Kept so the CLI and a scratch script
    report identically, caveats included."""
    return (
        f"Pipe Create TARGETS — {total['quarter']}, grain={grain}"
        + (f", key={key}" if key else "")
        + f"\nQuarter target: ${total['pipe_target']:,.0f} | "
          f"{total['opp_target']:,.0f} opps | ASP ${total['asp']:,.0f}\n\n"
        + df.to_string(index=False)
        + "\n\nCAVEAT (invariant 10): the Opportunities target counts opp-product-lines, "
          "not distinct opps. Opp-count and ASP figures are provisional; dollars are trustworthy."
        + (f"\nWARNING: Region/Geo rollup uses Target_Monthly.csv's own hierarchy, not the BTS "
           f"mapping (invariant 7). May differ from pipe_create.py." if grain in ("Region", "Geo") else "")
        + f"\n\nRun stored: {run_dir}"
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--grain", default="All", choices=GRAINS)
    ap.add_argument("--key", help="a single Geo/Region/Territory")
    ap.add_argument("--quarter", help="e.g. 'Q3 FY26' or '2026-07-01'")
    ap.add_argument("--as-of", dest="as_of", help="YYYY-MM-DD; defaults to today")
    a = ap.parse_args(argv)

    try:
        df, total, run_dir = published_targets(a.grain, a.key, a.as_of, a.quarter)
    except Exception as e:
        print(f"Failed: {type(e).__name__}: {e}")
        return 1
    print(render(df, total, run_dir, a.grain, a.key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
