# Transcripts Lookup — dimension tables (Opportunity · Employee · Call_Review · Account)

**When to load**: You need to link a call back to its opportunity, account owner (employee), or account *within the transcript schema itself* — e.g. "which AE ran this call", "how many calls per account", building a call-level bridge. For the raw call **text** (`summary`) load [`tables/call-transcripts.md`](call-transcripts.md) instead; for anything about the SFDC pipeline (real stage, ARR, geo) go to [`tables/opportunity.md`](opportunity.md).

**Source**: `[transcripts_lookup].[Opportunity]` / `.[Employee]` / `.[Call_Review]` / `.[Account]`, in Synapse's **serverless "Built-in" (on-demand) pool**, database `AIDatabase` — *not* the dedicated pool `SYNAPSE_CONN_STR` points at. Same AAD identity / `az login` / token scope; only the host (`<workspace>-ondemand.sql.azuresynapse.net`) and `Database=AIDatabase` differ. See [`tables/call-transcripts.md`](call-transcripts.md) → "Why this is a separate endpoint" for the connection-string derivation.

**Related**: [`tables/call-transcripts.md`](call-transcripts.md) (the `Call_Review`→`Call_Transcript` pull that produces `summary`) · [`tables/opportunity.md`](opportunity.md) (the authoritative SFDC opp)
> **Physical shape**: these are serverless views/external tables over the lake. **Every column is `nvarchar(max)`, every column is nullable, and there are no primary keys, foreign keys, or column statistics.** Types below are *semantic* (what the value means), not declared SQL types. Because there are no stats, `JOIN`/`LEN()`/`GROUP BY` on these run slow — expect multi-minute serverless queries on the full tables.

---

## The four tables at a glance

| Table | Alias | Grain | Rows (last pull) | Natural key |
|-------|-------|-------|------------------|-------------|
| `[transcripts_lookup].[Call_Review]` | `cr` | One row per reviewed call | 8,287 | `call_review_id` (unique) |
| `[transcripts_lookup].[Opportunity]` | `xo` | One row per opportunity | 3,370 | `opp_id` (unique) |
| `[transcripts_lookup].[Account]` | `ta` | One row per account | 1,726 | `account_id` (unique) |
| `[transcripts_lookup].[Employee]` | `te` | One row per employee | 574 | `employee_id` (unique) |

`Call_Review` is the **fact/bridge**; the other three are **dimensions** hanging off it via `Opportunity`. Row counts drift with call volume — recompute, don't trust these numbers.

---

## `Call_Review` (`cr`) — the call bridge

One row per Clari-reviewed call. Carries the `opp_id` that ties a call to everything else, and the `call_review_id` that ties it to the transcript text.

| Column | Semantic | Notes |
|--------|----------|-------|
| `call_review_id` | string, unique | Natural key. Joins to `[transcripts_lookup].[Call_Transcript].call_review_id` for the `summary` text (see [`tables/call-transcripts.md`](call-transcripts.md)) |
| `opp_id` | string | FK → `Opportunity.opp_id` and → SFDC opp elsewhere. **See ID-format caveat below** |
| `dif_load_date` | ISO-8601 timestamp string | ETL load timestamp (data-lake ingest), *not* the call date — do not use as call recency |
| `review_page_url` | URL | Clari call page, e.g. `https://copilot.clari.com/call/<uuid>` — the human link to the recording/review |
| `stage_before_call` | string | Deal stage *at call time*. Free-text, mixed vocabulary, frequently blank. **Point-in-time — never authoritative stage** |

---

## `Opportunity` (`xo`) — the transcript-side opp dimension

> Alias is `xo`, **not `to`** — `TO` is a reserved T-SQL keyword and `[Opportunity] to` fails with "Incorrect syntax near 'to'".

One row per opp (3,370 unique). This is the hub: it connects a call's `opp_id` to its account, owner, and contact. It is **not** the SFDC opportunity — it is a thin lookup and its `opp_stage` is stale.

| Column | Semantic | Notes |
|--------|----------|-------|
| `opp_id` | string, unique | Natural key. → `Account.account_id` world via `account_id` below; → SFDC `Opportunity_Id` / `opportunity_live.Id` **when it's an 18-char SFDC ID** (see caveat) |
| `account_id` | string | FK → `Account.account_id`. 100% match to `Account` |
| `employee_id` | string | FK → `Employee.employee_id` (the AE/owner). 100% match to `Employee` |
| `contact_id` | string, **~13.6% null** | Prospect contact. **No `Contact` table exists in this schema** — this is a dangling reference; you can't resolve it here |
| `opp_stage` | string | Stage at extract time. **Mixed vocabulary and often blank — never use as stage.** See caveat |

---

## `Account` (`ta`) — account dimension (anonymized)

One row per account (1,726 unique). **Both columns; `account_name` carries no real information.**

| Column | Semantic | Notes |
|--------|----------|-------|
| `account_id` | string, unique | Natural key. SFDC 18-char account ID (`0018c...`) |
| `account_name` | string | **Synthetic placeholder — literally `'Account_' + account_id` for all 1,726 rows.** The real customer name is *not* in this schema. To get a real account name, join `account_id` out to the SFDC account tables |

---

## `Employee` (`te`) — employee/owner dimension

One row per employee (574 unique). The one table here with a partially-real attribute (`employee_email`).

| Column | Semantic | Notes |
|--------|----------|-------|
| `employee_id` | string, unique | Natural key — short hex hash (e.g. `EF76779AC`), the *only* ID style used for employees here |
| `employee_email` | string | Appears real, e.g. `e.raksasouk@tricentis.com` — usable to resolve the AE if needed |
| `employee_name` | string | Initials-anonymized, e.g. `E Raksasouk` (first-initial + last name) — display only |

---

## Join map

```
Call_Transcript.summary                       (tables/call-transcripts.md — the text)
        ▲ call_review_id
        │
   Call_Review (cr)  ──opp_id──►  Opportunity (xo)  ──account_id──►  Account (ta)
   1 row / call        (bridge)     1 row / opp          │            1 row / acct
                                                         ├─employee_id─►  Employee (te)
                                                         │                 1 row / emp
                                                         └─contact_id──►  (no Contact table)
```

Ready-to-run bridge (call → owner email → placeholder account), serverless pool:

```sql
SELECT  cr.call_review_id, cr.opp_id, cr.review_page_url,
        te.employee_email  AS owner_email,
        ta.account_id
FROM        [transcripts_lookup].[Call_Review] cr
INNER JOIN  [transcripts_lookup].[Opportunity] xo ON cr.opp_id      = xo.opp_id
LEFT  JOIN  [transcripts_lookup].[Employee]    te ON xo.employee_id = te.employee_id
LEFT  JOIN  [transcripts_lookup].[Account]     ta ON xo.account_id  = ta.account_id
```

`INNER JOIN` on `Opportunity` because every `Call_Review.opp_id` matches (100%); `LEFT JOIN` the dimensions defensively even though they also currently match 100%. (Alias is `xo` — `to` is a reserved keyword.)

---

## Data-quality caveats — read before trusting any join out to SFDC

1. **Two `opp_id` worlds — ~23% won't leave this schema.** Of the 3,370 opps, **2,591 carry a real 18-char SFDC ID** (`0068c...`) and **779 carry a short hashed ID** (e.g. `C27BCA8A0`). The hashed ones join fine *inside* `transcripts_lookup` (Call_Review↔Opportunity↔Account↔Employee are all internally 100%), but they **do not join to `opportunity_live` / `sku_nacv_fact` / the daily snapshot** — those live in the SFDC 18-char world. Filter `WHERE LEN(opp_id) = 18` when you intend to bridge out to the pipeline tables. This drops **~23% of opps** but only **~9.4% of calls** (779 of 8,287 — hashed opps have fewer calls each; measured 2026-07-29, see § Proofs).

2. **The 779 hashed opps are also the blank-stage opps.** Exactly 779 rows have a blank `opp_stage` — the same population as the hashed IDs. So the anonymized opps carry no stage at all.

3. **`opp_stage` / `stage_before_call` are unusable as stage.** Point-in-time (stage *at call/extract time*), frequently blank, and they mix two naming conventions in the same column — legacy `Stage 2 - In Discussion` / `Stage 5 - Closed Won` alongside current `2 - Qualification Status` / `1 - Discovery`. The authoritative outcome is `StageName` in [`tables/opportunity.md`](opportunity.md); this is exactly why the summaries pull (see [`tables/call-transcripts.md`](call-transcripts.md)) deliberately excludes `opp_stage`.

4. **`account_name` is fake.** 100% synthetic (`'Account_' + account_id`). Never surface it as a customer name — resolve `account_id` against the SFDC account tables for the real name.

5. **`contact_id` dangles.** ~13.6% null, and there is no `Contact` table in `transcripts_lookup` to resolve the non-null ones.

6. **`dif_load_date` is ETL, not call date.** It's the lake ingest timestamp. There is no true call-date column in these four tables.

---

## Proofs & checks — validating the call-context query

The call-context pull (call `summary` + `call_time` joined to each opp's stage, geo,
dates, NACV) is a **cross-pool, call-grain merge**, which has two classic failure modes.
A 16-check validation script caught both (the script belonged to the dashboard project
and is not in this repo; re-validating means recomposing these checks in a scratch
script against the `query` tool). Its check design and last verified results follow —
the findings are real knowledge and stand on their own.

**Severity model**: a hard-invariant **FAIL** breaks the merge; **WARN** = a real
data-quality gap to filter/clamp; **INFO** = a number worth knowing.

### What it guards against

1. **NACV inflation / over-indexing.** The merge is one row per *call*, so an opp with N
   calls appears N times. Summing NACV on the merged frame double-counts — **always
   `drop_duplicates('opp_id')` before any NACV/opp-count aggregation.** A fan-out join
   (non-unique dedicated-side key) would inflate further *and* invent phantom calls.
2. **Hallucinated opps / calls.** Orphan `opp_id`s that aren't in `opportunity_live`, or a
   join that multiplies the call rows.

### The checks

| Group | Check | Type |
|-------|-------|------|
| Grain integrity | `call_review_id` unique in the call pull | invariant |
| | `len(merged) == len(calls)` — the dedicated join added **zero** rows | invariant |
| | `snap_latest` is exactly 1 row/opp (`ROW_NUMBER() rn=1` worked) | invariant |
| | Every 18-char call `opp_id` exists in `opportunity_live` | WARN (filter orphans) |
| NACV | Deduped opp-grain NACV **== source** `opportunity_live` sum | invariant |
| | Inflation factor if summed call-grain (must dedupe) | INFO |
| | Top-opp NACV: call-grain vs true | INFO |
| | `sku_nacv_fact` summed-NACV vs `NACV__c` correlation | INFO |
| Value sanity | `call_time` all parseable, none in the future | invariant |
| | `Stage_Age`, `S1_Age` non-negative | invariant |
| | Stage start dates monotonic (`Stage_i <= Stage_i+1`) | WARN (clamp) |
| | Calls dated >30d before opp `CreatedDate` | INFO |
| Coverage | Calls dropped by `LEN(opp_id)=18` filter | INFO |
| | Distinct joinable opps vs opps-with-calls | INFO |

### Observed results — run 2026-07-29 (8 PASS · 0 FAIL · 2 WARN · 6 INFO)

- **NACV is not inflated *if you dedupe*.** Deduped opp-grain total reconciles to source
  **exactly** ($79,093,129 == $79,093,129). But summing on the raw call-grain frame gives
  **$245,945,523 — 3.11× too high**, and the top opp over-indexes 2.85× ($8.2M vs true
  $2.88M). This is the single most important gotcha: **dedupe to opp before summing money.**
- **No invented calls / no fan-out.** 7,508 calls in → 7,508 rows out; `call_review_id` all
  unique; snapshot cleanly deduped to 2,589 opps.
- **WARN — 10 orphan call rows (0.13%)**: their 18-char `opp_id` isn't in `opportunity_live`
  (deleted / not in the SFDC extract). The pull `LEFT JOIN`s, so they appear with null opp
  fields — **`dropna(subset=['Opportunity_Id'])`** them (or investigate) before reporting.
- **WARN — 1 opp with non-monotonic stage dates**: a source stage-entry date is out of order,
  so its `days_in_stage_i` can go negative — **clamp with `.clip(lower=0)`**.
- **INFO — coverage**: the hashed-ID filter drops **779 of 8,287 calls (9.4%)** — note this is
  9.4% of *calls* but ~23% of *opps* (§ caveat 1), because hashed opps have fewer calls each.
  607 calls (~8%) are dated >30d before their opp's `CreatedDate` (pre-opp discovery calls
  attached retroactively, or `CreatedDate` quirks) — a soft anomaly, not a defect.
- **INFO — `sku` vs `live` NACV** correlate 0.741 across 1,169 opps; the definitions differ
  (product-grain summed `NACV_USD` vs opp `NACV__c`), so treat `sku` NACV as a cross-check,
  not a second source of truth. Also normalize `CurrencyIsoCode` to USD before cross-geo sums.

---

## Handoff

- Validating / regression-testing the call-context query → this § Proofs (the original validation script is not in this repo — recompose the checks in a scratch script if re-validating)
- Need the call **text** (`summary`) → [`tables/call-transcripts.md`](call-transcripts.md) (`Call_Review` ⋈ `Call_Transcript`, the pull query there)
- Need real stage / ARR / geo for these opps → filter to 18-char `opp_id`, then join out to [`tables/opportunity.md`](opportunity.md) (and [`tables/territory-mapping.md`](territory-mapping.md) for geo)
- Building call-derived model features → (originally extracted from the dashboard project's docs, now archived under archive/docs/); **nothing in these four dimension tables is a model feature** (esp. not `opp_stage`)
- Refreshing / debugging the serverless connection → the connection note in [`tables/call-transcripts.md`](call-transcripts.md)
