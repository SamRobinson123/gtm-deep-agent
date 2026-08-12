# Pipe Create Model — weekly actual vs target

The weekly Pipe Create actual-vs-target model. The target-allocation half is
implemented in `agent/targets.py`, run via `python -m pipeline.targets_cli`
(or `from pipeline.targets_cli import published_targets` in a scratch script),
and verified by `pipeline/checks.py` (`run_all`) — this is exactly the output
shape `checks.py` covers. Full implementation: `agent/targets.py`; invariants
enforced by `tests/test_targets.py`. (Originally extracted from the dashboard
project's docs, now archived under `archive/docs/`.)

Read the Pipe Create invariants in the project root `CLAUDE.md` before changing
anything here. Load order for Pipe Create work:
`../README.md` -> this file -> [`../tables/target-monthly.md`](../tables/target-monthly.md)
-> [`../tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)
-> [`../tables/territory-mapping.md`](../tables/territory-mapping.md).

> **OPEN QUESTION (2026-08-13):** this doc specifies BOTH halves of the model —
> targets and actuals — but only the targets half is implemented in this repo
> (`agent/targets.py` states "What this module does NOT do: actuals"). The
> actuals process below (MIN(snapshot_date) over the buffered frame, the filter
> rules, attainment) remains the documented spec, with no implementing module.
> Whether it gets implemented here or stays spec-only is the operator's call.

---

## Targets — day-weighted weekly allocation

Targets come from `data/Target_Monthly.csv` through the mandatory strip-on-read
recipe — see [`../tables/target-monthly.md`](../tables/target-monthly.md).
Month columns are derived from the quarter start, never hardcoded (invariant 1;
`config.month_columns`). Q3 FY26 has **14 weeks, not 13**, and W1 and W14 are
partial (invariant 3).

The allocator builds one row per calendar day of the quarter, tagged with its
week-of-quarter and home month, plus whether that day has actually been
observed yet (`as_of`). From that calendar it computes a week × month share
table, day-weighted by days ACTUALLY OBSERVED — not the full week length
(`agent/targets.py::week_shares`):

```python
    obs = cal[cal["counted"]]
    if not len(obs):
        return meta, pd.DataFrame(index=sorted(meta))
    days = obs.groupby(["week", "month"]).size()
    denom = obs.groupby(["week", "month"])["days_in_month"].first()
    share = (days / denom).unstack(fill_value=0.0)
    share = share.reindex(sorted(meta.keys()), fill_value=0.0)
```

One rule handles all three week states (invariant 4): a completed past week has
`days_counted == days_in_week` so its share is the full-week share; the
in-flight week is prorated down to the days seen so far; a week that has not
started has 0 observed days, so its share — and therefore its target — is 0.
That is exactly what lets attainment collapse to null with no special-casing.
**Do not "fix" it.**

A week's target is then `sum_month monthly[month] × share[week, month]`
(`agent/targets.py::week_target`):

```python
    if monthly is None or monthly.isna().all():
        return None
    row = share.loc[week] if week in share.index else pd.Series(0.0, index=monthly.index)
    vals = monthly.reindex(row.index).fillna(0.0)
    return float((vals * row).sum())
```

**None vs zero matters.** `None` only when the slice has no target row at all
for any month — a team absent from `Target_Monthly.csv`, e.g. APAC Asia
AGE/SEA (invariant 9) — and must never be rendered as 0% attainment. A real
but not-yet-elapsed week/month combination is `0.0`: target exists, none
allocated to this week yet.

**ASP is derived, never read as a row** (invariant 2): `target_asp = tc / to`
at matching grain, null when either side is missing. Every opp-count or ASP
figure carries the invariant-10 caveat — the Opportunities target counts
opp-product-lines, not distinct opps — and `targets_cli` emits that caveat in
code, not by prompt.

## Grain rollup

Territory rolls up on `BTS_Territory`, NOT `Bookings_Team_Static` (invariant
7): team == territory for 26 of 29 rows, and grouping by `BTS_Territory`
additionally rolls the 3-team APAC Asia territory up correctly.

> **OPEN QUESTION (2026-08-13):** the implementation diverges here.
> `agent/targets.py` cannot reach the live BTS mapping offline, so it rolls
> Region and Geo up using `Target_Monthly.csv`'s own Geo / GeoTerritory
> columns instead (its module docstring's GRAIN CAVEAT). Territory-grain
> figures agree (same source rows); Region/Geo grain MAY DIFFER if the CSV
> hierarchy and the BTS mapping disagree, and every such run carries the
> `offline-grain-rollup-not-bts` warning. Which hierarchy Region/Geo targets
> should use — or whether the BTS mapping should be cached for offline rollup
> — is unresolved. Until adjudicated, treat the warning as load-bearing.

## Actuals — the documented process (spec only; see OPEN QUESTION above)

One row per opp: its first-ever appearance in the daily snapshot feed, used as
a proxy for when it entered tracked pipe.

**`MIN(snapshot_date)` is taken over the FULL frame — pre-quarter buffer
included — and only THEN filtered to this quarter** (invariant 5). Filtering
to in-quarter rows before the min would credit every opp still alive on day 1
with a first_seen of day 1; the buffer is what lets week 1 read correctly
(verified: without it, 328 opps that actually first appeared in the Jun 18-30
buffer window would resurrect as week-1 creates, overstating it by $22.47M /
328 opps).

Deliberately **NO CloseDate filter** (invariant 6): only 141 of the 514 W1-4
creates close in this quarter — the other 373 are next-quarter pipe, which is
exactly what pipe creation is supposed to count. Deliberately **NO stage
filter**: 37 opps arrive already closed ($908K, 2.7% of QTD) — pipe create
counts entry into pipe regardless of what happened to it afterward.
Deliberately **no drop_duplicates**: the snapshot feed is verified one row per
Opp_Id per day. (`checks.opp_counted_once` polices the first-seen dedup on any
actuals frame.)

Unmapped teams fall back to `'Unassigned'` rather than being dropped. The
weekly frame loops the full week calendar — not just weeks with an observed
create — so a future week emits a target-only row with `created`/`opps` left
null rather than silently missing, and the grain iteration unions the org's
own Geo/Region/Territory combinations with whatever shows up in the actuals,
so a territory with zero new pipe in a week still shows its target-only row.

## Running and verifying

```bash
python -m pipeline.targets_cli --grain Territory --quarter "Q3 FY26"
```

Every invocation writes an immutable lineage run (`pipe_create_targets.csv` +
manifest) — a figure without lineage cannot be defended later. Verify with
`pipeline.checks.run_all(df, quarter_total=total["pipe_target"])` before
reporting; the regression anchor is Q3 FY26 Pipeline = **$201,789,918**
(`tests/test_targets.py`). These are PUBLISHED figures — an artifact of a
prior planning cycle. What the target *would be* given current data is the
DERIVED side (`pipeline/waterfall_cli.py`,
[`../analysis/pipe-create-waterfall.md`](../analysis/pipe-create-waterfall.md));
reporting one as the other is the mistake this codebase is arranged to prevent.
