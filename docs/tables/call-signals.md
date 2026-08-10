# Call Signals — call_signals_features.csv

**When to load**: Any task using call sentiment data — signal analysis, model features, dashboard signal view.  
**Source**: `data/call_signals_features.csv`, derived from `data/call_summaries.csv` — see [`tables/call-transcripts.md`](call-transcripts.md) for the live Synapse pull, and `pipeline/extract_signals.py` for the keyword-match extraction  
**Join key**: `opp_id` → `[sfdc_trf].[opportunity_live].Id` and `[src].[sku_nacv_fact].Opportunity_id`  
**Used by**: [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md) (dashboard signal view + agent layer) — **not** the win-probability model

**Row grain**: One row per opportunity — `opp_id` is unique  
**Rows**: 3,359 opportunities (as of the last `pipeline/pull_call_summaries.py` run — recompute after every pull, this drifts with call volume)  
**All signal columns**: Binary integers (0 or 1) — no nulls  
**Join key**: `opp_id` → `[sfdc_trf].[opportunity_live].Id` · `[src].[sku_nacv_fact].Opportunity_id`  
**Related**: [`tables/opportunity.md`](opportunity.md) · [`tables/sku-nacv-fact.md`](sku-nacv-fact.md) · [`tables/call-transcripts.md`](call-transcripts.md) · [`models/win-probability-design.md`](../models/win-probability-design.md)

> Fully automated now — no manual CSV upload. `pipeline/run.py` runs
> `pull_call_summaries.py` (Synapse serverless pull → `data/call_summaries.csv`) then
> `extract_signals.py` (keyword extraction → `data/call_signals_features.csv`) before the
> model notebook, which reads both files.

---

## Columns

| Column | Type | Signal rate | Notes |
|--------|------|-------------|-------|
| `opp_id` | string | — | Join key — matches SFDC 18-char opportunity ID |
| `risk_building_own_tool` | int 0/1 | 0.8% | Customer mentioned building in-house / DIY alternative |
| `risk_competitor_present` | int 0/1 | 1.8% | Competitor explicitly named or discussed in call |
| `risk_needs_business_case` | int 0/1 | 8.5% | Customer requires formal business case before proceeding |
| `win_urgency_signal` | int 0/1 | 16.8% | Customer expressed urgency or hard deadline |
| `win_renewal_language` | int 0/1 | 16.3% | Renewal intent or language present in call |
| `win_active_negotiation` | int 0/1 | 3.9% | Active commercial negotiation underway |
| `win_champion_present` | int 0/1 | 0.5% | Internal champion identified and engaged |
| `win_planning_rollout` | int 0/1 | 5.7% | Customer discussing rollout / implementation planning |
| `win_stakeholder_aligned` | int 0/1 | 8.7% | Multiple stakeholders aligned on the decision |
| `win_explicit_commitment` | int 0/1 | 27.3% | Explicit verbal commitment or intent to proceed |

### Signal groups

**Risk signals** (3) — presence increases likelihood of loss or delay:
`risk_building_own_tool` · `risk_competitor_present` · `risk_needs_business_case`

**Win signals** (7) — presence increases likelihood of close:
`win_urgency_signal` · `win_renewal_language` · `win_active_negotiation` · `win_champion_present` · `win_planning_rollout` · `win_stakeholder_aligned` · `win_explicit_commitment`

### Coverage summary
- 356 opps (10.6%) have at least one risk signal
- 1,632 opps (48.6%) have at least one win signal
- 206 opps (6.1%) have both risk and win signals — mixed sentiment

---

## Loading in Python

```python
import pandas as pd

signals = pd.read_csv('data/call_signals_features.csv')
# signals has 3,359 rows, one per opp_id, all signals are 0/1 int
```

---

## Joining to opportunity data

### Join to sku_nacv_fact (product-level pipeline)

```python
df = df_sku.merge(signals, left_on='Opportunity_Id', right_on='opp_id', how='left')
# how='left' — keeps all pipeline rows, signals are NaN for opps not in CSV
# fill NaN signals with 0 (no signal detected)
signal_cols = [c for c in signals.columns if c != 'opp_id']
df[signal_cols] = df[signal_cols].fillna(0).astype(int)
```

### Join to opportunity_live (SQL)

When Synapse is available, join directly in SQL:

```sql
SELECT
    o.Id,
    o.Name,
    o.StageName,
    o.CloseDate,
    ISNULL(o.Total_ARR__c, 0)  AS Total_ARR,
    cs.risk_building_own_tool,
    cs.risk_competitor_present,
    cs.risk_needs_business_case,
    cs.win_urgency_signal,
    cs.win_champion_present,
    cs.win_explicit_commitment,
    cs.win_planning_rollout,
    cs.win_stakeholder_aligned
FROM [sfdc_trf].[opportunity_live] o
LEFT JOIN [call_signals].[call_signals_features] cs
    ON cs.opp_id = o.Id
WHERE o.IsDeleted = 0
  AND o.IsClosed  = 0
```

Until then, load in Python and merge on `opp_id = opportunity_live.Id`.

---

## Composite signal scores

Useful derived columns for reporting and model features:

```python
risk_cols = ['risk_building_own_tool', 'risk_competitor_present', 'risk_needs_business_case']
win_cols  = ['win_urgency_signal', 'win_renewal_language', 'win_active_negotiation',
             'win_champion_present', 'win_planning_rollout', 'win_stakeholder_aligned',
             'win_explicit_commitment']

df['risk_score'] = df[risk_cols].sum(axis=1)   # 0–3
df['win_score']  = df[win_cols].sum(axis=1)    # 0–7
df['net_signal'] = df['win_score'] - df['risk_score']  # positive = net win sentiment
```

---

## Call signals are NOT model features

Call signals are **not** inputs to the win-probability logistic regression in
[`models/win-probability-design.md`](../models/win-probability-design.md). They are consumed by a **separate agent layer**
that reads each opportunity's calls, documents them, and flags positive/negative
factors tied to the deal. Keep them out of the model's training and scoring
feature matrix.

Use them instead for:
- the dashboard call-sentiment view ([`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md))
- agent-generated flags and narrative per opportunity
- the composite `risk_score` / `win_score` / `net_signal` rollups (above)

They still left-join to opportunities on `opp_id` and are written alongside the
model output in `gtm_intel.parquet`, but the logistic regression never sees them.

**Coverage note**: Only 3,345 opps have signal data. Opps without call data get all
signals = 0 after the left-join fill — treat 0 as "no signal detected," not missing.

---

## Key notes for any task using this data

1. **Left join always** — not all opportunities have call signal data. Inner join drops opps silently.
2. **Fill NaN with 0 after join** — absence of a signal row means no signal detected, not unknown.
3. **`opp_id` format** — verify it matches the 18-char SFDC ID format in `opportunity_live.Id`. Some rows in the sample show 9-char IDs (`000BDA028`) — confirm with the signal extraction pipeline whether these are truncated or a different ID format.
4. **One row per opp** — this is already aggregated at the opp level, not the call level. Multiple calls per opp have been collapsed into these binary flags upstream.
---

## Handoff

- Call signals are **not** model features — the model layer is [`models/win-probability-design.md`](../models/win-probability-design.md), kept separate
- Building the full pipeline → load [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md)
- When Synapse is available → replace `pd.read_csv` with a SQL pull and update join pattern here
