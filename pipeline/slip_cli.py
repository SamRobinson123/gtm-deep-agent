"""Slip measurement — rate, destinations, Pre Q, forecast, create-date cohorts.

Demoted from `agent.tools.slip_analysis` in v2 step 4.

    python -m pipeline.slip_cli --quarter "Q3 FY26" --kind destinations

Read docs/analysis/slip.md for the METHOD; this gives the current NUMBER. Cite
both. The cohort mode carries its warning inline: create-date cohorts are a
different axis from the Pre Q / In Q timing split and must never be quoted as
those rates.
"""
from __future__ import annotations

import argparse

import pandas as pd

from agent import targets, waterfall
from pipeline import config


def slip_analysis(quarter=None, kind='rate', grain='Territory', as_of=None):
    kind = (kind or "rate").strip().lower()
    grain = grain or "Territory"
    as_of = as_of or str(pd.Timestamp.today().date())
    try:
        q = targets.resolve_quarter((quarter or "").strip() or None)
    except ValueError as e:
        raise

    HIST = "snapshot_hist.parquet"
    label = config.fq_label(q)
    # For a quarter in flight, the like-for-like historic anchor is the equivalent
    # point a year earlier, not that quarter's start — the population still open
    # mid-quarter is enriched in non-closers, so its rate is higher.
    prior = waterfall.prior_year_quarter(q)
    point = waterfall.slip_anchor(q, as_of, prior)

    try:
        if kind == "rate":
            g = waterfall.slip(prior, grain, from_point=point, snapshot_file=HIST)
            t = g[["starting_open_pipe", "won", "lost", "slipped", "held"]].sum()
            # Dollars and a rate in one frame: a single float_format renders
            # 0.639 as "1". Format the rate to text before printing.
            show = g.copy()
            show["slip_rate"] = show["slip_rate"].map(
                lambda x: "—" if pd.isna(x) else f"{x:.1%}")
            body = show.to_string(float_format=lambda x: f"{x:,.0f}")
            head = (f"SLIP RATE for {label}, measured on {config.fq_label(prior)} "
                    f"from {g.attrs['from_point']} ({g.attrs['days_remaining']}d left)\n"
                    f"  starting open pipe ${t.starting_open_pipe:,.0f}\n"
                    f"  slipped ${t.slipped:,.0f} = {t.slipped/t.starting_open_pipe:.1%}"
                    f"  |  won {t.won/t.starting_open_pipe:.1%}"
                    f"  lost {t.lost/t.starting_open_pipe:.1%}"
                    f"  held {t.held/t.starting_open_pipe:.1%}")

        elif kind == "destinations":
            d = waterfall.slip_destinations(prior, from_point=point, snapshot_file=HIST)
            body = "\n".join(
                f"  Q+{int(k)}: {v:6.1%}  ${d.attrs['dollars'][k]:>14,.0f}" for k, v in d.items())
            head = (f"WHERE {label}'s SLIP LANDS, from {config.fq_label(prior)} "
                    f"(anchor {d.attrs['from_point']})\n"
                    f"  ${d.attrs['slipped_value']:,.0f} slipped across "
                    f"{d.attrs['opps']} opps")

        elif kind == "pre_q":
            r = waterfall.pre_q_slip(q, as_of, grain=grain, snapshot_file=HIST)
            if not len(r):
                return (f"PRE Q SLIP for {label}: none — {r.attrs.get('reason')}. "
                           f"This is correct, not missing data: a quarter already "
                           f"under way has had its Pre Q slip, and it is already "
                           f"inside the observed open pipe.")
            head = (f"PRE Q SLIP for {label}: {r.attrs['pooled_rate']:.1%} at "
                    f"{r.attrs['lead_days']}d lead, measured on "
                    f"{r.attrs['measured_on']} read {r.attrs['read_at']}")
            body = r.to_string(float_format=lambda x: f"{x:.1%}")

        elif kind == "forecast":
            f = waterfall.slip_forecast(q, grain=grain, as_of=as_of, snapshot_file=HIST)
            head = f"SLIP FORECAST for {label}, from {config.fq_label(prior)}"
            body = f.to_string(float_format=lambda x: f"{x:,.0f}")

        elif kind == "cohort":
            g = waterfall.slip_by_cohort(prior, snapshot_file=HIST)
            head = (f"SLIP BY CREATE-DATE COHORT on {config.fq_label(prior)}\n"
                    f"  *** These are NOT the Pre Q / In Q slip rates. Those are a "
                    f"TIMING split and both act on existing open pipe. This splits "
                    f"by when the pipe was CREATED — a different axis. Never quote "
                    f"these as Pre Q / In Q. See docs/analysis/slip.md. ***\n"
                    f"  re-slip cohort observable here: "
                    f"{g.attrs['reslip_observable']}")
            body = g.to_string(float_format=lambda x: f"{x:,.3f}")
        else:
            return ("Unknown kind. Use rate, destinations, pre_q, forecast or cohort.")
    except waterfall.MissingData as e:
        return (f"Cannot measure slip: {e}")
    except Exception as e:
        return (f"Slip analysis failed: {type(e).__name__}: {e}")

    return (f"{head}\n\n{body}\n\n"
               f"Measured from data/{HIST}. Method and caveats: docs/analysis/slip.md.")

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quarter", help="e.g. 'Q3 FY26'")
    ap.add_argument("--kind", default="rate",
                    choices=["rate", "destinations", "pre_q", "forecast", "cohort"])
    ap.add_argument("--grain", default="Territory")
    ap.add_argument("--as-of", dest="as_of")
    a = ap.parse_args(argv)
    try:
        print(slip_analysis(a.quarter, a.kind, a.grain, a.as_of))
    except Exception as e:
        print(f"Failed: {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
