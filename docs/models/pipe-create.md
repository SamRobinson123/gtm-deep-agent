# Pipe Create Model — `pipeline/pipe_create.py`

(originally extracted from the dashboard project's docs, now archived under
archive/docs/) This file is the model itself.

Read the Pipe Create invariants in the project root `CLAUDE.md` before changing
anything here. Load order for Pipe Create work:
`../README.md` -> this file -> [`../tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)
-> [`../tables/territory-mapping.md`](../tables/territory-mapping.md).

---

## Full source

Actual-vs-target pipe creation per week (slide 15). A new module rather than an
addition to `coverage.py`: every rule in that file (CloseDate scoping, boundary
anchoring, current-week ordering) is *inapplicable* here, and `coverage.py` is
PBI-reconciled and shouldn't be touched. Full source:

```python
import pandas as pd
from config import DATA, OUTPUT, QUARTER_START, QUARTER_END, geo_bucket, quarter_week, load_pipe_create_targets

Q_START, Q_END = pd.Timestamp(QUARTER_START), pd.Timestamp(QUARTER_END)

def _week_calendar(mx):
    """One row per calendar day of the quarter, tagged with its week-of-quarter (the
    same numbering coverage.py's source QuarterWeek uses — verified identical) and
    home month, plus whether that day has actually been observed yet (<= mx, the
    latest snapshot date in the feed)."""
    cal = pd.DataFrame({'date': pd.date_range(Q_START, Q_END)})
    cal['week']          = cal['date'].apply(lambda d: quarter_week(d, QUARTER_START))
    cal['month']         = cal['date'].dt.strftime('M%Y%m')
    cal['counted']       = cal['date'] <= mx
    cal['days_in_month'] = cal['date'].dt.days_in_month
    return cal

def _week_shares(cal):
    """Per-week metadata (start/end/days_in_week/days_counted) plus a week x month
    share table, day-weighted by days ACTUALLY OBSERVED (not the full week length).

    One rule handles all three week states: a fully-completed past week has
    days_counted == days_in_week, so its share equals the full-week share; the
    in-flight week's share is prorated down to just the days seen so far; a week
    that hasn't started yet has 0 observed days, so its share — and therefore its
    target_created/target_opps — comes out to 0, which is exactly what lets
    attainment collapse to null there with no extra branching.
    """
    meta = cal.groupby('week').agg(week_start=('date', 'min'), week_end=('date', 'max'),
                                    days_in_week=('date', 'size'),
                                    days_counted=('counted', 'sum')).to_dict('index')
    obs = cal[cal['counted']]
    days  = obs.groupby(['week', 'month']).size()
    denom = obs.groupby(['week', 'month'])['days_in_month'].first()
    share = (days / denom).unstack(fill_value=0.0) if len(obs) else pd.DataFrame()
    share = share.reindex(sorted(meta.keys()), fill_value=0.0)
    return meta, share

def _week_target(monthly, week, share):
    """Sum_month monthly[month] x share[week, month]. None only when the slice has
    no target row at all for any month (a team absent from Target_Monthly.csv,
    e.g. APAC Asia AGE/SEA) — a real but not-yet-elapsed week/month combo is 0, not
    None, per the day-weighted share above."""
    if monthly is None or monthly.isna().all():
        return None
    row  = share.loc[week] if week in share.index else pd.Series(0.0, index=monthly.index)
    vals = monthly.reindex(row.index).fillna(0.0)
    return float((vals * row).sum())

def _targets_by_grain(bts, pipe_m, opps_m):
    """Bottom-up Pipe-Create-$ and Opp-Count monthly targets rolled up through bts.

    Territory is grouped on BTS_Territory, NOT Bookings_Team_Static: coverage.py's
    territory_target dict is keyed by team and happens to work only because team ==
    territory for 26 of 29 rows; grouping by BTS_Territory here additionally rolls
    the 3-team APAC Asia territory up correctly. (Not changed in coverage.py itself
    — that file is PBI-reconciled and this quirk doesn't move its numbers.)
    """
    months = list(pipe_m.columns)
    b = bts[['Bookings_Team_Static', 'BTS_Territory', 'BTS_Region', 'Geo']].copy()
    for m in months:
        b[f'p_{m}'] = b['Bookings_Team_Static'].map(pipe_m[m])
        b[f'o_{m}'] = b['Bookings_Team_Static'].map(opps_m[m])
    pcols, ocols = [f'p_{m}' for m in months], [f'o_{m}' for m in months]

    def rollup(by):
        g = b.groupby(by)
        p = g[pcols].sum(min_count=1); p.columns = months
        o = g[ocols].sum(min_count=1); o.columns = months
        return p, o

    all_p, all_o = b[pcols].sum(min_count=1), b[ocols].sum(min_count=1)
    all_p.index = all_o.index = months
    return {
        'Territory': rollup('BTS_Territory'),
        'Region':     rollup('BTS_Region'),
        'Geo':        rollup('Geo'),
        'All':        (all_p, all_o),
    }

def _actuals(snap, bts):
    """One row per opp: its first-ever appearance in the daily snapshot feed, used as
    a proxy for when it entered tracked pipe.

    MIN(snapshot_date) is taken over the FULL frame — pre-quarter buffer included —
    and only THEN filtered to this quarter. Filtering to in-quarter rows before the
    min would credit every opp still alive on day 1 with a first_seen of day 1;
    the buffer is what lets week 1 read correctly (verified: without it, 328 opps
    that actually first appeared in the Jun 18-30 buffer window would resurrect as
    week-1 creates, overstating it by $22.47M / 328 opps).

    Deliberately NO CloseDate filter (the inverse of coverage.py's rule): only 141
    of the 514 W1-4 creates close in this quarter — the other 373 are next-quarter
    pipe, which is exactly what pipe creation is supposed to count. Deliberately NO
    stage filter: 37 opps arrive already closed ($908K, 2.7% of QTD) — pipe create
    counts entry into pipe regardless of what happened to it afterward. Deliberately
    no drop_duplicates: the snapshot feed is verified one row per Opp_Id per day.
    """
    first = snap.sort_values('snapshot_date').groupby('Opp_Id').first().reset_index()
    first = first[first['snapshot_date'] >= Q_START].copy()

    first['_team_key'] = first['Bookings_Team_static'].str.strip().str.lower()
    key = bts[['_team_key', 'Geo', 'BTS_Region', 'BTS_Territory']]
    first = first.merge(key, on='_team_key', how='left')
    for c in ('Geo', 'BTS_Region', 'BTS_Territory'):
        first[c] = first[c].fillna('Unassigned')
    first['week'] = first['snapshot_date'].apply(lambda d: quarter_week(d, QUARTER_START))
    return first

def _weekly_rows(frame, cal_meta, share, geo, region, territory, pipe_target, opp_target):
    """One pipe-create row per week 1..N — loops the full week calendar (not just
    weeks with an observed create), so a future week emits a target-only row with
    created/opps left null rather than silently missing."""
    rows = []
    by_week = dict(tuple(frame.groupby('week'))) if len(frame) else {}
    for w, m in cal_meta.items():
        has_data = m['days_counted'] > 0
        wk = by_week.get(w)
        if not has_data:
            created = opps = None
        else:
            created = float(wk['Cal_IACV'].sum()) if wk is not None else 0.0
            opps    = int(wk['Opp_Id'].nunique()) if wk is not None else 0
        asp = (created / opps) if opps else None

        tc = _week_target(pipe_target, w, share)
        to = _week_target(opp_target, w, share)
        ta = (tc / to) if (tc and to) else None

        rows.append({
            'week_of_quarter': w, 'week_start': m['week_start'], 'week_end': m['week_end'],
            'days_in_week': int(m['days_in_week']), 'days_counted': int(m['days_counted']),
            'Geo': geo, 'Region': region, 'Territory': territory,
            'created': created, 'opps': opps, 'asp': asp,
            'target_created': tc, 'target_opps': to, 'target_asp': ta,
            'att_created': (created / tc) if (created is not None and tc) else None,
            'att_opps':    (opps / to)    if (opps is not None and to)    else None,
            'att_asp':     (asp / ta)     if (asp is not None and ta)    else None,
        })
    return rows

def pipe_create():
    bts = pd.read_parquet(DATA / 'bts.parquet')
    bts['Geo'] = bts['BTS_RegionFamily'].apply(geo_bucket)
    bts['_team_key'] = bts['Bookings_Team_Static'].str.strip().str.lower()

    snap = pd.read_parquet(DATA / 'snapshot.parquet')
    snap['snapshot_date'] = pd.to_datetime(snap['snapshot_date'])
    mx = snap['snapshot_date'].max()

    pipe_m, opps_m = load_pipe_create_targets()
    grains = _targets_by_grain(bts, pipe_m, opps_m)
    first  = _actuals(snap, bts)
    cal_meta, share = _week_shares(_week_calendar(mx))

    def tgt(level, key):
        p, o = grains[level]
        if level == 'All':
            return p, o
        return (p.loc[key] if key in p.index else None), (o.loc[key] if key in o.index else None)

    # Iterate the org's own Geo/Region/Territory combinations (from bts), unioned
    # with whatever combos actually show up in `first` — unlike coverage.py's
    # weekly frame (which realistically touches every real territory in any given
    # 14-day window), a territory can easily have zero NEW pipe in a given week,
    # so grouping on `first` alone would silently drop it instead of showing a
    # target-only row.
    def combos(cols):
        return pd.concat([bts[cols], first[cols]], ignore_index=True).drop_duplicates()

    rows = []
    ap, ao = tgt('All', None)
    rows += _weekly_rows(first, cal_meta, share, 'All', None, None, ap, ao)
    for _, r in combos(['Geo']).iterrows():
        geo = r['Geo']
        grp = first[first.Geo == geo]
        p, o = tgt('Geo', geo)
        rows += _weekly_rows(grp, cal_meta, share, geo, None, None, p, o)
    for _, r in combos(['Geo', 'BTS_Region']).iterrows():
        geo, region = r['Geo'], r['BTS_Region']
        grp = first[(first.Geo == geo) & (first.BTS_Region == region)]
        p, o = tgt('Region', region)
        rows += _weekly_rows(grp, cal_meta, share, geo, region, None, p, o)
    for _, r in combos(['Geo', 'BTS_Region', 'BTS_Territory']).iterrows():
        geo, region, territory = r['Geo'], r['BTS_Region'], r['BTS_Territory']
        grp = first[(first.Geo == geo) & (first.BTS_Region == region) & (first.BTS_Territory == territory)]
        p, o = tgt('Territory', territory)
        rows += _weekly_rows(grp, cal_meta, share, geo, region, territory, p, o)

    df = pd.DataFrame(rows).sort_values(['Geo', 'Region', 'Territory', 'week_of_quarter'])
    OUTPUT.mkdir(exist_ok=True)
    df.to_parquet(OUTPUT / 'gtm_pipe_create.parquet', index=False)
    df.to_json(OUTPUT / 'gtm_pipe_create.json', orient='records', date_format='iso')

    qtd = df[(df.Geo == 'All') & (df.days_counted > 0)]
    print(f"Pipe create QTD: {qtd.opps.sum():.0f} opps / ${qtd.created.sum():,.0f} "
          f"vs target ${qtd.target_created.sum():,.0f}")

if __name__ == '__main__':
    pipe_create()
```
