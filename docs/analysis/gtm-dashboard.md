# GTM Intelligence Dashboard — Context

**When to load**: Building the GTM intelligence dashboard or running the full pipeline. This is the integration layer — it ties all other files together.  
**Requires ALL of**:
- [`sql/conventions.md`](../sql/conventions.md) — SQL rules
- [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) — product pipeline features
- [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) — snapshot + coverage + age features  
- [`tables/territory-mapping.md`](../tables/territory-mapping.md) — geo join
- [`tables/call-transcripts.md`](../tables/call-transcripts.md) — raw call summaries pull (serverless Synapse)
- [`tables/call-signals.md`](../tables/call-signals.md) — sentiment signals (derived from call-transcripts)
- [`models/win-probability-design.md`](../models/win-probability-design.md) — model design
- [`models/implementation.md`](../models/implementation.md) — model code
- [`analysis/coverage-curve.md`](coverage-curve.md) — coverage mechanics  

**This file contains**: Output schema · pipeline layout (pull script → model notebook → coverage script → pipe-create script → HTML dashboard) · the model notebook cells · Dashboard view specs · How the four layers connect.

**What this builds**: A single dashboard and runnable pipeline that ties together four data layers:
1. **Coverage** — where the quarter stands (open pipe, booked, LTB, coverage × WoW), across every quarter in `config.QUARTERS`, plus a Pre-Q/1-Week-Ago/1-Day-Ago/Current timepoint comparison (`gtm_timepoints.json`)
2. **Pipe create** — how much NEW pipe was created per week vs target (`gtm_pipe_create.json`)
3. **Win probability** — logistic regression score per open opportunity
4. **Call sentiment** — risk and win signals extracted from call transcripts

**Read these first**: [`analysis/coverage-curve.md`](coverage-curve.md) · [`models/win-probability-design.md`](../models/win-probability-design.md) · [`models/implementation.md`](../models/implementation.md) · [`tables/call-signals.md`](../tables/call-signals.md) · [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) · [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)

---

## Core principle

Simplest code, fewest lines. The pipeline has six parts:
1. **`pipeline/pull.py`** — pulls Synapse (dedicated pool) into cached parquets: `sku_nacv_fact` + snapshot + ages + bts (needs VPN).
2. **`pipeline/pull_call_summaries.py`** — pulls Synapse's **serverless "Built-in" pool**
   (`AIDatabase.transcripts_lookup` — a separate endpoint from step 1, see
   [`tables/call-transcripts.md`](../tables/call-transcripts.md)) → `data/call_summaries.csv` (needs VPN).
3. **`pipeline/extract_signals.py`** — keyword-matches `data/call_summaries.csv` into the
   per-opp binary signal CSV → `data/call_signals_features.csv` (offline, no VPN needed
   once step 2 has run).
4. **`notebooks/win_probability.ipynb`** — the model, in Jupyter with a markdown cell
   per block. Trains + validates the logistic regression, **pickles the fitted model**,
   scores the open pipeline (joining all three layers), and writes
   `output/gtm_intel.parquet` **and** `output/gtm_intel.json`.
5. **`pipeline/coverage.py`** — builds the weekly coverage curve **and the timepoint
   comparison**, for every quarter in `config.QUARTERS` → `gtm_coverage`/`gtm_timepoints`
   parquet + JSON.
6. **`pipeline/pipe_create.py`** — actual-vs-target pipe creation per week →
   `gtm_pipe_create.parquet` + `.json`.

The HTML dashboard reads the JSON exports (`gtm_intel.json`, `gtm_coverage.json`,
`gtm_timepoints.json`, `gtm_pipe_create.json`) — it never calls Synapse and never loads
a pickle directly (a pickle is Python-only, so the notebook also exports JSON for the
browser). The model itself stays in the notebook.

---

## Output schema — `output/gtm_intel.parquet` (+ `gtm_intel.json`)

One row per open opportunity **SKU** (product line) — this is the intended grain, not one row per opp. Products have distinct win rates, so each SKU is scored on its own feature mix and carries its own `Product_NACV`. A multi-product opp therefore appears as several rows, one per product. The notebook writes this as parquet (Python re-use) and `gtm_intel.json` (what the HTML dashboard reads).

| Column | Source | Notes |
|--------|--------|-------|
| `Opportunity_Id` | sku_nacv_fact | SFDC 18-char ID (join key; no name is carried) |
| `CloseDate` | sku_nacv_fact | Expected close date (`Opp_Closed_Date`) |
| `Geo` | territory mapping | AMS / EMEA / APAC / Public Sector |
| `Region` | territory mapping | BTS_Region |
| `Territory` | territory mapping | BTS_Territory |
| `Deal_Type` | sku_nacv_fact (mapped) | New Customer / Expansion / Upsell / Professional services |
| `Source` | sku_nacv_fact (mapped) | Sales Sourced / BDR / Marketing Sourced / Partner Sourced |
| `Segment` | sku_nacv_fact | Tier 1 / Tier 2 / Tier 3 |
| `Product` | sku_nacv_fact (mapped) | Tosca / qTest / Testim / etc. |
| `Product_NACV` | sku_nacv_fact | `NACV_USD − Uplift_USD` |
| `Stage` | sku_nacv_fact (mapped) | Open / Closed Won / Closed / Other (bucket) — see [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) |
| `Raw_Stage` | sku_nacv_fact | Actual stage name (`StageName`) |
| `Stage_Age` | snapshot | Days in current stage |
| `S1_Age` | snapshot | Days since stage 1 entry |
| `NextStep_Age` | snapshot | Days since next steps last updated |
| `snapshot_date` | snapshot | Latest snapshot date for this opp |
| `open_pipe` | snapshot | `Cal_IACV` where `Stage_Pipe_Category` in `('Early Stage', 'Late Stage')` |
| `Win_Prob` | model output | Logistic regression score 0–1 |
| `Win_Factors` | model output | List of up to 3 short strings — the transformed features with the largest *positive* contribution to this row's logit (e.g. `"Segment: Tier 1 (+27%)"`), each carrying that specific opportunity's own odds-ratio effect expressed as a **% change in odds** (`(exp(coefficient × this row's value) − 1) × 100`, exact — see notebook Cell 6d below). Output-only, never fed back into the model. |
| `Risk_Factors` | model output | Same, but the largest *negative* contributions — pushing toward Closed Lost, shown as a negative percentage (e.g. `"Product: Sealights (-71%)"`), not an inverted/positive number. Distinct from the call-signal `risk_score` below; this explains the MODEL's score, not call sentiment. |
| `Call_Summaries` | data/call_summaries.csv | List of the actual call-note summaries for this opp (one per call — an opp can have several), attached in the notebook via the same `Opportunity_Id`/`opp_id` join as the signal columns below. Empty list if no calls on file. |
| `risk_score` | call signals | Count of risk signals (0–3) |
| `win_score` | call signals | Count of win signals (0–7) |
| `net_signal` | derived | `win_score − risk_score` |
| `risk_building_own_tool` | call signals | 0/1 |
| `risk_competitor_present` | call signals | 0/1 |
| `risk_needs_business_case` | call signals | 0/1 |
| `win_urgency_signal` | call signals | 0/1 |
| `win_renewal_language` | call signals | 0/1 |
| `win_active_negotiation` | call signals | 0/1 |
| `win_champion_present` | call signals | 0/1 |
| `win_planning_rollout` | call signals | 0/1 |
| `win_stakeholder_aligned` | call signals | 0/1 |
| `win_explicit_commitment` | call signals | 0/1 |

Plus coverage-level aggregates written to `output/gtm_coverage.parquet` — one row per
(quarter, week) **at each of three grains**: Geo, Geo×Region, and Geo×Region×Territory
(from the `Map_Booking_Team_Static_live` mapping table). All three grains carry a
`target` — `TERRITORY_TARGETS` (from `Target_Monthly.csv`, see "Loading targets" below)
is Territory-keyed, and Region/Geo/All targets are summed bottom-up from it, so they
always reconcile exactly. `target`/`LTB`/`coverage` are null only where no team under
that slice has a `Target_Monthly.csv` match (currently APAC Asia AGE/SEA) — handled
the same as any other missing target (dashboard shows `—`, never crashes):

| Column | Notes |
|--------|-------|
| `week_of_quarter` | 1–14 (Q3 FY26: 14 weeks, W1 and W14 partial — see "14 weeks not 13" below) |
| `snapshot_date` | start-of-week date |
| `Geo` | AMS / EMEA / APAC / Public Sector / All / Unassigned |
| `Region` | `BTS_Region`; null for Geo-grain rows |
| `Territory` | `BTS_Territory`; null for Geo- and Region-grain rows |
| `open_pipe` | sum Cal_IACV where Stage = 'Open', CloseDate within the quarter |
| `booked` | sum Cal_IACV where Stage = 'Closed Won', CloseDate within the quarter |
| `target` | from `Target_Monthly.csv`, bottom-up rolled up — see "Loading targets" below |
| `LTB` | target − booked; null if no target |
| `coverage` | open_pipe / LTB; null if no target or LTB ≤ 0 |
| `coverage_wow` | coverage − coverage(week − 1), grouped by (**quarter**, Geo, Region, Territory) |
| `quarter` | e.g. `'Q3 FY26'` — one of `config.QUARTERS`'s labels |
| `quarter_start` | that quarter's start date, ISO |
| `cov_benchmark` | the deck's "needed coverage" line for this quarter (3.5× for Q3, 4.0× for Q4) — a display benchmark, not a dollar target |
| `is_current` | true for the one quarter that's actually in flight today |
| `is_future` | true for a quarter that hasn't started yet — its only row is a single "current standing" point (`week_of_quarter=1`), not a real weekly progression |

**`output/gtm_timepoints.json`** — one row per (quarter, timepoint, Geo, Region-or-null,
Territory-or-null), for slides 2/3's Pre-Q/1-Week-Ago/1-Day-Ago/Current comparison grid.
Same `quarter`/`quarter_start`/`cov_benchmark`/`is_current`/`is_future` metadata as
`gtm_coverage.json`, plus:

| Column | Notes |
|--------|-------|
| `timepoint` | one of `day1`, `wk_ago`, `day_ago`, `current` |
| `snapshot_date` | the as-of date for this timepoint (`day1` = quarter start; others = the actual snapshot date used) |
| `Geo` / `Region` / `Territory` | same semantics as `gtm_coverage.json` |
| `open_pipe` / `booked` / `target` / `LTB` / `coverage` | same as `gtm_coverage.json` |

A quarter that hasn't started yet (`is_future`) only ever has a `current` row — `day1`/
`wk_ago`/`day_ago` are absent (not null-valued, **absent as rows**), which is what lets
the dashboard render `—` for every column and delta with no special-casing.
`day1` reuses the verified week-1 frame; `wk_ago` is, **by explicit user decision, the
prior week's own pinned start-of-week snapshot** (not a day-relative today-minus-7) —
only `day_ago` needed new logic (`coverage.py`'s `_standing_at()` helper).

**`output/gtm_pipe_create.json`** — one row per (week 1–14, Geo, Region-or-null,
Territory-or-null), for slide 15's Pipe Create heatmap:

| Column | Notes |
|--------|-------|
| `week_of_quarter` | 1–14, always all 14 (future weeks are target-only rows) |
| `week_start` / `week_end` | ISO dates for that week |
| `days_in_week` / `days_counted` | 5/7/3 and however many of those days have actually been observed — `days_counted < days_in_week` marks a partial (in-flight or future) week |
| `Geo` / `Region` / `Territory` | same semantics as `gtm_coverage.json` |
| `created` | sum of `Cal_IACV` at each opp's first-ever appearance in the snapshot feed; **null** (not 0) when `days_counted == 0` |
| `opps` | count of such opps; null under the same condition |
| `asp` | `created / opps`; null if `opps` is 0 or null |
| `target_created` / `target_opps` | day-weighted allocation of the monthly `Pipeline`/`Opportunities` targets onto this week, prorated to `days_counted` |
| `target_asp` | `target_created / target_opps` |
| `att_created` / `att_opps` / `att_asp` | actual ÷ target; null if the target is falsy |

`opps`/`target_opps`/`att_opps`/`asp`/`target_asp`/`att_asp` are **unverified** — see
"Opp-count unit question" below. The dashboard's Pipe Create tab surfaces this caveat
whenever Opps or ASP is the selected metric.

---

## Pipeline layout

The model lives in a **Jupyter notebook**; data pull and coverage are small scripts.

```
notebooks/
└── win_probability.ipynb  ← THE MODEL (Jupyter): train + validate + pickle model,
                              score open pipeline, write gtm_intel.parquet + .json
pipeline/
├── pull.py                ← Synapse dedicated-pool pull: sku_nacv_fact + snapshot + ages + bts
├── pull_call_summaries.py ← Synapse serverless-pool pull: transcripts_lookup →
│                             data/call_summaries.csv (see tables/call-transcripts.md)
├── extract_signals.py     ← keyword-matches data/call_summaries.csv →
│                             data/call_signals_features.csv (offline)
├── coverage.py            ← weekly coverage curve + timepoints, per quarter in
│                             config.QUARTERS → gtm_coverage/gtm_timepoints parquet + .json
├── pipe_create.py         ← actual vs target pipe creation per week →
│                             gtm_pipe_create.parquet + .json
├── run.py                 ← runs all six steps in order, end to end
└── config.py              ← paths, constants, .env, quarter list, targets
dashboard/
└── index.html             ← reads output/*.json, renders the 5 views
```

### Step order

```
1. python pipeline/pull.py                 → data/*.parquet (needs VPN)
2. python pipeline/pull_call_summaries.py  → data/call_summaries.csv (needs VPN)
3. python pipeline/extract_signals.py      → data/call_signals_features.csv (offline)
4. run notebooks/win_probability.ipynb     → models/win_prob_model.pkl
                                             + output/gtm_intel.parquet + .json
5. python pipeline/coverage.py             → output/gtm_coverage(+timepoints).parquet + .json
6. python pipeline/pipe_create.py          → output/gtm_pipe_create.parquet + .json
7. open dashboard/index.html               → reads the JSON, renders the dashboard
```

Or just run `python pipeline/run.py`, which does steps 1–6 in order (needs VPN for
steps 1–2; the notebook execution uses `jupyter nbconvert`).

- Steps 1–2 need VPN; steps 3–7 run offline from the cached files.
- Run the notebook interactively, or headless for the loop/automation:
  `jupyter nbconvert --to notebook --execute notebooks/win_probability.ipynb` (or `papermill`).

---

## `pipeline/config.py`

```python
import os
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

ROOT        = Path(__file__).parent.parent
DATA        = ROOT / 'data'
OUTPUT      = ROOT / 'output'
MODELS      = ROOT / 'models'
SIGNALS_CSV = ROOT / 'data' / 'call_signals_features.csv'
TARGET_MONTHLY_CSV = ROOT / 'Target_Monthly.csv'

load_dotenv(ROOT / '.env')            # load Synapse creds from the .env at repo root
SYNAPSE_CONN_STR = os.environ['SYNAPSE_CONN_STR']

QUARTER_STARTS = ['2026-07-01', '2026-10-01']   # oldest first; [0] MUST stay the in-flight
# quarter — PRE_QUARTER_BUFFER_START and coverage.py's verified week-1 anchor logic both
# assume the buffer sits immediately before QUARTER_STARTS[0]. When Q4 becomes current,
# roll this list forward (and re-pull) rather than reordering it.

def q_end(start):
    """Last day of the 3-month quarter beginning `start`."""
    return (pd.Timestamp(start) + pd.DateOffset(months=3) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')

def fq_label(start):
    """'2026-07-01' -> 'Q3 FY26' (Tricentis fiscal year == calendar year)."""
    t = pd.Timestamp(start)
    return f"Q{(t.month - 1) // 3 + 1} FY{t.year % 100}"

QUARTER_START = QUARTER_STARTS[0]      # unchanged meaning downstream — the in-flight quarter
QUARTER_END   = q_end(QUARTER_START)
SNAP_END      = q_end(QUARTER_STARTS[-1])   # pull.py fetches through here so one pull covers
                                              # every quarter in QUARTERS (CloseDate scoping
                                              # still happens per-quarter in coverage.py)

# Pull.py also grabs this many days of snapshot history BEFORE QUARTER_START, so
# week 1 can anchor to the true last-snapshot-of-prior-quarter baseline instead of
# the first in-quarter snapshot. Some opps enter the daily snapshot feed 1-4 days
# late (created right at the quarter boundary but not captured until the ETL's next
# run) — without a buffer, week 1 picks up their first available (post-boundary) row
# as if it were the day-1 state, overstating day-1 pipe. Matches Coverage Curve
# Analysis/backend/coverage_builder.py's ENTRY_BUFFER pattern.
PRE_QUARTER_BUFFER_START = (pd.Timestamp(QUARTER_START) - pd.Timedelta(days=14)).strftime('%Y-%m-%d')

# Target_Monthly.csv is read ONCE here (not once per loader) — stripping both the column
# names and every object column's values, since the raw CSV has stray whitespace in
# ' Target_Type ', ' Geo ', ' GeoSubTerritory_AccountOwnerBookingsTeam' that would
# otherwise silently create blank-key rows and zero out real teams.
_TARGETS_RAW = pd.read_csv(TARGET_MONTHLY_CSV, low_memory=False)
_TARGETS_RAW.columns = [c.strip() for c in _TARGETS_RAW.columns]
for _c in _TARGETS_RAW.select_dtypes(include='object').columns:
    _TARGETS_RAW[_c] = _TARGETS_RAW[_c].str.strip()

def _target_by_team(target_type, quarter_start=QUARTER_START):
    """Team x month DataFrame for one Target_Type, for the 3 months of the quarter
    starting `quarter_start` — monthly, not summed to a single quarter total, so
    callers (pipe_create.py's weekly allocator) can prorate partial weeks themselves.
    Shared by load_territory_targets() and load_pipe_create_targets() so the CSV is
    parsed exactly once (_TARGETS_RAW above) and the month-column derivation lives
    in exactly one place.
    """
    start = pd.Timestamp(quarter_start)
    month_cols = [f"M{(start + pd.DateOffset(months=i)).strftime('%Y%m')}" for i in range(3)]
    df = _TARGETS_RAW[_TARGETS_RAW['Target_Type'] == target_type]
    return df.groupby('GeoSubTerritory_AccountOwnerBookingsTeam')[month_cols].sum()

def load_territory_targets(quarter_start=QUARTER_START):
    """Per-Territory Bookings targets for the quarter starting `quarter_start`, from
    Target_Monthly.csv.

    `GeoSubTerritory_AccountOwnerBookingsTeam` matches `Bookings_Team_Static` (the
    live Synapse mapping table) almost exactly — all but 2 of 28 teams (APAC Asia
    AGE/SEA, a recent split finance hasn't allocated a target to yet; the parent
    'APAC Asia' team still has one). Region/Geo targets are summed bottom-up from
    this in coverage.py via the bts mapping, so Territory -> Region -> Geo -> All
    always reconcile exactly — no separate hardcoded Geo dict to keep in sync.
    """
    return _target_by_team('Bookings', quarter_start).sum(axis=1).to_dict()

TERRITORY_TARGETS = load_territory_targets()

def load_pipe_create_targets(quarter_start=QUARTER_START):
    """Per-Territory Pipe Create $ target (Target_Type == 'Pipeline') and opp-count
    target (Target_Type == 'Opportunities'), both monthly, for the quarter starting
    `quarter_start`. Returns (pipe_target_by_month, opp_target_by_month) — two
    team x month DataFrames.

    ASP is NOT a row in this file — always derive it as pipe_target / opp_target at
    matching grain. Same team-key match and same 2 missing teams (APAC Asia AGE/SEA)
    as Bookings targets. The opp-count target's unit is unconfirmed: its rows carry
    a Product dimension, so it may count opp-product-lines rather than distinct
    opps — treat opp-count/ASP attainment as provisional until reconciled.
    """
    return _target_by_team('Pipeline', quarter_start), _target_by_team('Opportunities', quarter_start)

def quarter_week(d, quarter_start=QUARTER_START):
    """Week-of-quarter (1-based, Mon-Sun) for date `d` — reproduces the source
    snapshot table's own QuarterWeek column exactly (verified against every
    in-quarter date). Used only to build the forward-looking week calendar for
    pipe-create target allocation in pipe_create.py — actuals should read
    snap['QuarterWeek'] directly, never re-derive it with this.
    """
    anchor = pd.Timestamp(quarter_start)
    anchor = anchor - pd.Timedelta(days=anchor.weekday())   # Monday of the week containing quarter_start
    return (pd.Timestamp(d) - anchor).days // 7 + 1

COVERAGE_BENCHMARK = {'Q3 FY26': 3.5, 'Q4 FY26': 4.0}   # the deck's "needed coverage" line —
                                                          # a display benchmark, not a dollar target

QUARTERS = [{
    'label': fq_label(s), 'start': s, 'end': q_end(s),
    'targets': load_territory_targets(s),
    'benchmark': COVERAGE_BENCHMARK.get(fq_label(s)),
} for s in QUARTER_STARTS]

EXCLUDED_TEAMS = ['Account Management', 'Global', 'QAS Account Management']
EXCLUDED_STAGES = [
    'Closed - Duplicate', 'Stage 6 - Closed - Admin', 'Stage 7 - Churned',
    'Opportunity Rejected', 'Stage 0 - Renewal Outreach Not Started', '0 - First Interaction',
]

CAT_FEATURES    = ['Deal_Type', 'Source', 'Segment', 'Product']
NUM_FEATURES    = ['Stage_Age', 'S1_Age', 'NextStep_Age']
# call sentiment — NOT model features; used only by the agent/dashboard layer
SIGNAL_COLS     = [
    'risk_building_own_tool', 'risk_competitor_present', 'risk_needs_business_case',
    'win_urgency_signal', 'win_renewal_language', 'win_active_negotiation',
    'win_champion_present', 'win_planning_rollout', 'win_stakeholder_aligned',
    'win_explicit_commitment',
]

def geo_bucket(rf):
    """Map BTS_RegionFamily → the 4 reporting geos. Values are prefixed APAC, not APJ."""
    if rf and ('pubsec' in rf.lower() or 'public sector' in rf.lower()): return 'Public Sector'
    if rf and rf.startswith('AMS'):  return 'AMS'
    if rf and rf.startswith('EMEA'): return 'EMEA'
    if rf and rf.startswith('APAC'): return 'APAC'
    return 'Unassigned'

CLOSED_STAGES     = {'Closed Deferred', 'Closed Lost'}
CLOSED_WON_STAGES = {'6 - Closed/Pending', 'Closed Won', 'Stage 5 - Closed Won'}

def stage_bucket(raw_stage):
    """StageName-derived bucket — mirrors the Stage CASE in pull.py's SKU_SQL, applied here to
    Raw_Stage from the snapshot table. Do not use Stage_Pipe_Category: it's precomputed upstream
    and silently folds Closed Deferred into its 'Lost' value and drops '6 - Closed/Pending' entirely."""
    if raw_stage in CLOSED_WON_STAGES: return 'Closed Won'
    if raw_stage in CLOSED_STAGES:     return 'Closed'
    return 'Open'
```

---

## `pipeline/pull.py`

Pulls from Synapse. Saves parquets so downstream steps don't need VPN.

```python
import struct
import pandas as pd
import pyodbc
from azure.identity import AzureCliCredential
from config import DATA, EXCLUDED_TEAMS, EXCLUDED_STAGES, PRE_QUARTER_BUFFER_START, SNAP_END, SYNAPSE_CONN_STR

SQL_COPT_SS_ACCESS_TOKEN = 1256   # pyodbc connection attribute for a raw AAD access token

def get_conn():
    # Auth comes from `az login` (run once outside this script), not the connection
    # string's Authentication= keyword — the ODBC driver has no "use the CLI's cached
    # login" mode, so we fetch the token ourselves and hand it to pyodbc directly.
    token = AzureCliCredential().get_token('https://database.windows.net/.default').token
    token_bytes = token.encode('utf-16-le')
    token_struct = struct.pack(f'<I{len(token_bytes)}s', len(token_bytes), token_bytes)
    return pyodbc.connect(SYNAPSE_CONN_STR, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})

PRODUCT_CASE = """
    CASE
        WHEN N.Record_Type IN ('Service','Platinum Support')             THEN 'Recurring Services'
        WHEN N.Family IN ('Tosca OSV','TTA','TEE','Tosca')               THEN 'Tosca'
        WHEN N.Family IN ('Testim','Testim Salesforce','TTA for SFDC',
                          'TTA for SNOW','TTA SNOW',
                          'Tricentis Device Cloud','Mobile')             THEN 'Testim'
        WHEN N.Family IN ('Tosca BI','Tosca DI')                         THEN 'Data Integrity'
        WHEN N.Family IN ('Vera')                                        THEN 'Vera'
        WHEN N.Family IN ('qTest')                                       THEN 'qTest'
        WHEN N.Family IN ('LiveCompare')                                 THEN 'LiveCompare'
        WHEN N.Family IN ('NeoLoad')                                     THEN 'NeoLoad'
        WHEN N.Family IN ('Tricentis Sealights')                         THEN 'Sealights'
        ELSE N.Family
    END
"""

SKU_SQL = f"""
SELECT
    N.Opportunity_id                                            AS Opportunity_Id,
    CASE WHEN N.Deal_Type = 'New Business' THEN 'New Customer'
         ELSE N.Deal_Type END                                   AS Deal_Type,
    CASE WHEN N.Opportunity_Source_Logic = 'Lead Sourced' THEN 'Marketing Sourced'
         ELSE N.Opportunity_Source_Logic END                    AS Source,
    N.Segment,
    {PRODUCT_CASE}                                             AS Product,
    N.NACV_USD - N.Uplift_USD                                  AS Product_NACV,
    CASE
        WHEN N.StageName IN ('Closed Deferred','Closed Lost')                          THEN 'Closed'
        WHEN N.StageName IN ('6 - Closed/Pending','Closed Won','Stage 5 - Closed Won') THEN 'Closed Won'
        WHEN N.StageName IN ('Closed - Duplicate','Stage 6 - Closed - Admin',
                             'Stage 7 - Churned','Opportunity Rejected',
                             '0 - First Interaction')                                  THEN 'Other'
        ELSE 'Open'
    END                                                        AS Stage,
    N.StageName                                                AS Raw_Stage,
    N.Opp_Closed_Date                                          AS CloseDate,
    N.Booking_Team_Static
FROM [src].[sku_nacv_fact] N
WHERE N.Period                = 'Period_1'
  AND N.NACV_USD             != 0
  AND N.Record_Type          IN ('Product','Service','Platinum support')
  AND N.Deal_Type            IN ('New Business','Expansion','Upsell','Professional services')
  AND N.Booking_Team_Static  NOT IN ({','.join(f"'{t}'" for t in EXCLUDED_TEAMS)})
  AND N.Booking_Team_Static  IS NOT NULL
  AND N.StageName            NOT IN ({','.join(f"'{s}'" for s in EXCLUDED_STAGES)})
"""

# Snapshot pull — for the COVERAGE curve, the timepoint comparisons, and pipe create.
# Filtered on snapshot_date (the actual calendar day), not QuarterStartDate (an
# ETL-assigned tag that reflects when a row was recorded, not the deal's own
# CloseDate quarter — see coverage.py's CloseDate filter for why that tag isn't
# trustworthy). Range is [buffer before the FIRST quarter in config.QUARTERS, end of
# the LAST quarter] — one pull covers every quarter config.QUARTERS lists; CloseDate
# and per-quarter scoping happen downstream in coverage.py/pipe_create.py, not here.
# coverage.py reads the source QuarterWeek column directly rather than deriving it.
SNAP_SQL = f"""
SELECT
    snap.Opp_Id,
    snap.snapshot_date,
    snap.Raw_Stage,
    snap.Stage_Pipe_Category,
    snap.Cal_IACV,
    snap.Bookings_Team_static,
    snap.CloseDate,
    snap.QuarterWeek,
    snap.QuarterStartDate
FROM [rep].[trf_opp_daily_snapshot_new] snap
WHERE snap.snapshot_date     >= '{PRE_QUARTER_BUFFER_START}'
  AND snap.snapshot_date     <= '{SNAP_END}'
  AND snap.Bookings_Team_static IS NOT NULL
  AND snap.Bookings_Team_static NOT IN ({','.join(f"'{t}'" for t in EXCLUDED_TEAMS)})
  AND snap.Raw_Stage NOT IN ({','.join(f"'{s}'" for s in EXCLUDED_STAGES)})
"""

# Latest-snapshot age features — for the MODEL. No quarter filter: training spans
# every historical closed deal, so ages must reach all quarters. Daily snapshot →
# latest snapshot_date per opp = most up-to-date ages. Clean CTE, one row per opp.
AGE_SQL = """
WITH latest_snapshot AS (
    SELECT
        Opp_Id, Stage_Age, S1_Age, NextStep_Age,
        ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) AS rn
    FROM [rep].[trf_opp_daily_snapshot_new]
)
SELECT Opp_Id, Stage_Age, S1_Age, NextStep_Age
FROM latest_snapshot
WHERE rn = 1
"""

# Territory mapping — geo / region / territory come from here, never derived by CASE.
BTS_SQL = """
SELECT Bookings_Team_Static, BTS_Geo, BTS_Region, BTS_Territory, BTS_ProductFamily, BTS_RegionFamily
FROM [sharepoint].[Map_Booking_Team_Static_live]
WHERE ActiveTeam = 'Active'
"""

def pull():
    conn = get_conn()
    sku  = pd.read_sql(SKU_SQL, conn).drop_duplicates()
    snap = pd.read_sql(SNAP_SQL, conn).drop_duplicates()   # snapshot table emits each row twice
    ages = pd.read_sql(AGE_SQL, conn)                       # one row per opp — already deduped by rn=1
    bts  = pd.read_sql(BTS_SQL, conn)                       # territory mapping, cached for offline joins
    sku.to_parquet(DATA / 'sku_nacv.parquet', index=False)
    snap.to_parquet(DATA / 'snapshot.parquet', index=False)
    ages.to_parquet(DATA / 'opp_ages.parquet', index=False)
    bts.to_parquet(DATA / 'bts.parquet', index=False)
    print(f"Pulled {len(sku)} SKU rows, {len(snap)} snapshot rows, {len(ages)} opp ages, {len(bts)} teams")

if __name__ == '__main__':
    pull()
```

---

## `notebooks/win_probability.ipynb` — the model (Jupyter)

The model is authored in a **Jupyter notebook**, one markdown cell describing each
code block. It trains + validates, **pickles the fitted model**, scores the open
pipeline, and writes the dashboard feed. Run it interactively, or headless for the
loop: `jupyter nbconvert --to notebook --execute notebooks/win_probability.ipynb`.

**Cell 1 — markdown**
> # Win Probability Model
> Trains logistic regression on decided deals (Closed Won vs Closed Lost), validates
> on a held-out test set, pickles the model, and scores the open pipeline.

**Cell 2 — code — imports + config**
```python
import sys; sys.path.append('../pipeline')      # so the notebook can import config.py
import numpy as np
import pandas as pd
import joblib
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.metrics import roc_auc_score, classification_report
from config import DATA, OUTPUT, MODELS, CAT_FEATURES, NUM_FEATURES, SIGNAL_COLS, geo_bucket
```

**Cell 3 — markdown**
> ## Load cached data
> Read the parquets written by `pipeline/pull.py` — no VPN needed from here on.

**Cell 4 — code**
```python
sku     = pd.read_parquet(DATA / 'sku_nacv.parquet')
snap    = pd.read_parquet(DATA / 'snapshot.parquet')
ages    = pd.read_parquet(DATA / 'opp_ages.parquet')   # latest age per opp, all history
bts     = pd.read_parquet(DATA / 'bts.parquet')
signals = pd.read_csv(DATA / 'call_signals_features.csv')
bts['Geo'] = bts['BTS_RegionFamily'].apply(geo_bucket)
```

**Cell 5 — markdown**
> ## Build the training frame
> Decided deals only — Closed Won (1) vs Closed Lost (0); exclude Closed Deferred,
> Open, Other. Join the latest age features. Call signals are NOT model features.

**Cell 6 — code**
```python
train_df = (sku[(sku.Stage == 'Closed Won') | (sku.Raw_Stage == 'Closed Lost')]
            .merge(ages, left_on='Opportunity_Id', right_on='Opp_Id', how='left'))
train_df[NUM_FEATURES] = train_df[NUM_FEATURES].fillna(0)
train_df['Won'] = (train_df.Stage == 'Closed Won').astype(int)   # 1 = Won, 0 = Lost

X = train_df[CAT_FEATURES + NUM_FEATURES]
y = train_df['Won']
```

**Cell 7 — markdown**
> ## Split, cross-validate, evaluate on a held-out test set
> Fit on train only; report CV AUC and held-out test AUC — never in-sample.

**Cell 8 — code**
```python
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, stratify=y, random_state=42)

pre = ColumnTransformer([
    ('cat', OneHotEncoder(handle_unknown='ignore'), CAT_FEATURES),
    ('num', StandardScaler(),                       NUM_FEATURES),
])
model = Pipeline([('pre', pre), ('clf', LogisticRegression(max_iter=1000))])

cv_auc = cross_val_score(model, X_train, y_train, cv=5, scoring='roc_auc')
model.fit(X_train, y_train)
test_auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
print(f"CV AUC {cv_auc.mean():.3f} ± {cv_auc.std():.3f}  |  held-out test AUC {test_auc:.3f}")
print(classification_report(y_test, model.predict(X_test),
                            target_names=['Closed Lost', 'Closed Won']))
```

**Cell 9 — markdown**
> ## Refit on all decided deals and pickle the model

**Cell 10 — code**
```python
model.fit(X, y)                                     # deployed model = all decided deals
MODELS.mkdir(exist_ok=True)
joblib.dump(model, MODELS / 'win_prob_model.pkl')   # pickle the fitted model
```

**Cell 11 — markdown**
> ## Score the open pipeline
> One row per open SKU. Join ages, latest snapshot (open-pipe value), geo mapping,
> and call signals. `Win_Prob` from the model; signals are output-only (agent layer).

**Cell 12 — code**
```python
latest = snap.sort_values('snapshot_date').groupby('Opp_Id').last().reset_index()

scored = (sku[sku.Stage == 'Open']
          .merge(ages, left_on='Opportunity_Id', right_on='Opp_Id', how='left')
          .drop(columns='Opp_Id')
          .merge(latest[['Opp_Id','snapshot_date','Cal_IACV']],
                 left_on='Opportunity_Id', right_on='Opp_Id', how='left')
          .drop(columns='Opp_Id')
          .merge(bts[['Bookings_Team_Static','Geo','BTS_Region','BTS_Territory']],
                 left_on='Booking_Team_Static', right_on='Bookings_Team_Static', how='left')
          .merge(signals, left_on='Opportunity_Id', right_on='opp_id', how='left'))

scored[NUM_FEATURES] = scored[NUM_FEATURES].fillna(0)
scored['Win_Prob']  = model.predict_proba(scored[CAT_FEATURES + NUM_FEATURES])[:, 1]

scored[SIGNAL_COLS] = scored[SIGNAL_COLS].fillna(0).astype(int)   # agent layer, output only
scored['risk_score'] = scored[['risk_building_own_tool','risk_competitor_present','risk_needs_business_case']].sum(axis=1)
scored['win_score']  = scored[['win_urgency_signal','win_renewal_language','win_active_negotiation',
                               'win_champion_present','win_planning_rollout','win_stakeholder_aligned',
                               'win_explicit_commitment']].sum(axis=1)
scored['net_signal'] = scored['win_score'] - scored['risk_score']
scored = scored.rename(columns={'BTS_Region':'Region','BTS_Territory':'Territory','Cal_IACV':'open_pipe'})
```

**Cell 6b — markdown**
> ## 6b. Explain the model — odds ratios and per-score factors
> Two things, both derived from the same fitted coefficients: (1) a global odds-ratio
> table — `exp(coefficient)`, rescaled to "per +1 day" for the standardized numeric
> features so it reads as a real-world unit, not per-standard-deviation — showing
> exactly how much each feature multiplies the odds of Won; (2) per-opportunity
> `Win_Factors`/`Risk_Factors`, the same coefficients applied to each row's own
> feature values, so each factor string carries that opportunity's actual odds
> multiplier.

**Cell 6c — code (global odds-ratio table)**
```python
pre_fitted, clf = model.named_steps['pre'], model.named_steps['clf']
feat_names = pre_fitted.get_feature_names_out()
coefs = clf.coef_[0]
num_scale = pre_fitted.named_transformers_['num'].scale_   # per-feature std dev, same order as NUM_FEATURES

def _feature_level(name):
    """'cat__Segment_Tier 1' -> ('Segment', 'Tier 1'); 'num__Stage_Age' -> ('Stage_Age', 'per +1 day')."""
    kind, rest = name.split('__', 1)
    if kind == 'num':
        return rest, 'per +1 day'
    col = next(c for c in CAT_FEATURES if rest.startswith(c + '_'))
    return col, rest[len(col) + 1:]

odds_rows = []
for name, coef in zip(feat_names, coefs):
    col, level = _feature_level(name)
    if name.startswith('num__'):
        coef = coef / num_scale[NUM_FEATURES.index(col)]   # per-day, not per-SD
    odds_ratio = np.exp(coef)
    odds_rows.append({'Feature': col, 'Level': level, 'Coefficient': coef,
                       'Odds Ratio': odds_ratio, '% Change in Odds': (odds_ratio - 1) * 100})

odds_ratios = pd.DataFrame(odds_rows).sort_values('% Change in Odds', ascending=False).reset_index(drop=True)
odds_ratios.to_csv(OUTPUT / 'win_prob_odds_ratios.csv', index=False)   # reference artifact, not read by the dashboard
odds_ratios
```

The split on `'__'` first, then a `CAT_FEATURES` prefix match (not a naive split on
the first `'_'`), matters because `get_feature_names_out()` joins as
`{column}_{category}` and column names like `Deal_Type` already contain an
underscore — a naive split breaks on those. Numeric coefficients are divided by the
fitted `StandardScaler`'s `scale_` before exponentiating: the model was trained on
standardized features, so the raw coefficient is "per 1 standard deviation," and
without this rescaling the odds ratio would silently mean something different from
what its "per +1 day" label claims. **`% Change in Odds`** (`(odds_ratio − 1) × 100`)
is the sort key and the more interpretable column — an odds ratio of 2.74 and a %
change of +174% are the same number, but "+174%" reads directly as "this more than
doubles the odds," where "2.74×" needs a beat of mental math first.

**Cell 6d — code (per-opportunity factors)**
```python
X_trans = pre_fitted.transform(scored[CAT_FEATURES + NUM_FEATURES])
if hasattr(X_trans, 'toarray'):
    X_trans = X_trans.toarray()
contrib = X_trans * coefs   # one row per open SKU, one column per transformed feature (this row's own standardized value, not rescaled)

raw_vals = scored[CAT_FEATURES + NUM_FEATURES].to_dict('records')

def _factor_label(name, raw_row, odds_ratio):
    col, level = _feature_level(name)
    shown = f"{raw_row[col]:.0f}d" if name.startswith('num__') else level
    pct = (odds_ratio - 1) * 100   # e.g. odds_ratio 2.49 -> '+149%', 0.35 -> '-65%'
    return f"{col}: {shown} ({pct:+.0f}%)"

def _top_factors(i, sign, n=3):
    row = contrib[i]
    order = (row * sign).argsort()[::-1][:n]
    idx = [j for j in order if row[j] * sign > 0]
    return [_factor_label(feat_names[j], raw_vals[i], np.exp(row[j])) for j in idx]

scored['Win_Factors']  = [_top_factors(i, 1)  for i in range(len(scored))]
scored['Risk_Factors'] = [_top_factors(i, -1) for i in range(len(scored))]
```

`np.exp(row[j])` is exact, not an approximation: for a one-hot categorical feature
`row[j]` is either `coef` (category active) or `0` (inactive), so `exp(row[j])` is
precisely that level's odds ratio; for a numeric feature `row[j] = coef_sd × z`
where `z` is this row's own standardized value, which is mathematically identical
to `(coef_sd / scale) × (x − mean)` — the same per-day rescaling as the global
table, just multiplied by this specific opportunity's actual deviation from the
training mean rather than a flat "+1 day." Sign is applied only to *select* which
indices are the top win/risk factors (largest positive vs. largest negative
contribution) — the displayed percentage always uses the true, unflipped `row[j]`,
so a risk factor correctly shows as a negative percentage (e.g. `-71%`) rather than
an inverted positive number.

**Cell 6e — markdown**
> ## 6e. Attach call summaries
> `data/call_summaries.csv` (pulled live from Synapse's serverless "Built-in" pool,
> `AIDatabase.transcripts_lookup`, by `pipeline/pull_call_summaries.py`) holds the
> actual call notes (one row per call — a single opp can have several), keyed the
> same way as `call_signals_features.csv`. Grouped into a list per opp so an opp
> with multiple calls keeps each summary distinct rather than concatenated into one
> blob.

**Cell 6f — code**
```python
transcripts = pd.read_csv(DATA / 'call_summaries.csv')
call_summaries = transcripts.groupby('opp_id')['summary'].apply(list)
scored['Call_Summaries'] = scored['Opportunity_Id'].map(call_summaries)
scored['Call_Summaries'] = scored['Call_Summaries'].apply(lambda v: v if isinstance(v, list) else [])
```

Same join key as the signal columns (`Opportunity_Id` ↔ `opp_id`) — `call_summaries.csv`
and `call_signals_features.csv` are derived from the same pull, so `hasCallData`
(checked via `opp_id` in the dashboard) and having a non-empty `Call_Summaries` list
correlate closely, but the dashboard checks each independently rather than assuming
one implies the other.

**Cell 13 — markdown**
> ## Write the dashboard feed
> Parquet for Python re-use, **JSON for the HTML dashboard** — a browser cannot read
> a pickle or parquet. The `.pkl` above is the fitted model; this JSON is the scored data.

**Cell 14 — code**
```python
OUTPUT.mkdir(exist_ok=True)
scored.to_parquet(OUTPUT / 'gtm_intel.parquet', index=False)
scored.to_json(OUTPUT / 'gtm_intel.json', orient='records')   # dashboard/index.html reads this
print(f"Scored {len(scored)} open SKUs  |  median Win_Prob: {scored.Win_Prob.median():.1%}")
```

---

## `pipeline/coverage.py`

Builds the weekly coverage curve **and** the timepoint comparison, for every quarter
in `config.QUARTERS`. Writes `gtm_coverage`/`gtm_timepoints` parquet + JSON.

Structure: three small extractions with **zero behavior change** from the original
single-quarter version (`_weekly_curve` parameterized by `by`/`key` so it emits both
weekly and timepoint rows; `_targets` factors out the bottom-up target rollup;
`_all_grains` factors out the four-grain loop), a new `_standing_at()` as-of helper
(only needed for the "1 Day Ago" timepoint — "1 Week Ago" reuses the existing weekly
frame, "Pre-Q Day 1" reuses the existing week-1 frame), a new `_quarter_rows()` that
returns `(weekly_rows, timepoint_rows)` for one quarter — with a future-quarter branch
that emits only a `current` timepoint — and an outer loop in `coverage()` over
`config.QUARTERS`. **The normal-quarter branch of `_quarter_rows()` is the original,
PBI-reconciled logic moved in verbatim** — same CloseDate filter, same week-1
at-or-before-boundary anchor with no fallback, same current-week latest-then-filter
ordering. Full source:

```python
import pandas as pd
from config import DATA, OUTPUT, QUARTERS, geo_bucket, stage_bucket

nn = lambda v: None if v is None or pd.isna(v) else v   # NaN -> None (pandas sum(min_count=1) leaves NaN, not None)

def _weekly_curve(weekly, geo, region=None, territory=None, target=None, by='QuarterWeek', key='week_of_quarter'):
    """One coverage row per `by`-group (a week, or a named timepoint like 'day1') for
    a single geo / region / territory slice.

    `target` is None wherever no team under this slice has a Target_Monthly.csv
    match (e.g. APAC Asia AGE/SEA) — handled the same as any other missing target.
    `by`/`key` let this same function emit both the weekly coverage curve (grouped
    on the source QuarterWeek column) and the timepoint comparison (grouped on a
    'timepoint' label) without duplicating the open_pipe/booked/coverage math.
    """
    rows = []
    for k, wk in weekly.groupby(by):
        open_pipe = wk.loc[wk.Stage == 'Open',       'Cal_IACV'].sum()
        booked    = wk.loc[wk.Stage == 'Closed Won', 'Cal_IACV'].sum()
        ltb       = (target - booked) if target is not None else None
        cov       = (open_pipe / ltb) if (ltb is not None and ltb > 0) else None
        # 'as_of' carries an explicit as-of date for timepoint rows (all rows in a
        # timepoint group share one label-level date, e.g. 'day1' -> quarter start);
        # weekly rows have no 'as_of' column, so they fall back to the original
        # per-group earliest-snapshot date.
        snap_date = wk['as_of'].iloc[0] if 'as_of' in wk.columns else wk.snapshot_date.min()
        rows.append({key: k, 'snapshot_date': snap_date,
                     'Geo': geo, 'Region': region, 'Territory': territory,
                     'open_pipe': open_pipe, 'booked': booked,
                     'target': target, 'LTB': ltb, 'coverage': cov})
    return rows

def _targets(bts, territory_targets):
    """Bottom-up targets: `territory_targets` (one quarter's slice of Target_Monthly.csv)
    rolled up through this bts mapping, so Territory -> Region -> Geo -> All reconcile
    exactly. Mutates bts['TerritoryTarget'] in place — call once per quarter, since
    each quarter in QUARTERS carries its own targets dict.
    """
    bts['TerritoryTarget'] = bts['Bookings_Team_Static'].map(territory_targets)
    territory_target = bts.set_index('Bookings_Team_Static')['TerritoryTarget'].to_dict()
    region_target = bts.groupby('BTS_Region')['TerritoryTarget'].sum(min_count=1).to_dict()
    geo_target = bts.groupby('Geo')['TerritoryTarget'].sum(min_count=1).to_dict()
    all_target = bts['TerritoryTarget'].sum(min_count=1)
    return territory_target, region_target, geo_target, all_target

def _all_grains(frame, tgt, by, key):
    """The four standard output grains (All / Geo / Geo x Region / Geo x Region x
    Territory), shared verbatim by the weekly coverage curve and the timepoint
    comparison — `frame` just needs Geo/BTS_Region/BTS_Territory/Stage/Cal_IACV
    columns and a `by` column to group on."""
    territory_target, region_target, geo_target, all_target = tgt
    rows = _weekly_curve(frame, 'All', target=nn(all_target), by=by, key=key)
    for geo, grp in frame.groupby('Geo'):                                  # per-geo curves
        rows += _weekly_curve(grp, geo, target=nn(geo_target.get(geo)), by=by, key=key)
    for (geo, region), grp in frame.groupby(['Geo', 'BTS_Region']):        # per-region drill-down
        rows += _weekly_curve(grp, geo, region=region, target=nn(region_target.get(region)), by=by, key=key)
    for (geo, region, territory), grp in frame.groupby(['Geo', 'BTS_Region', 'BTS_Territory']):  # per-territory
        rows += _weekly_curve(grp, geo, region=region, territory=territory,
                               target=nn(territory_target.get(territory)), by=by, key=key)
    return rows

def _standing_at(snap, as_of, qs, qe):
    """Standing pipe as of `as_of`, using the SAME rule as the verified current-week
    logic below: latest row per opp inside that day's own quarter-week window,
    CloseDate checked only AFTER picking the latest row. Reproduces the current-week
    frame exactly when as_of == the latest available snapshot date (verified against
    the Week 31 PBI-reconciled figures: $90.0320M open pipe at every grain). Falls
    back to the newest snapshot_date <= as_of so a skipped ETL day never blanks a
    timepoint column. Matched on QuarterStartDate as well as QuarterWeek so a
    lookback landing in the pre-quarter buffer can't collide with this quarter's
    own week numbering.
    """
    d   = snap.loc[snap['snapshot_date'] <= as_of, 'snapshot_date'].max()
    ref = snap.loc[snap['snapshot_date'] == d].iloc[0]
    win = snap[(snap['QuarterStartDate'] == ref['QuarterStartDate']) &
               (snap['QuarterWeek'] == ref['QuarterWeek']) &
               (snap['snapshot_date'] <= d)]
    out = win.groupby('Opp_Id').last().reset_index()
    out = out[(out['CloseDate'] >= qs) & (out['CloseDate'] <= qe)]
    return d, out

def _quarter_rows(snap, q, tgt):
    """Weekly + timepoint rows for one QUARTERS entry `q`. A quarter with no
    snapshots yet gets a single 'current standing' point instead of a weekly
    progression; everything else is the original, PBI-reconciled single-quarter
    logic, moved in verbatim.
    """
    q_start, q_end = pd.Timestamp(q['start']), pd.Timestamp(q['end'])
    mx = snap['snapshot_date'].max()

    if q_start > mx:
        # Future quarter: nothing has been snapshotted inside it yet. Emit ONLY a
        # 'current' timepoint (today's open pipe for deals landing in this quarter)
        # and a matching single weekly row (QuarterWeek=1), so the existing weekly
        # table keeps working unmodified. day1/wk_ago/day_ago are simply ABSENT —
        # that absence (not a null placeholder) is what lets the dashboard render
        # dashes for a not-yet-started quarter with no special-casing.
        fut = snap[snap['snapshot_date'] == mx].groupby('Opp_Id').last().reset_index()
        fut = fut[(fut['CloseDate'] >= q_start) & (fut['CloseDate'] <= q_end)]
        fut_weekly = fut.assign(QuarterWeek=1, as_of=mx)
        fut_tp     = fut.assign(timepoint='current', as_of=mx)
        return (_all_grains(fut_weekly, tgt, by='QuarterWeek', key='week_of_quarter'),
                _all_grains(fut_tp,     tgt, by='timepoint',   key='timepoint'))

    # A deal only belongs to THIS quarter's coverage if its CloseDate falls inside
    # it. The daily snapshot table keeps reporting every historically-Won (and
    # future-dated open) opp every day regardless of quarter — some Won rows here
    # have CloseDates back to 2013 and open rows out to 2099. Without this filter,
    # open_pipe/booked silently pull in billions from deals closing in other
    # quarters entirely.
    in_q = snap[(snap['snapshot_date'] >= q_start) & (snap['snapshot_date'] <= q_end)]
    current_week = in_q['QuarterWeek'].max()
    in_q_valid = in_q[(in_q['CloseDate'] >= q_start) & (in_q['CloseDate'] <= q_end)]

    # Weeks 2..N-1: filter to CloseDate-in-quarter first (what was true AS OF that
    # historical week), then take the EARLIEST snapshot per opp in that week — its
    # start-of-week balance. Skips week 1 (handled separately below) and the current
    # in-flight week (handled further below).
    past = in_q_valid[(in_q_valid['QuarterWeek'] != 1) & (in_q_valid['QuarterWeek'] != current_week)]
    past = past.groupby(['Opp_Id','QuarterWeek']).first().reset_index()

    # Week 1: each opp's LATEST snapshot AT OR BEFORE the quarter boundary — usually
    # exactly QUARTER_START, falling back a day or two only if that exact date has no
    # snapshot (e.g. a weekend/holiday quarter start). Deliberately NO fallback to a
    # later in-quarter date for opps missing at the boundary: some opps first appear
    # in the feed 1-4 days into the quarter (created right at the boundary but not
    # captured until the ETL's next run, or genuinely new pipe added that week), and
    # PBI's own "Pre-Q Day 1" figure does not count them either — including them via
    # a later-date fallback was the actual bug (verified against the Week 31 PBI
    # deck: with the fallback, every region was off by $0.05-0.9M; without it, 9 of
    # 13 regions match to the penny and the total lands within 0.16%). Also
    # restricted to opps tracked somewhere in-quarter, since some opps exist only in
    # the pre-quarter buffer and never appear again (closed/reassigned right at the
    # boundary) — without this restriction they resurrect as phantom week-1 pipe.
    # Skipped entirely if week 1 IS the current in-flight week (quarter just
    # started) — the current-week logic below already does the right thing then.
    if current_week == 1:
        week1 = in_q_valid.iloc[0:0]   # empty — current-week branch handles it instead
    else:
        in_q_opp_ids = set(in_q['Opp_Id'])
        week1 = snap[snap['snapshot_date'] <= q_start].groupby('Opp_Id').last().reset_index()
        week1 = week1[week1['Opp_Id'].isin(in_q_opp_ids)]
        week1 = week1[(week1['CloseDate'] >= q_start) & (week1['CloseDate'] <= q_end)]
        week1['QuarterWeek'] = 1

    # Current in-flight week: take each opp's LATEST snapshot within the current
    # week's own date range FIRST, THEN check CloseDate. Filtering CloseDate before
    # grouping (like `past` does) is wrong here — an opp that slipped out of the
    # quarter mid-week has its post-slip rows dropped by the filter, so `.last()`
    # would resurrect its last PRE-slip row (still "Open") as if it were current.
    # Checking CloseDate only after picking the true latest row correctly drops
    # deals that have actually slipped by today, instead of resurrecting a stale
    # pre-slip snapshot as "current" open pipe. The snapshot_date guard keeps this
    # from ever matching a pre-quarter buffer row that coincidentally shares the
    # same week number from a prior quarter's own numbering.
    current = in_q[in_q['QuarterWeek'] == current_week].groupby('Opp_Id').last().reset_index()
    current = current[(current['CloseDate'] >= q_start) & (current['CloseDate'] <= q_end)]

    weekly = pd.concat([week1, past, current], ignore_index=True)
    weekly_rows = _all_grains(weekly, tgt, by='QuarterWeek', key='week_of_quarter')

    # Timepoint frame for slides 2/3. Per explicit user decision, "1 Week Ago" is the
    # prior week's own pinned start-of-week snapshot (already computed above as part
    # of `weekly`), NOT a day-relative today-minus-7 — so only "1 Day Ago" needs the
    # new _standing_at() helper; the other three timepoints reuse existing frames.
    mx_q = in_q['snapshot_date'].max()
    wk_ago = weekly[weekly['QuarterWeek'] == current_week - 1] if current_week > 1 else weekly.iloc[0:0]
    wk_ago_asof = wk_ago['snapshot_date'].min() if len(wk_ago) else None
    d1, day_ago = _standing_at(snap, mx_q - pd.Timedelta(days=1), q_start, q_end)

    tp = pd.concat([
        week1  .assign(timepoint='day1',    as_of=q_start),
        wk_ago .assign(timepoint='wk_ago',  as_of=wk_ago_asof),
        day_ago.assign(timepoint='day_ago', as_of=d1),
        current.assign(timepoint='current', as_of=mx_q),
    ], ignore_index=True)
    timepoint_rows = _all_grains(tp, tgt, by='timepoint', key='timepoint')

    return weekly_rows, timepoint_rows

def coverage():
    snap = pd.read_parquet(DATA / 'snapshot.parquet')
    bts  = pd.read_parquet(DATA / 'bts.parquet')           # geo/region/territory come from the mapping table
    bts['Geo'] = bts['BTS_RegionFamily'].apply(geo_bucket)
    snap['Stage'] = snap['Raw_Stage'].apply(stage_bucket)  # derive from Raw_Stage — Stage_Pipe_Category is unreliable

    # Case/whitespace-insensitive join key — some historical snapshot rows carry a
    # team name that differs from the live mapping only in casing (e.g. snapshot's
    # 'EMEA Core Benelux' vs bts's 'EMEA Core BeNeLux'). An exact-match merge drops
    # those into 'Unassigned' instead of their real region, understating it (this is
    # what caused EMEA North to read ~$2.75M low against the Week 31 PBI deck).
    snap['_team_key'] = snap['Bookings_Team_static'].str.strip().str.lower()
    bts['_team_key']  = bts['Bookings_Team_Static'].str.strip().str.lower()
    snap = snap.merge(bts[['_team_key','Geo','BTS_Region','BTS_Territory']], on='_team_key', how='left')
    snap['Geo']          = snap['Geo'].fillna('Unassigned')
    snap['BTS_Region']    = snap['BTS_Region'].fillna('Unassigned')
    snap['BTS_Territory'] = snap['BTS_Territory'].fillna('Unassigned')

    snap['CloseDate']        = pd.to_datetime(snap['CloseDate'])
    snap['snapshot_date']    = pd.to_datetime(snap['snapshot_date'])
    snap['QuarterStartDate'] = pd.to_datetime(snap['QuarterStartDate'])
    snap = snap.sort_values('snapshot_date')

    # One pass per quarter in config.QUARTERS (today: the in-flight quarter + the
    # next one). Each quarter gets its own bottom-up targets (_targets mutates
    # bts['TerritoryTarget'], so it's recomputed every iteration) and is stamped
    # with metadata so the dashboard can select/filter by quarter.
    weekly_rows, tp_rows = [], []
    mx = snap['snapshot_date'].max()
    for q in QUARTERS:
        tgt = _targets(bts, q['targets'])
        w, t = _quarter_rows(snap, q, tgt)
        meta = {'quarter': q['label'], 'quarter_start': q['start'], 'cov_benchmark': q['benchmark'],
                'is_current': pd.Timestamp(q['start']) <= mx <= pd.Timestamp(q['end']),
                'is_future':  pd.Timestamp(q['start']) > mx}
        weekly_rows += [dict(r, **meta) for r in w]
        tp_rows     += [dict(r, **meta) for r in t]

    df = pd.DataFrame(weekly_rows).sort_values(['quarter', 'Geo', 'Region', 'Territory', 'week_of_quarter'])
    # Quarter-aware WoW: without grouping on `quarter` too, Q4's single standing row
    # would diff against Q3's last week instead of showing no WoW at all.
    df['coverage_wow'] = df.groupby(['quarter', 'Geo', 'Region', 'Territory'], dropna=False)['coverage'].diff()

    tp = pd.DataFrame(tp_rows).sort_values(['quarter', 'Geo', 'Region', 'Territory', 'timepoint'])

    OUTPUT.mkdir(exist_ok=True)
    df.to_parquet(OUTPUT / 'gtm_coverage.parquet', index=False)
    df.to_json(OUTPUT / 'gtm_coverage.json', orient='records', date_format='iso')   # iso dates -> dashboard/index.html reads this
    tp.to_parquet(OUTPUT / 'gtm_timepoints.parquet', index=False)
    tp.to_json(OUTPUT / 'gtm_timepoints.json', orient='records', date_format='iso')

    for q in QUARTERS:
        qdf = df[df['quarter'] == q['label']]
        print(f"{q['label']}: {qdf.week_of_quarter.nunique()} week(s) across {qdf.Geo.nunique()} geos"
              f"{' (standing only, quarter not started)' if qdf['is_future'].any() else ''}")

if __name__ == '__main__':
    coverage()
```

---

## `pipeline/pipe_create.py`

Actual-vs-target pipe creation per week (slide 15). A new module rather than an
addition to `coverage.py`: every rule in that file (CloseDate scoping, boundary
anchoring, current-week ordering) is *inapplicable* here, and `coverage.py` is
PBI-reconciled and shouldn't be touched.

**Full source and design notes moved to [`../models/pipe-create.md`](../models/pipe-create.md).**
It is the single source of truth for this module — do not re-inline it here.

---

## `pipeline/run.py` — orchestrator

The model is a notebook, so `run.py` runs the two scripts and executes the notebook
headless in between (via `nbconvert`). This is the single entry point for the loop.

```python
import subprocess
from pull        import pull
from coverage    import coverage
from pipe_create import pipe_create

NOTEBOOK = 'notebooks/win_probability.ipynb'

if __name__ == '__main__':
    print('--- Step 1: pull ---');        pull()
    print('--- Step 2: model ---')
    subprocess.run(['jupyter', 'nbconvert', '--to', 'notebook',
                    '--execute', '--inplace', NOTEBOOK], check=True)
    print('--- Step 3: coverage ---');     coverage()
    print('--- Step 4: pipe create ---'); pipe_create()
    print('Done. Outputs in output/ (parquet + json)')
```

Run everything: `python pipeline/run.py`
Re-run the model + coverage/pipe-create without re-pulling (no VPN): execute the
notebook, then `python pipeline/coverage.py` and `python pipeline/pipe_create.py`
(each also runs standalone: `python pipeline/pipe_create.py` alone is enough if only
the model didn't change).

---

## Dashboard — what to show

The dashboard (`dashboard/index.html`) reads the four JSON exports written by the
notebook, `coverage.py`, and `pipe_create.py`. Five tabs (segmented page nav,
`01`–`05`), one page each.

| Tab | Reads from |
|-----|-----------|
| `01` Coverage | `output/gtm_coverage.json`, scoped to `is_current` |
| `02` Pipe Balance | `output/gtm_timepoints.json` |
| `03` Pipe Create | `output/gtm_pipe_create.json` |
| `04` Deal Scoring | `output/gtm_intel.json` (rollup also reads `gtm_coverage.json`, scoped to `is_current`) |
| `05` Call Sentiment | `output/gtm_intel.json`, deduplicated on `opp_id` (rows with null `opp_id` excluded before dedup) |

### View 1 — Coverage

Source: `output/gtm_coverage.json`, **filtered to `r.is_current`** — this file now
carries every quarter in `config.QUARTERS`, and a future quarter's single standing
row is tagged `week_of_quarter=1` too, which would otherwise sort ahead of this
quarter's real weeks 2-N. This weekly-curve view only makes sense for the in-flight
quarter anyway; a future quarter has no week-over-week history yet (see View 2 for
how that quarter is shown instead).

Show:
- KPI strip: Open pipe / Booked / LTB / Coverage (× amount, not %) / Coverage WoW delta (absolute × diff, e.g. `-0.10×` — not a percent change); always the quarter-wide 'All' total for the selected week (no Geo/Region/Territory filter drives it — that's the tree table's job now)
- No chart — dropped in favor of the tree below
- **No filter dropdowns for Geo/Region/Territory.** The weekly detail table is an always-visible, expandable Geo → Region → Territory tree (`.row-geo`/`.row-region`/`.row-territory` — see Design system section): click a row's chevron to expand/collapse its children in place, rather than picking a level from a `<select>`. Geo rows default expanded, Region rows default collapsed. Only the Geo grain has a `target`; Region/Territory rows show `—` for target/LTB/coverage. A totals row ('All') closes out the table. A Week picker above the tree still scrubs history.
- `covClass` colors the Coverage cell against `r.cov_benchmark ?? 3` (per-quarter — 3.5× for Q3, 4.0× for Q4), not a hardcoded `3`.

### View 2 — Pipe Balance (slides 2-5)

Source: `output/gtm_timepoints.json`

Recreates the deck's Regional Pipe Coverage (slide 2, Q3) / Pipe Balance (slide 3, Q3)
and their Q4 counterparts (slides 4-5) as one interactive table instead of four static
screenshots.

Show:
- A Quarter pill row (`.week-filter`/`.week-pill`, sorted by `quarter_start`; default = the `is_current` quarter) and a **Coverage / Dollars** metric toggle.
- The same Geo → Region → Territory tree as Coverage, but with 7 data columns instead of 6: **Pre-Q (Day 1)**, **1 Week Ago**, **1 Day Ago**, **Current**, **QTD Change**, **Current vs. Last Week**, **Current vs. Yesterday** — the last three are deltas computed client-side (`current − day1`, `current − wk_ago`, `current − day_ago`), never precomputed server-side.
- Rows are pivoted client-side from `gtm_timepoints.json`'s long format (one row per timepoint) into one node per grain (`pivotByGrain(rows, 'timepoint')`), then rendered through the same `treeBody()` walker Coverage uses.
- **A quarter that hasn't started yet (`is_future`) needs zero special-casing**: its rows only ever have a `current` timepoint, so `day1`/`wk_ago`/`day_ago` are `undefined` on the pivoted node, every value/delta formatter treats that as `null`, and the whole row renders as `—` except Current.
- Current's coverage cell colors against `r.cov_benchmark` (the same per-quarter benchmark used on the Coverage tab), so Q4 is judged against 4.0× and Q3 against 3.5×.

### View 3 — Pipe Create (slide 15)

Source: `output/gtm_pipe_create.json`

Recreates the deck's Pipe Create Heatmap: how much pipe was actually created per week
vs. the `Target_Monthly.csv`-derived target, by Geo/Region/Territory.

Show:
- KPI strip: Pipe Created / Pipe Target / Attainment (QTD, summed over weeks with `days_counted > 0`) / Opps Created, then Opp Target / Actual ASP / Target ASP. The "latest week" label uses the latest week with `days_counted > 0` — **not** the max `week_of_quarter`, since `gtm_pipe_create.json` always carries all 14 weeks (future weeks are target-only rows) and an unfiltered max would always read 14.
- A **Pipe $ / Opps / ASP** metric toggle (`.pill-btn`, same pattern as Deal Scoring's risk pills). Selecting Opps or ASP shows a visible caveat: *"Unverified — the Opportunities target may count opp-product-lines rather than distinct opps."* — see "Opp-count unit question" below.
- Heatmap: same Geo → Region → Territory tree as Coverage/Pipe Balance, columns are `QTD` + `W1`...`W14`, cells are attainment % on a 5-band color scale (background carries the heat, text carries the tier — reuses `--good`/`--warn`/`--bad` and their `-soft`/`-band` variants, no new tokens). Two threshold sets: `[0.25, 0.50, 0.75, 1.00]` for Pipe $ and Opps (matches the observed decile spread), `[0.70, 0.85, 0.95, 1.10]` for ASP (which clusters tightly around 1.0, so the $ thresholds would paint every cell the same shade of green). `QTD` is volume-weighted (`sum(actual) / sum(target)` over observed weeks), not an average of weekly ratios. A partial week's column header gets a `·` suffix and a tooltip with the exact day count. Null cells (future weeks, or a slice with no target at all) render `—` in `var(--ink-4)`.
- Each row's cells show that row's own attainment (its own actual ÷ its own bottom-up-rolled target) — no inheritance from parent to child.

### View 4 — Deal scoring

Source: `output/gtm_intel.json` (rollup also reads `output/gtm_coverage.json` for Open Pipe/Bookings/Target)

Show:
- Filters: Deal Type · Product · Risk pills (no Min Win Prob slider — removed; no Geo/Region/Territory dropdowns — replaced by the tree below). These now scope only the KPI strip (`getScoringFiltered()`); the rollup tree reads `intel` directly and is unaffected by them.
- KPI strip: Opportunities · Pipeline NACV · Median Win Probability · High-Risk Deals, all recomputed from the filtered set
- **Geo/Region/Territory rollup** — same always-visible expandable tree pattern as Coverage (not filter dropdowns): Open Pipe / Bookings / Target pulled from the *same* `gtm_coverage.json` row used on the Coverage tab (so the two tabs never disagree), plus **Median Win Prob** (deliberately median, not mean — a handful of near-0%/near-100% deals shouldn't swing the team-level read the way they would an average) and **Expected Bookings** computed from `gtm_intel.json`. Only Geo-grain rows have a target — Region/Territory show `—` for Target/vs Target, same null-handling as Coverage. A totals row ('All') closes out the table.
  - `Expected Bookings` = already-booked (from coverage.json) + Σ(`Product_NACV` × `Win_Prob`) over open deals — **must be scoped to `CloseDate` falling in the current quarter first** (derived dynamically from `gtm_coverage.json`'s own snapshot dates, not hardcoded). `gtm_intel.json` holds every open SKU regardless of which quarter it's expected to close in (CloseDates run out to 2027+ and beyond) — without this filter, Expected Bookings silently includes deals closing in future quarters and looks wildly inflated against a single quarter's target. Same root issue as the `coverage.py` CloseDate fix, surfacing again on the deal-scoring side.
  - **`gtm_coverage.json` is multi-quarter now — three call sites must filter on `r.is_current` first**, or a future quarter's row (tagged `week_of_quarter=1`, with a *current* snapshot_date) silently corrupts this tab: `latestCoverageRow()` (picks the max `week_of_quarter` per slice), `currentQuarterBounds()` (derives `[start,end]` from the min `snapshot_date` across all rows), and the rollup's `#sc-rollup-ctx` "as of Week N" label (took a global max week). All three now filter to `is_current` before doing their min/max.
- **No flat opportunity/"Deal table" below the rollup** — it was removed. The rollup's
  Territory (leaf) rows already drill into the full opp list for a team via the team
  drawer (see below), which is the intended path to individual opportunities; the flat
  300-row table was redundant with it. Removing it also dropped the now-unused
  `#sc-table-wrap`/`#sc-tbl-ctx` elements and the `scState.sortKey`/`sortDir` state
  (`scState` is now just `{ risk }`). Browse individual opps via the territory drill-down
  or the Call Sentiment list instead.

**Team drill-down (Territory row → open opportunities → single-opp detail).** Territory
(leaf) rows in the rollup have no expand/collapse chevron, so they're wired to a click
handler instead (`data-team-key="{geo}||{region}||{territory}"`, delegated listener in
`initScoringTab()`) that calls `openTeamDrawer(geo, region, territory)`. This opens the
same `#drawer`/`#drawer-overlay` panel the single-opp view uses, but with
`teamDrawerHtml()`: that team's full `gtm_intel.json` slice (unscoped by CloseDate
quarter — deliberately different from the rollup's own Expected Bookings figure, which
IS quarter-scoped, since this list is for browsing the whole pipeline, not just what's
expected to close this quarter), sorted by `expectedBookings(r) = Product_NACV × Win_Prob`
descending, each row showing Win_Prob, Expected Bookings, the existing `signalDotsHtml()`
call-sentiment dots, and — since `Call_Summaries` is available in `gtm_intel.json` at
this level too — a `.call-badge` (`"N calls"`) next to the Opportunity_Id whenever that
opp has any, so which opps are worth opening for call context is visible before
drilling further in, not just after.

Clicking an opportunity row inside this list drills into the existing single-opp
`drawerHtml()` view, in this section order: Opportunity Overview (including a **Call
Summaries** meta item showing the count, or "None on file"), the **Call Summaries**
section, `Win_Factors`/`Risk_Factors`, then Risk/Win Signals. The Call Summaries
section sits **directly under Opportunity Overview** (moved up from the bottom of the
drawer) — because an opp's signal dots imply call data exists, users expected the
summaries near the top, not below five other sections past the fold. It renders
**all** of `row.Call_Summaries` as one `.call-summary` paragraph per call (numbered
`Call N of M` when there's more than one) whenever that opp has any. Both
`summarySnippet()` (View 5 list) and this section filter `Call_Summaries` to non-empty
strings first (`typeof s === 'string' && s.length`) — the array can contain `null`
entries, and an unguarded `s.length`/`s.toLowerCase()` on one throws and aborts the whole
render.

**Signal-phrase highlighting.** Each summary paragraph is rendered through
`highlightSignals(text, flaggedCols)` (not plain `escapeHtml`): it wraps the exact spans
that tripped a signal in `<mark class="hl-risk">` / `<mark class="hl-win">`, so the reader
sees *which words* drove the risk/win flags, not just the flag. The regexes in
`SIGNAL_PATTERNS` are **ported verbatim from `pipeline/extract_signals.py`'s `PATTERNS`**
(keep the two in sync if either changes) — that guarantees the highlight matches what the
pipeline actually flagged. `flaggedCols` = the opp's fired signal columns
(`[...RISK_COLS, ...WIN_COLS].filter(c => row[c] === 1)`), so only real triggers light up.
The highlighter escapes text around and inside every match and resolves overlaps by
earliest start (risk beats win on a tie) so `<mark>` never nests. A small legend
(`.hl-legend`) shows the risk/win swatches above the paragraphs. This is distinct from the
list's search-term `<mark>` in `summarySnippet()`. Presence of summaries is
checked independently of the existing `hasCallData`/signal-flag check since the two
come from different source files. A `← Back` link (`drawerHtml(row, backTo)`'s optional second
argument, a literal `onclick="..."` string) re-renders the team view via
`reopenTeamDrawer()` — the last-opened team's `(geo, region, territory)` args are
cached in module-level `_teamDrawerArgs` for exactly this purpose. Both views share the
same drawer element; only its `innerHTML` changes, so there's no second panel/overlay
to manage.

**Median, not mean, everywhere Win_Prob is aggregated.** A shared `median()` helper
(sort + middle element, averaging the two middle values on an even count) replaced
every `reduce(...) / length` average across the dashboard — the Deal Scoring KPI
strip, the rollup's per-slice `medianWinProb`, the team drawer's summary stats, and
Call Sentiment's risk-by-geo tree (`riskSlice()`). Deliberate: a handful of near-0%/near-100%
scores can swing a mean hard in a way they can't swing a median, and team-level
"typical deal" reads are what these views are for.

### View 5 — Call sentiment

Source: `output/gtm_intel.json`

The tab answers both "how much / where" (aggregates) and "why" (the actual call text).
These are split across two **in-page sub-tabs** (`#seSubnav`, `.se-panel` panels toggled
by `initSentimentTab()`; both panels stay in the DOM and are re-rendered by
`renderSentimentTab()` regardless of which is visible), because the call list is ~200
rows / ~15,000px tall and, when stacked above the aggregates in a single scroll, buried
every analytical section below the fold. **Overview** now leads (the default panel) so
"whose pipeline has the most risk" is the first thing on the page; **Browse calls** holds
the list on its own. The top-level Geo · Deal Type filters sit above the sub-nav and
scope both panels.

**Overview panel** — now just the risk tree. Several aggregate widgets were removed as
redundant with it and with the drill-down:
- The old KPI strip (Opps with Call Data · Any Risk % · Any Win % · Mixed %) — its at-risk
  count, Any Risk %, and At-Risk Pipe live in the tree at every grain.
- The `02` **Risk signals / Win signals** bar charts and `03` **Net signal distribution**
  histogram — removed. (`barTracks()` / `netDistributionChart()` remain defined but unused.)
- **`01` Risk by Geo / Region / Territory** — the expandable risk tree (see below) is the
  whole panel; click a territory to drill into its at-risk opps and their calls.

**Browse calls panel:**
- **`01` Call summaries** — the browsable/searchable list:
  - A `Search` text box (`#se-search`) that filters to opps where *any* call summary
    contains the term (case-insensitive substring, not a full-text index — this is a
    few thousand rows client-side, no server round-trip needed)
  - A `Signal` pill group (`#se-signal-pills`: All / Any risk / Any win / Mixed) —
    same `risk_score`/`win_score ≥ 1` logic as the KPI strip above, scoped to just this
    list (the aggregate sections below are unaffected by search/signal, only by the
    top-level Geo/Deal Type filters)
  - Sortable columns (click header, same interaction as the Deal Scoring table):
    Opp ID · Product · Geo · Risk · Win · Net — **defaults to Risk descending**, so the
    most concerning opps surface first rather than requiring a manual sort
  - A `Call excerpt` column: the opening line of the first call summary, or — when a
    search term is active — a ~140-char excerpt centered on the first match with the
    match `<mark>`-highlighted (`summarySnippet()`), so the match is visible without
    opening the row
  - Click a row → opens the same single-opp `drawerHtml()` drawer Deal Scoring uses,
    with every call summary, factor, and signal for that opp
  - Capped at 200 rows shown (truncation note below the table) — same pattern as the
    Deal Scoring table's 300-row cap

**Risk-by-geo tree detail** (the Overview panel's `01`) — an expandable Geo → Region →
Territory tree (`geoBreakdownTable()`, same collapse pattern as the other tree tables via
the `treeCollapsed.seGeo` store + `wireTreeToggle('#se-geo-table', 'seGeo', …)`), replacing
the old flat per-geo table. Answers "whose pipeline has the most risk." Columns (trimmed
to just the risk read — Any Win and Median Win Prob were dropped): Opps · At-Risk (opp
count with `risk_score ≥ 1`) · Any Risk % · **At-Risk Pipe** (sum of `open_pipe` over
at-risk opps, rendered with an inline red intensity bar scaled to the largest geo-level
value). **Sorted by At-Risk Pipe descending at every level** so the most-exposed pipeline
surfaces at the top (ignores the fixed `GEO_ORDER` the other trees use — this view is a
risk ranking). Built on the `deduped` (one-row-per-`opp_id`) set: `open_pipe` is stored
**per-opp** (identical across an opp's SKU rows), so it must be summed on the deduped
grain — summing raw `intel` rows would multiply it by the SKU count. `riskSlice()`
computes each row's metrics; the tree is unaffected by the Geo dropdown (always shows all
geos so they stay comparable), only by the Deal Type filter.

**Risk drill-down** — Territory (leaf) rows that have at-risk opps get `.se-drill` +
`data-risk-key="{geo}||{region}||{territory}"` and open `openRiskDrawer()` on click
(delegated on `#se-geo-table`; leaf rows carry no `data-toggle-key`, so it never collides
with the expand/collapse handler). The drawer lists that slice's **at-risk opps only**
(`risk_score ≥ 1`), sorted by `open_pipe` desc (each opp's contribution to the slice's
At-Risk Pipe), showing per opp: Opp ID + High-risk badge (`risk_score ≥ 2`) + a call-count
badge, the signal dots, risk score, and open pipe; header meta = At-Risk Opps · At-Risk
Pipe · With Call Data. It reads from the module-level `_seDeduped` (set by
`renderSentimentTab` to the current Deal-Type–filtered deduped set, so the drawer opens
exactly the opps the tree was built from). Clicking an opp opens the single-opp
`drawerHtml()` view with a `← Back` link (`reopenRiskDrawer()`) — and since Call Summaries
now sit directly under Opportunity Overview, the calls behind the risk are one click and
no scrolling away. Non-at-risk territory rows are intentionally not clickable (the shared
`tr:hover` pointer is neutralized for them).

---

## How the three layers connect at the opp level

```
                    Opportunity_Id
                         │
          ┌──────────────┼──────────────┐
          │              │              │
    Coverage          Win_Prob      Call signals
  (Cal_IACV,         (logistic      (agent layer —
  Stage, week)       regression)     10 binary flags,
                                     not model inputs)
          │              │              │
          └──────────────┴──────────────┘
                         │
                 gtm_intel.parquet
                (one row per open SKU)
```

- Coverage tells you **where the quarter stands in aggregate**
- Win_Prob tells you **which individual deals are most likely to close**
- Call signals tell you **why** — what sentiment is driving or threatening each deal

The three together answer: "Do we have enough pipe, which deals should we prioritize, and what risks are in front of us?"

---

## Design system — current implementation

`dashboard/index.html` shares its design system with the sibling `Coverage Curve
Analysis/frontend/dashboard_template.html` project, for visual consistency across
Strategic Analytics reporting tools. Light mode only — no dark mode toggle.

**Fonts**: `Geist` (UI text) and `Geist Mono` (all numbers, labels, table cells), both
loaded from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500&display=swap" rel="stylesheet">
```

**CSS tokens** (`:root`):
```css
--background:    #ffffff;
--surface:       #ffffff;
--surface-2:     #f8fafc;
--foreground:    #0f172a;
--ink-2:         #334155;
--ink-3:         #64748b;
--ink-4:         #94a3b8;
--primary:       #1e5fbf;
--primary-soft:  #eaf2fd;
--muted:         #f1f5f9;
--border:        #e2e8f0;
--border-strong: #cbd5e1;
--good:          #047857;
--good-soft:     #ecfdf5;
--good-band:     rgba(4, 120, 87, 0.16);
--warn:          #b45309;
--warn-soft:     #fffbeb;
--bad:           #b91c1c;
--bad-soft:      #fee2e2;
--bad-band:      rgba(185, 28, 28, 0.14);
--cur-line:      #1e5fbf;
--cur-area:      rgba(30, 95, 191, 0.10);
--need-line:     #b45309;
--radius:        0.625rem;
--radius-sm:     0.375rem;
--shadow-card:   0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06);
--shadow-pop:    0 8px 24px -4px rgb(15 23 42 / 0.10), 0 2px 6px -1px rgb(15 23 42 / 0.06);
--sans:          'Geist', ui-sans-serif, system-ui, sans-serif;
--mono:          'Geist Mono', ui-monospace, monospace;
```

**KPI strip pattern**: mono meta bar at the top (`.kpi-meta` — quarter + week in
`var(--primary)`, only meaningful on the Coverage tab) sitting above a 4-column
`.kpi-row` grid (`gap: 1px; background: var(--border)` creates hairlines between
cards). Each `.kpi-card` value is 34px Geist Mono with a 16px `.unit` suffix (`M`,
`×`, `%`). Deltas are pill-shaped (`.delta.pos/.neg/.zero`).

**Chart card pattern**: `.chart-card .head` = section number badge (`.num`, mono,
`var(--primary)` on `var(--primary-soft)`) + `<h3>` + right-aligned `.takeaway` +
`.legend` row, then an inline SVG (`svg.chart`). No external charting library —
vanilla SVG only.

**Table pattern** (`table.detail`): Geist Mono `td`, Geist sans `th` (10px
uppercase), `thead` background `var(--surface-2)`, hover row `var(--primary-soft)`.
Geo-grouped rows use `.geo-row-first`/`.geo-row-last`; a `tr.totals` row (bold,
`border-top: 2px solid var(--border-strong)`) closes out a grouped table.

**Filter pill pattern** (`.pill-btn`): ghost by default (transparent bg,
`var(--ink-3)` text), solid `var(--primary)` background with white text when
`data-active="true"`.

**Geo/Region/Territory tree-row pattern** (`.row-geo`/`.row-region`/`.row-territory`,
`.row-chev`): Geo/Region/Territory breakdowns are **always-visible expandable tree
rows inside the table itself, not `<select>` filter dropdowns above it** — modeled on
`GTM Weekly Reporting/gtm-weekly-reporting/output/dashboards/*.html`'s `row-team`/
`row-child` pattern. A `▾` chevron (`.row-chev`, rotates -90° via `.is-collapsed`)
sits in the label cell; clicking anywhere on that row toggles its children via a
single delegated click listener (`wireTreeToggle()`) reading `data-toggle-key` +
`data-toggle-default` off the row, keyed per-table so no two tables' expand state
ever collide (`treeCollapsed = { coverage, balance, pipe, scoring }`). Geo rows
default **expanded**; Region rows default **collapsed**; Territory is the leaf
level (no chevron). Indentation: Region label padding-left 34px, Territory 60px.

The actual row-walking logic (geo ordering, finding each grain's row, building the
chevron/meta markup, recursing into regions/territories, closing with an 'All'
totals row) lives in exactly one place — `treeBody(nodes, store, cells)` — shared by
Coverage, Pipe Balance, and Pipe Create. Each caller just supplies a flat array of
objects carrying `Geo`/`Region`/`Territory` and a `cells(node)` callback that renders
that row's `<td>`s; `treeBody` handles the tree structure and collapse state. This is
why adding two more tree tables (Pipe Balance, Pipe Create) needed no new expand/
collapse code — see `renderCoverageTree`/`renderBalanceTree`/`renderPipeHeat` for the
three `cells()` callbacks that plug into it.

**Pipe Create heatmap pattern** (`td.heat`, `.heat-wrap`): a heatmap is just a
`treeBody()`-rendered `table.detail` with one `<td class="heat">` per week instead of
per-metric, tighter padding (`9px 4px`) and centered text so 15 week-columns fit, and
a horizontal-scroll wrapper (`.heat-wrap { overflow-x: auto }`) so it degrades on
narrow viewports instead of squeezing. Cell color comes from an inline `style`
(background = the tier, text color = the tier) computed by `heatStyle(attainment,
metric)` — no new CSS classes per band, just the existing `--good`/`--warn`/`--bad`
tokens and their `-soft`/`-band` variants. A `.legend` row (reusing `.swatch.area`
from the chart-card legend pattern) explains the five bands above the table.

**Page nav pattern** (`.page-nav`): segmented control (`var(--surface-2)` background,
1px border, 4px padding) with mono `01`–`05` section-number badges (`.num`);
the active button gets `var(--surface)` background + `var(--shadow-card)`, and its
badge switches to `var(--primary-soft)`/`var(--primary)`. Tabs: `01` Coverage, `02`
Pipe Balance, `03` Pipe Create, `04` Deal Scoring, `05` Call Sentiment.

---

## Critical data contract notes — read before any rebuild

1. `Win_Prob` is 0–1. Multiply by 100 to display as a percentage. Never display the raw value.
2. `opp_id` null means no call data. Exclude null `opp_id` rows from all signal statistics. Do not treat null as zero risk.
3. Grain is one row per SKU. Multiple rows share the same `Opportunity_Id` when an opp has multiple products. Deduplicate on `opp_id` for any opp-level aggregation.
4. `Product_NACV` can be negative for downsell SKUs. Handle without crashing.
5. Column name is `Opportunity_Id` — capital I, lowercase d. This exact casing matters for JSON key access.
6. Signal columns have no prefix beyond `risk_`/`win_`. They are `risk_building_own_tool`, `risk_competitor_present`, `risk_needs_business_case`, `win_urgency_signal`, `win_renewal_language`, `win_active_negotiation`, `win_champion_present`, `win_planning_rollout`, `win_stakeholder_aligned`, `win_explicit_commitment`. Not `opp_sig_*`.

**STALE — do not use**: `pipeline_dashboard.json`, `model_metrics.json`,
`enriched_pipeline.json`, `opp_sig_*_ever` column names, `win_probability_pct`. None
of these exist in this repo — if you see them referenced anywhere, the reference is
wrong. (An earlier version of this note also flagged "a 4-tab layout" as stale; that
referred to this same phantom `pipeline_dashboard.json` design, not to today's real
5-tab dashboard — the current tab count is 5: Coverage, Pipe Balance, Pipe Create,
Deal Scoring, Call Sentiment, reading `gtm_intel.json`, `gtm_coverage.json`,
`gtm_timepoints.json`, and `gtm_pipe_create.json`, with the real column names
listed above and in the "Output schema" section.)

---

## Known constraints

- Call signals are a CSV upload — no Synapse connection yet. When Synapse is connected, replace `pd.read_csv` in the notebook's scoring cells with a SQL pull and update the join accordingly (the training cells do not read signals — they are not model features)
- `pull.py` and `pull_call_summaries.py` both require VPN (two different Synapse endpoints — dedicated pool and serverless "Built-in" pool, respectively; see [`tables/call-transcripts.md`](../tables/call-transcripts.md)). All downstream steps (`extract_signals`, the notebook, `coverage`, `pipe_create`) work from cached files — no VPN needed after the initial pulls
- `SYNAPSE_CONN_STR` is read from a `.env` at the repo root via `python-dotenv`, loaded once in `config.py` (`load_dotenv(ROOT / '.env')`). Add `python-dotenv` to requirements and keep `.env` out of git (`.gitignore`) — never hard-code the connection string or commit credentials. `pull_call_summaries.py` derives its serverless connection string from the same `SYNAPSE_CONN_STR` (swaps in `-ondemand` host + `AIDatabase`) rather than needing a second secret
- Signal coverage is 3,359 opps (recomputed from the live pull each run — will drift with call volume). Opps without call data get all signals = 0 after the left join fill — the agent/sentiment layer treats this as "no signal detected." The win-probability model does not use signals at all
- One row per **SKU** in `gtm_intel.parquet` — this is intentional. Each product line is scored separately because products have distinct win rates and feature mixes, and each carries its own `Product_NACV`. Do **not** aggregate to a dominant product.
- Because the grain is SKU, opp-level signal features (the 10 call signals) repeat across every SKU of the same opp — expected. Any opp-level rollup must `COUNT(DISTINCT Opportunity_Id)` and avoid summing signal columns, or it will multi-count multi-product deals.
- **Loading targets — current method.** Targets come from **`Target_Monthly.csv`** (repo root), *not* `FY'26 Targets.xlsx` (superseded — see below). `config.py` reads the CSV **once** into `_TARGETS_RAW` at import (stripping both column names and every object column's values — the raw CSV has stray leading/trailing spaces in column names *and* in `Geo`/`GeoTerritory`/`GeoSubTerritory_AccountOwnerBookingsTeam` values), and a shared `_target_by_team(target_type, quarter_start)` helper filters to one `Target_Type`, derives the 3 month columns from `quarter_start` (e.g. `M202607`+`M202608`+`M202609` for Q3 FY26 — never hardcode the column names), and groups by `GeoSubTerritory_AccountOwnerBookingsTeam`, returning a **team × month** DataFrame (not pre-summed to a quarter total). `load_territory_targets(quarter_start)` sums those 3 months to the Bookings dict `coverage.py` uses; `load_pipe_create_targets(quarter_start)` returns the `Pipeline`/`Opportunities` monthly DataFrames `pipe_create.py` needs (kept monthly, not summed, because its weekly allocator has to prorate partial weeks — see below). That team-name column matches `Bookings_Team_Static` (the live Synapse mapping table) for all but 2 of 28 teams — `APAC Asia AGE`/`APAC Asia SEA`, a recent team split finance hasn't allocated a target to yet (the parent `APAC Asia` team, which still exists as its own `Bookings_Team_Static` row mapped to the same `BTS_Territory`, still has one, so that territory/region isn't fully blank). `coverage.py` then sums `TERRITORY_TARGETS` bottom-up through the `bts` mapping (by `BTS_Region`, then by `Geo`) to get Region/Geo/All targets — **Territory → Region → Geo → All always reconcile exactly** because they're all derived from the same numbers, unlike the old approach below. Verified: Geo-level totals from this method land within ~$4K of the previous `datalake_FY26`-sourced figures (e.g. AMS $14,426,277 vs $14,422,756) — consistent, not a new number to re-justify.
- **Loading pipe-create targets.** `Target_Monthly.csv`'s `Target_Type == 'Pipeline'` rows are the Pipe Create **$** target; `Target_Type == 'Opportunities'` rows are the opp-**count** target — both at the same team/month grain as Bookings. **ASP is never a row in the CSV** — always derive it as `pipe_target / opp_target`. Verified Q3 FY26 totals: $201,789,918 pipe target, 2,843.52 opp target (blended ASP $70,965). `pipe_create.py`'s target rollup groups Territory on **`BTS_Territory`**, not `Bookings_Team_Static` — a one-word difference from `coverage.py`'s Bookings rollup that additionally rolls the 3-team `APAC Asia` territory up correctly (not backported to `coverage.py`, which is PBI-reconciled and unaffected by the quirk at the Bookings grain).
- **14 weeks, not 13 — and two of them are partial.** Q3 FY26 spans Jul 1 – Sep 30: W1 is Jul 1-5 (5 days, since Jul 1 2026 is a Wednesday), W2-W13 are full 7-day weeks, and W14 is Sep 28-30 (3 days). `config.quarter_week()` reproduces the source snapshot table's own `QuarterWeek` column exactly (verified against every in-quarter date) and is used only to build `pipe_create.py`'s forward-looking week calendar — actuals still read the snapshot's real `QuarterWeek` column directly. Any week-count assumption of "13 equal weeks" is wrong for this quarter.
- **Pipe create's target allocation is day-weighted, not `quarter_target / 14`.** A flat divide would make partial W1/W14 (and whichever week is currently in-flight) look artificially behind, and would permanently redden the most-looked-at column. Instead each month's target is allocated across the days of that month falling in each week (`days_in_that_week_and_month / days_in_that_month`), then the whole share is further prorated to `days_counted` (days actually observed) — which is what makes a not-yet-started week's target (and therefore its attainment) collapse to 0/null with no special-casing, and a fully-elapsed week's target come out identical to its unprorated fair share. Verified lossless: summing the fully-elapsed-week allocation across all 14 weeks reproduces the raw unallocated quarterly target exactly ($201,789,918 / 2,843.52).
- **Pipe create's actuals use MIN(snapshot_date) over the FULL frame — buffer included — filtered to the quarter only afterward.** Filtering to in-quarter rows *before* taking the min would credit every opp still alive on day 1 with a `first_seen` of day 1. Verified: without the pre-quarter buffer, 328 opps that actually first appeared in the Jun 18-30 buffer window would resurrect as week-1 creates, overstating it by $22.47M (443 opps/$29.7M instead of the correct 115/$7.19M).
- **Pipe create deliberately has NO CloseDate filter — the inverse of `coverage.py`'s rule, on purpose.** Only 141 of the 514 W1-4 created opps close inside this quarter; the other 373 are next-quarter pipe, which is exactly what pipe *creation* is supposed to count. Applying coverage's CloseDate-in-quarter filter here would understate pipe create by ~3.6×. Also deliberately no stage filter (37 opps arrive already closed — $908K, 2.7% of QTD — and still count as a create) and no `drop_duplicates` (the snapshot feed is verified one row per `Opp_Id` per day, unlike `gtm_intel.json`'s per-SKU grain).
- **Opp-count unit question — unresolved.** The `Opportunities` target (2,843.52 for Q3) is ~5.5× the actual QTD create pace (514 opps), and its `Target_Monthly.csv` rows carry a `Product` dimension that `Bookings`/`Pipeline` rows don't use the same way — finance may be counting opp-**product-lines**, not distinct opps. The dollar-attainment number is trustworthy; opp-count and ASP attainment are not, until reconciled against finance's own definition. The dashboard's Pipe Create tab surfaces this as a visible caveat whenever Opps or ASP is selected — don't remove that caveat without resolving the underlying question first.
- **"1 Week Ago" is the prior week's pinned start-of-week snapshot, not `today − 7` — an explicit user decision, not a default.** The day-relative reading (`_standing_at(mx − 7 days)`) was considered and is a ~$5M / ~0.2× difference from the chosen definition at today's data. If the deck's own "1 Week Ago" figure ever disagrees with this dashboard, check which of the two definitions the deck actually means before assuming a bug.
- **A quarter that hasn't started yet gets a single "current standing" point, not a weekly progression.** `coverage.py`'s `_quarter_rows()` future-branch emits only a `timepoint='current'` row (and a matching `week_of_quarter=1` weekly row) for such a quarter — `day1`/`wk_ago`/`day_ago` rows are absent, not null-valued. This is what lets the Pipe Balance tab render all-dashes for Q4 today with no `if (is_future)` branching anywhere in the render path.
- **Loading targets — superseded method (`FY'26 Targets.xlsx`).** The workbook's `datalake_FY26` sheet only breaks targets out by `Geo` (AMS/EMEA/APAC) — no Region/Territory column exists there at all. `FLM Targets`' "Board Target" tab *does* have team-level (~Territory-level) figures, but its team-name list has drifted significantly from the live `Bookings_Team_Static` values (13 of 28 mismatched — some renames/casing, some genuine org restructuring) and its Q3 total (~$30.8M WW) is ~25% below `datalake_FY26`'s (~$38.4M WW) despite both being internally self-consistent — the two sheets disagree with each other and neither has clean, matchable Territory-level detail. `Target_Monthly.csv` resolved both problems at once (clean team-name match, and Geo totals in line with what was already configured), so the workbook is no longer the target source — kept only as historical context for *why* Region/Territory targets weren't available originally.
- **`coverage.py` must filter snapshot rows to `CloseDate` within `[QUARTER_START, QUARTER_END]` before computing `open_pipe`/`booked`.** `[rep].[trf_opp_daily_snapshot_new]` keeps reporting every historically-Won opp (and every future-dated open opp) in *every day's* snapshot regardless of quarter — `QuarterStartDate` on a snapshot row reflects when the row was *recorded*, not when the opp's own `CloseDate` falls. Without the `CloseDate` filter, `booked` silently accumulates every Won deal ever recorded (some CloseDates go back to 2013) instead of only deals actually closing this quarter — this inflated `booked` to 40–60× the real figure before the fix (e.g. AMS showed $88.6M booked against a $14.4M quarterly target, when the real figure was ~$576K). Same root pattern as `Coverage Curve Analysis/backend/coverage_builder.py`'s `_assign_quarter(CloseDate)`, which scopes every metric (`open_pipe`, `ls_pipe`, `booked`) to the quarter implied by `CloseDate`, not the snapshot's own recording date.
- **Reconciled against Power BI ground truth (`GTM Funnel Metrics 2026 Week 31.pptx`, slides 2 & 12) — three real bugs found and fixed, verified to ~0.2%.** After the CloseDate-in-quarter fix above, `open_pipe` still didn't match the PBI deck's Regional Pipe Coverage (slide 2) or Pipe Movement (slide 12) tables. Three root causes:
  1. **Team-name case mismatch.** The join between `snap.Bookings_Team_static` and `bts.Bookings_Team_Static` was an exact string match. Some historical snapshot rows carry a team name that differs from the live mapping only in casing (e.g. `'EMEA Core Benelux'` vs the live `'EMEA Core BeNeLux'`) — these silently fell into `'Unassigned'` instead of their real region, which is what made `EMEA North`'s week-1 open pipe read ~$2.75M low. Fixed by joining on a `.str.strip().str.lower()` normalized key instead of the raw column (`_team_key` in the code above).
  2. **Slipped deals resurrected as "current" open pipe.** For the in-flight current week, filtering `CloseDate` into the quarter *before* grouping (the same approach used for past weeks) drops a deal's *post-slip* rows entirely once its `CloseDate` moves to a future quarter — leaving `.groupby('Opp_Id').last()` to pick up that deal's *last pre-slip* row (still showing `Stage='Open'`) as if it were today's state. Verified concretely: 215 opps that had slipped out of Q3 by the latest snapshot were being counted as $17.6M of phantom current-quarter open pipe. Fixed by taking each opp's true latest row within the current week's date range *first*, then checking `CloseDate` only on that final row.
  3. **Week 1 (Day 1) falling back to a later date for late-arriving opps.** Some opps first appear in the daily snapshot feed 1-4 days *into* the quarter (created right at the boundary but not captured until the ETL's next run, or genuinely new pipe added that week). The initial week-1 logic fell back to an opp's first available in-quarter row when it had nothing at the exact boundary — which counted these as day-1 pipe. PBI's own "Pre-Q Day 1" figure does **not** count them. Verified concretely: 31 such opps, ~$1.9M of open pipe, matched the observed week-1 gap almost exactly, region by region (e.g. Pubsec: $0.863M vs an observed $0.87M gap). Fixed by removing the fallback entirely — week 1 uses each opp's latest snapshot *at or before* `QUARTER_START` (which `pull.py`'s new `PRE_QUARTER_BUFFER_START` makes available), full stop; opps with nothing before/at the boundary simply aren't counted in week 1, matching PBI. A related trap surfaced while building this: naively using the full pre-quarter buffer resurrected 28 "ghost" opps that exist only in the buffer window and never appear again (closed/reassigned right at the boundary) as phantom week-1 pipe — fixed by restricting the buffer lookup to opps also tracked somewhere in-quarter.
  - **Result after all three fixes**: Week 1 (Pipe Start) total $106.21M vs PBI's $106.38M (0.16% off) — 9 of 13 regions match to the penny. Week 4 (current/Pipe End) total $90.03M vs PBI's $89.86M (0.19% off); coverage 2.49× vs PBI's 2.5× Total. This required a fresh Synapse pull (the buffer wasn't in previously cached data) — `pull.py`'s auth also changed from the interactive-MFA connection string to `AzureCliCredential` (needs `az login` once outside the script; if `az` was just installed and isn't on PATH yet, a terminal restart or an explicit `PATH` prepend of the CLI's install dir — e.g. `C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin` — resolves it without needing the restart).
---

## Handoff

This is the terminal integration file — it has no further handoffs.  
If something is missing or unclear in this file, the answer is in one of the files listed in the header above.  
Do not duplicate logic from those files here — reference them.
