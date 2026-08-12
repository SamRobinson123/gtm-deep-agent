"""The derivation assembly — one path, shared by every caller.

Demoted from `agent.tools._derive_frame` in v2 step 4. Synchronous now: the tool
handler was `async` because the SDK requires it, but the body never awaited
anything, and forcing a scratch script into an event loop for no reason is a
tax on the thing v2 exists to make easy.

    from pipeline.derive import derive_frame
    df, notes = derive_frame("Q3 FY26, Q4 FY26", as_of="2026-08-11")

ONE assembly path, deliberately. This function already drifted once: the derive
tool kept a private copy under a comment reading "keeping two copies is how they
drift", and when pre_q_slip() and slip_inflow() were added here, that copy went
on silently returning the old numbers. Every caller — CLI, what-if, UI — comes
through this function.

`notes` is not decoration. It carries the Pre Q slip rate, the slip inflow and
the untargeted-pipe exclusion; a caller that takes the frame and drops the notes
reports numbers with no idea what shaped them.
"""
from __future__ import annotations

import pandas as pd

from agent import targets, waterfall
from pipeline import config


def derive_frame(quarters: str, grain: str = "Territory", overrides=None,
                        as_of: str | None = None, window=None):
    """Assemble the derivation inputs and solve. Shared by the derive tool and the
    what-if, so an override is evaluated against exactly the same inputs as the
    baseline — two separate assembly paths would drift and make the delta a lie.

    Returns (frame, notes). Raises on anything that makes a number impossible.
    """
    qs = [targets.resolve_quarter(q.strip())
          for q in quarters.replace(";", ",").split(",") if q.strip()]
    sku = waterfall.load_sku(grain)
    book = {q: config._target_by_team("Bookings", q).sum(axis=1) for q in qs}
    as_of = as_of or str(pd.Timestamp.today().date())

    # Slip is measured on the SAME QUARTER a year earlier, per quarter being
    # solved — Q3 FY26 from Q3 FY25, Q4 FY26 from Q4 FY25. Slip is seasonal, so
    # applying one quarter's rate to every quarter imports the wrong shape.
    notes, existing = [], {}

    def slip_override(q, name):
        """A slip override for this quarter, applying to every key.

        The slip assumptions are consumed HERE, before derive_targets runs, not
        in its loop with the others — they shape the existing-pipe input rather
        than the solve. Resolved with the same _override_for() the solve uses, so
        the two accept identical shapes; a per-key slip override would need a
        Series and is not offered yet.
        """
        if not overrides:
            return None
        for key in ("*", "All"):
            v = waterfall._override_for(overrides, q, key).get(name)
            if v is not None:
                return v
        seen = {waterfall._override_for(overrides, q, k).get(name)
                for k in overrides if isinstance(overrides.get(k), dict)}
        seen.discard(None)
        return seen.pop() if len(seen) == 1 else None

    for i, q in enumerate(qs):
        h = waterfall.prior_year_quarter(q)

        # Pre-Q slip: only a FUTURE quarter has any. For the in-flight quarter it
        # has already happened and sits inside the observed balance, so the
        # function returns empty and the term is a no-op. Not special-casing.
        pq = None
        try:
            pq = waterfall.pre_q_slip(q, as_of, grain=grain)
            if len(pq):
                notes.append(
                    f"PRE-Q SLIP for {config.fq_label(q)}: {pq.attrs['pooled_rate']:.1%} "
                    f"at {pq.attrs['lead_days']}d lead, from {pq.attrs['measured_on']}")
        except Exception as e:
            notes.append(f"PRE-Q SLIP NOT INCLUDED for {config.fq_label(q)} "
                         f"— {type(e).__name__}: {e}")

        # Slip inflow: existing open pipe pushed out of EARLIER quarters in this
        # solve and landing here. Only quarters being solved contribute — a
        # quarter outside the solve has no measured open pipe to forward.
        inflow = None
        for earlier in qs[:i]:
            try:
                f = waterfall.slip_inflow(earlier, q, grain=grain, as_of=as_of)
                if not len(f):
                    continue
                inflow = f if inflow is None else inflow.add(f, fill_value=0.0)
                notes.append(
                    f"SLIP INFLOW {config.fq_label(earlier)} -> {config.fq_label(q)}: "
                    f"${f.sum():,.0f} ({f.attrs['destination_share']:.1%} of "
                    f"${f.attrs['slipping_value']:,.0f} slipping)")
            except Exception as e:
                notes.append(f"SLIP INFLOW NOT INCLUDED {config.fq_label(earlier)} -> "
                             f"{config.fq_label(q)} — {type(e).__name__}: {e}")

        # A challenged slip assumption replaces the measured one and is recorded,
        # so a what-if row can never be mistaken for a measured one.
        for name, val in (("pre_q_slip_rate", slip_override(q, "pre_q_slip_rate")),
                          ("in_q_slip_rate", slip_override(q, "in_q_slip_rate")),
                          ("slip_inflow", slip_override(q, "slip_inflow"))):
            if val is None:
                continue
            if name == "pre_q_slip_rate":
                pq = val
            elif name == "slip_inflow":
                inflow = val
            notes.append(f"OVERRIDE {config.fq_label(q)} {name} = {val} "
                         f"(replaces the measured value)")

        try:
            existing[q] = waterfall.existing_pipe_bookings(
                q, [h], sku=sku, grain=grain,
                slip_from_points={h: waterfall.slip_anchor(q, as_of, h)},
                window=window, slip_snapshot_file="snapshot_hist.parquet",
                pre_q_slip_rate=pq, slip_inflow_pipe=inflow,
                in_q_slip_rate=slip_override(q, "in_q_slip_rate"))
        except Exception as e:
            notes.append(f"SLIP NOT INCLUDED for {config.fq_label(q)} "
                         f"(needs {config.fq_label(h)}) — {type(e).__name__}: {e}")

        # Open pipe on a key the solve never visits is dropped, because the solve
        # iterates the bookings target's keys. This is USUALLY CORRECT: BTS_SQL
        # filters ActiveTeam='Active', so a disbanded team's residual pipe has no
        # target and rightly earns no create (confirmed with the model owner
        # 2026-08-11 — AMS Specialty and the DevOps teams are all inactive).
        #
        # Reported anyway, as information rather than a defect, because the same
        # path would silently swallow a LIVE team that is merely missing from the
        # mapping table — and a smaller existing-pipe term inflates required
        # create with nothing else in the output to reveal why.
        e_q = existing.get(q)
        if e_q is not None:
            orphan = e_q.index.difference(book[q].index)
            if len(orphan):
                pipe = e_q.attrs.get("open_pipe")
                lost = float(pipe.reindex(orphan).sum()) if pipe is not None else 0.0
                notes.append(
                    f"UNTARGETED PIPE EXCLUDED from {config.fq_label(q)}: "
                    f"{', '.join(map(str, orphan))} — ${lost:,.0f} of open pipe, "
                    f"${float(e_q.reindex(orphan).sum()):,.0f} of expected bookings. "
                    f"Expected for INACTIVE teams, which carry no target. Only "
                    f"investigate if a currently selling team appears here.")
    existing = existing or None

    won = None
    try:
        won = {q: waterfall.closed_won_at(q, grain=grain) for q in qs}
    except Exception as e:
        notes.append(f"CLOSED WON NOT INCLUDED — {type(e).__name__}: {e}")

    df = waterfall.derive_targets(sku, book, qs, grain=grain, window=window,
                                  existing_pipe_bookings=existing, closed_won=won,
                                  overrides=overrides)
    return waterfall.flag_outliers(df, grain), notes
