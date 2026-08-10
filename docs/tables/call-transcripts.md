# Call Transcripts — transcripts_lookup (Synapse serverless)

**When to load**: Pulling or refreshing raw call summaries; debugging `pull_call_summaries.py`; any task that needs the call-note text itself (not just the derived binary signals).
**Source**: `[transcripts_lookup].[Call_Review]` / `.[Call_Transcript]`, in Synapse's **serverless "Built-in" pool**, database `AIDatabase`.
**Pulled by**: `pipeline/pull_call_summaries.py` → `data/call_summaries.csv`
**Hands off to**: `pipeline/extract_signals.py` (keyword extraction) → [`tables/call-signals.md`](call-signals.md)

**Only `opp_id` + `summary` are pulled** — deliberately. `transcripts_lookup.Opportunity`
also carries `account_id`/`contact_id`/`employee_id`/`opp_stage`, but nothing downstream
uses them, and `opp_stage` in particular reflects stage *at call time*, not the opp's
current state — pulling it risks it getting used as a stage source instead of the
authoritative `StageName` in [`tables/opportunity.md`](opportunity.md). Don't join `Opportunity` back in
without a concrete need for one of those columns.

---

## Why this is a separate endpoint, not part of `pull.py`

Everything else in `pull.py` (`sku_nacv_fact`, the daily snapshot, territory mapping)
lives in the workspace's **dedicated SQL pool** — the database `SYNAPSE_CONN_STR` in
`.env` points at directly. `transcripts_lookup` lives in a different logical
database on the same Synapse workspace: the **serverless ("Built-in" / on-demand)
pool**, database `AIDatabase`. Same AAD identity, same `az login`, same token scope
(`https://database.windows.net/.default`) — only the SQL endpoint hostname and the
`Database=` value differ.

`pull_call_summaries.py` does not need a second secret in `.env` for this. It derives
the serverless connection string from `SYNAPSE_CONN_STR` at runtime:

```python
def _serverless_conn_str(database='AIDatabase'):
    cs = re.sub(r'(Server=(?:tcp:)?)([\w-]+)(\.sql\.azuresynapse\.net)',
                r'\1\2-ondemand\3', SYNAPSE_CONN_STR, flags=re.I)
    cs = re.sub(r'Database=[^;]+', f'Database={database}', cs, flags=re.I)
    return cs
```

i.e. `<workspace>.sql.azuresynapse.net` → `<workspace>-ondemand.sql.azuresynapse.net`,
and `Database=DedicatedSQLPool` → `Database=AIDatabase`. The AAD access token is
identical to `pull.py`'s (`pipeline/pull.py`'s `_token_struct()`, shared via import)
— serverless and dedicated pools are the same `database.windows.net` audience.

---

## Source tables

| Table | Alias | Grain |
|-------|-------|-------|
| `[transcripts_lookup].[Call_Review]` | `cr` | One row per reviewed call — carries `opp_id` |
| `[transcripts_lookup].[Call_Transcript]` | `ct` | One row per call transcript, keyed to `call_review_id`; carries the free-text `summary` |

## Pull query

```sql
SELECT
     cr.[opp_id]
    ,ct.[summary]
FROM [transcripts_lookup].[Call_Review] cr
INNER JOIN [transcripts_lookup].[Call_Transcript] ct ON cr.call_review_id = ct.call_review_id
```

`INNER JOIN` — this pull is deliberately scoped to reviewed calls that have a
transcript summary. Opps with no calls simply don't appear (the same behavior
`extract_signals.py` and the dashboard's left-join fill already assume downstream).

## Output — `data/call_summaries.csv`

**Row grain**: One row per call (an opp with N calls has N rows).
**Rows**: ~8,300 calls / ~3,360 distinct opps as of the last pull — both counts drift
with call volume, recompute rather than trust this number.

| Column | Notes |
|--------|-------|
| `opp_id` | Join key → `Opportunity_Id` elsewhere in this context (SFDC 18-char ID) |
| `summary` | Free-text call summary — the raw input `extract_signals.py` pattern-matches against |

---

## Refreshing this data

```bash
python pipeline/pull_call_summaries.py    # needs VPN — writes data/call_summaries.csv
python pipeline/extract_signals.py        # offline — writes data/call_signals_features.csv
```

Both run automatically as steps 2–3 of `python pipeline/run.py`. See
[`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md) → "Pipeline layout" for the full step order.

---

## Handoff

- Raw call text → `pipeline/extract_signals.py` keyword-matches this into the binary
  signal columns documented in [`tables/call-signals.md`](call-signals.md)
- The notebook (`notebooks/win_probability.ipynb`, Cell 6e/6f) also reads this file
  directly to attach `Call_Summaries` (list of raw strings) to each scored row —
  display-only, never a model feature
- This file has no further upstream dependencies — it is a pull, not a derived table
