# Call Transcripts — transcripts_lookup (Synapse serverless)

**When to load**: Pulling or refreshing raw call summaries; any task that needs the call-note text itself.
**Source**: `[transcripts_lookup].[Call_Review]` / `.[Call_Transcript]`, in Synapse's **serverless "Built-in" pool**, database `AIDatabase`.
**Pulled by**: ad-hoc read through the agent's `query` tool (the pull query below) — there is no local refresh script in this repo.

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

No second secret in `.env` is needed for this. The serverless connection string
derives from `SYNAPSE_CONN_STR` at runtime:

```python
def _serverless_conn_str(database='AIDatabase'):
    cs = re.sub(r'(Server=(?:tcp:)?)([\w-]+)(\.sql\.azuresynapse\.net)',
                r'\1\2-ondemand\3', SYNAPSE_CONN_STR, flags=re.I)
    cs = re.sub(r'Database=[^;]+', f'Database={database}', cs, flags=re.I)
    return cs
```

i.e. `<workspace>.sql.azuresynapse.net` → `<workspace>-ondemand.sql.azuresynapse.net`,
and `Database=DedicatedSQLPool` → `Database=AIDatabase`. The AAD access token is
identical to `pull.py`'s (`pipeline/pull.py`'s `_token()` / `get_conn()`) —
serverless and dedicated pools are the same `database.windows.net` audience.

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
transcript summary. Opps with no calls simply don't appear.

## Result shape

**Row grain**: One row per call (an opp with N calls has N rows).
**Rows**: ~8,300 calls / ~3,360 distinct opps as of the last pull — both counts drift
with call volume, recompute rather than trust this number.

| Column | Notes |
|--------|-------|
| `opp_id` | Join key → `Opportunity_Id` elsewhere in this context (SFDC 18-char ID) |
| `summary` | Free-text call summary (signal extraction belonged to the dashboard project — archived under `archive/docs/`) |

---

## Refreshing this data

Refresh is an ad-hoc read through the agent's `query` tool against the
serverless pool (the pull query above), subject to the usual approval; there
is no local refresh script.

---

## Handoff

- This data has no further upstream dependencies — it is a pull, not a derived table
