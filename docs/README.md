# GTM Context — Master Index

**This is the entry point. Read this file first on every task.**

Claude Code: this folder is the single source of truth for all Tricentis GTM
data contracts, SQL patterns, model logic, and pipeline code. Load only the
files your specific task needs — do not load everything. The task→file map
below tells you exactly what to load.

**Two exclusions.** `superpowers/specs/` holds design documents and decision
history — it is *not* context, and nothing in it may be cited as fact about the
data or the models. And the project root's `CLAUDE.md` holds the Pipe Create
invariants; those govern Pipe Create work, the hard rules below govern all data
and SQL. If the two ever contradict, stop and surface it rather than choosing.

---

## Hard rules — always apply, no exceptions

1. **Stage determines outcome** — won/lost comes from `StageName`, not dates
2. **`CloseDate`** = when the deal closed or is expected to close · **`CreatedDate`** = when it entered the pipeline
3. **Never use `Amount`** — use `Total_ARR__c` or `NACV__c`
4. **Always `WHERE IsDeleted = 0`** on `opportunity_live`
5. **Never derive geo with a CASE statement** — always join `[sharepoint].[Map_Booking_Team_Static_live]` with `ActiveTeam = 'Active'`
6. **`ISNULL(col, 0)`** on all financial columns in aggregations
7. **`snaplogic_extract_date` and `snap_source_hash`** are ETL infrastructure fields — never use in business queries
8. **`sku_nacv_fact` requires 7 mandatory filters** — missing any one returns junk rows → see [`tables/sku-nacv-fact.md`](tables/sku-nacv-fact.md)
9. **Simplest code, fewest lines** — do not add complexity unless a simpler approach demonstrably fails
10. **Snapshot current-state = the LATEST snapshot** — for any "as of now" read of `[rep].[trf_opp_daily_snapshot_new]`, anchor to the latest row (`ROW_NUMBER() … ORDER BY snapshot_date DESC`, `rn = 1`, per opp) or `MAX(snapshot_date)`; **never `CAST(GETDATE() AS DATE)`** (empty on weekends/pre-ETL) or a stale hardcoded date. Only deliberate time-series/coverage queries iterate `snapshot_date` → see [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md)

---

## Task → file map

| Task | Load these files |
|------|-----------------|
| **What the agent can do, its tools, and where the walls are** | [`agent-architecture.md`](agent-architecture.md) — read before adding a tool or when a capability seems missing |
| **How to COMPUTE something** — no tool covers it | Write a script in `workspace/scratch/`, run it, read it, delete it. Import `pipeline/` modules rather than re-deriving. See [`agent-architecture.md`](agent-architecture.md) "The tools". |
| **How to VERIFY a figure before reporting it** | `pipeline/checks.py` → `checks.run_all(df)`. **There are no golden output numbers** — say which layer backed the number. See [`agent-architecture.md`](agent-architecture.md) "Verification". |
| **What earlier sessions already learned** | `workspace/notes/journal.md` — read at session start; findings, dead ends, open questions. |
| Any SQL query | [`sql/conventions.md`](sql/conventions.md) always, then the relevant table file(s) |
| Ready-made query templates to adapt | [`sql/conventions.md`](sql/conventions.md) → [`sql/patterns.md`](sql/patterns.md) |
| Current opp-level pipeline state | [`sql/conventions.md`](sql/conventions.md) → [`tables/opportunity.md`](tables/opportunity.md) |
| Live product-level bookings or pipeline | [`sql/conventions.md`](sql/conventions.md) → [`tables/sku-nacv-fact.md`](tables/sku-nacv-fact.md) |
| Historical pipeline / trend / QoQ | [`sql/conventions.md`](sql/conventions.md) → [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md) |
| Any geo / territory / region grouping | [`sql/conventions.md`](sql/conventions.md) → [`tables/territory-mapping.md`](tables/territory-mapping.md) |
| Win / loss analysis | [`sql/conventions.md`](sql/conventions.md) → [`tables/opportunity.md`](tables/opportunity.md) |
| Pulling or debugging raw call transcripts | [`tables/call-transcripts.md`](tables/call-transcripts.md) |
| Linking a call to its opp/account/owner (transcript-side dims) | [`tables/transcripts-lookup.md`](tables/transcripts-lookup.md) |
| Call sentiment or signals per opportunity | [`tables/call-transcripts.md`](tables/call-transcripts.md) → [`tables/call-signals.md`](tables/call-signals.md) |
| Coverage curve — open pipe, LTB, coverage WoW | [`analysis/coverage-curve.md`](analysis/coverage-curve.md) → [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md) |
| **Pipe Create — weekly actual vs target, allocation, attainment** | [`models/pipe-create.md`](models/pipe-create.md) → [`tables/target-monthly.md`](tables/target-monthly.md) → [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md) → [`tables/territory-mapping.md`](tables/territory-mapping.md). Root `CLAUDE.md` invariants apply. |
| **Anything involving targets, attainment, or ASP** | [`tables/target-monthly.md`](tables/target-monthly.md) — mandatory load recipe, whitespace and case-collision gotchas |
| Per-AE targets, AE capacity, pipeline per AE, headcount | [`tables/headcount.md`](tables/headcount.md) → [`tables/target-monthly.md`](tables/target-monthly.md) |
| **Where the pipe create TARGET comes from** — the whole derivation, In Q / Pre Q win rates, sales cycle curves, the two run regimes | [`analysis/pipe-create-waterfall.md`](analysis/pipe-create-waterfall.md) — **start at "THE MODEL AS IT STANDS"**; implemented, but derived totals are not reconciled to published |
| **Slip** — how much moves out, **where it lands**, serial slip, tracing one opp | [`analysis/slip.md`](analysis/slip.md) → [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md) |
| **Pre-Q vs In-Q slip**, and what supplies a future quarter vs an in-flight one | [`analysis/slip.md`](analysis/slip.md) — the timing-split and supply/drain sections. **Read before quoting either rate.** |
| Reconciling against the old Excel model / investigating invariant 10 | [`reference/legacy-pipe-create-xlsm.md`](reference/legacy-pipe-create-xlsm.md) |
| Win probability model — design decisions | [`models/win-probability-design.md`](models/win-probability-design.md) |
| Win probability model — writing or modifying code | [`models/win-probability-design.md`](models/win-probability-design.md) → [`models/implementation.md`](models/implementation.md) |
| GTM intelligence dashboard | [`analysis/gtm-dashboard.md`](analysis/gtm-dashboard.md) → all files it references |
| Running the full pipeline | [`analysis/gtm-dashboard.md`](analysis/gtm-dashboard.md) (pipeline section) |
| Adding a new table context file | [`sql/conventions.md`](sql/conventions.md) — follow the same contract format |
| Competitor threat / build-vs-buy risk in deals | [`reference/tricentis-competitive.md`](reference/tricentis-competitive.md) |
| Note change lineage / staleness check on an AE note | [`tables/opportunity-field-history.md`](tables/opportunity-field-history.md) |
| Designing an autonomous loop / scheduled automation | [`loops/loop-engineering.md`](loops/loop-engineering.md) |

---

## File index and handoff map

```
README.md  (you are here)
│
├── agent-architecture.md  ← the AGENT itself, not the data: its three tools, the
│                             pipeline/ modules that replaced the rest, the six
│                             boundaries, and how runs/lineage work.
│                             read before changing what the agent can do
│
├── sql/
│   ├── conventions.md     ← read before ANY SQL; defines stage logic, dates,
│   │                         financial hierarchy, NULL rules, geo join rule
│   └── patterns.md        ← 10 ready-to-run query templates; requires conventions.md
│
├── tables/
│   ├── opportunity.md     ← [sfdc_trf].[opportunity_live] column contracts
│   │                         hands off to → territory-mapping.md (geo join)
│   ├── sku-nacv-fact.md   ← [src].[sku_nacv_fact] product-level bookings
│   │                         hands off to → territory-mapping.md (geo join)
│   │                         hands off to → opp-daily-snapshot.md (age features)
│   ├── opp-daily-snapshot.md  ← [rep].[trf_opp_daily_snapshot_new] point-in-time state
│   │                              hands off to → territory-mapping.md (geo join)
│   │                              hands off to → analysis/coverage-curve.md (usage)
│   ├── target-monthly.md  ← data/Target_Monthly.csv — the ONLY source of target
│   │                         values. Mandatory strip-on-read recipe; GeoTerritory
│   │                         case collisions; no ASP row exists.
│   │                         required by → models/pipe-create.md
│   ├── headcount.md       ← data/Headcount.xlsx — AE count per territory per
│   │                         quarter. The divisor for per-AE targets.
│   │                         pairs with → tables/target-monthly.md
│   ├── territory-mapping.md   ← [sharepoint].[Map_Booking_Team_Static_live]
│   │                              geo/region/territory hierarchy; required by all
│   │                              queries that group by geography
│   ├── call-transcripts.md ← [transcripts_lookup].* (Synapse serverless "Built-in"
│   │                            pool, database AIDatabase) — raw call summaries pull
│   │                            hands off to → tables/call-signals.md (extraction)
│   ├── transcripts-lookup.md ← [transcripts_lookup] dimension tables: Opportunity,
│   │                            Employee, Call_Review, Account (same serverless pool)
│   │                            — call→opp→account/owner bridge; anonymized names,
│   │                            two opp_id formats. relates to → call-transcripts.md
│   └── call-signals.md    ← call_signals_features.csv (derived from call-transcripts.md
│                              via pipeline/extract_signals.py — fully automated pull)
│                              hands off to → models/win-probability-design.md (features)
│
├── models/
│   ├── pipe-create.md             ← pipeline/pipe_create.py: weekly actual-vs-target
│   │                                 pipe creation. Extracted from gtm-dashboard.md,
│   │                                 which now points here. Day-weighted allocation,
│   │                                 MIN(snapshot_date) actuals, no CloseDate filter.
│   │                                 reads from → tables/opp-daily-snapshot.md
│   │                                 reads from → tables/territory-mapping.md
│   ├── win-probability-design.md  ← what the model does, features, data sources
│   │                                 hands off to → models/implementation.md (code)
│   │                                 reads from → tables/sku-nacv-fact.md
│   │                                 reads from → tables/opp-daily-snapshot.md
│   │                                 reads from → tables/call-signals.md
│   └── implementation.md          ← complete Python code; requires design.md first
│                                     hands off to → analysis/gtm-dashboard.md (integration)
│
├── analysis/
│   ├── slip.md             ← what slip is, how much moves, WHERE IT LANDS
│   │                          (destination curves by quarter offset), serial
│   │                          slip, value drift, opp-level tracing. In Q / Pre Q
│   │                          is a TIMING split — read before quoting a rate.
│   │                          reads from → tables/opp-daily-snapshot.md
│   │                          feeds → analysis/pipe-create-waterfall.md (Step 2)
│   ├── pipe-create-waterfall.md ← how the pipe create TARGET is derived: sales cycle
│   │                               weights Q0..Q+8, In Q / Pre Q win rates, roll-up.
│   │                               IMPLEMENTED (pipeline/waterfall_cli.py); start at
│   │                               "THE MODEL AS IT STANDS". Derived totals are NOT
│   │                               reconciled to published.
│   │                               reads from → reference/legacy-pipe-create-xlsm.md
│   │                               feeds → tables/target-monthly.md
│   ├── coverage-curve.md   ← coverage mechanics: open pipe, booked, LTB, WoW
│   │                          reads from → tables/opp-daily-snapshot.md
│   │                          hands off to → analysis/gtm-dashboard.md (integration)
│   └── gtm-dashboard.md    ← ties everything together: pipeline + dashboard spec
│                              reads from → ALL files above
│                              this is the integration layer
│
├── reference/
│   └── tricentis-competitive.md ← what Tricentis sells + competitor landscape + build-vs-buy
│                                   signals; recognizes rival/DIY mentions in call text
│                                   used by → gtm-risk-report skill
│
└── loops/
    └── loop-engineering.md ← tool-agnostic reference for designing autonomous loops
                               (5 primitives + memory); standalone, no dependencies
                               relates to → analysis/gtm-dashboard.md (loop the pipeline)
```

---

## Source tables quick reference

| Table | Alias | Schema | Context file |
|-------|-------|--------|-------------|
| `[sfdc_trf].[opportunity_live]` | `o` | `sfdc_trf` | [`tables/opportunity.md`](tables/opportunity.md) |
| `[src].[sku_nacv_fact]` | `N` | `src` | [`tables/sku-nacv-fact.md`](tables/sku-nacv-fact.md) |
| `[rep].[trf_opp_daily_snapshot_new]` | `snap` | `rep` | [`tables/opp-daily-snapshot.md`](tables/opp-daily-snapshot.md) |
| `[sharepoint].[Map_Booking_Team_Static_live]` | `bts` | `sharepoint` | [`tables/territory-mapping.md`](tables/territory-mapping.md) |
| `[transcripts_lookup].[Call_Review]`/`.[Call_Transcript]` | `cr`/`ct` | `transcripts_lookup` (serverless `AIDatabase`) | [`tables/call-transcripts.md`](tables/call-transcripts.md) |
| `[transcripts_lookup].[Opportunity]`/`.[Employee]`/`.[Account]`/`.[Call_Review]` | `xo`/`te`/`ta`/`cr` | `transcripts_lookup` (serverless `AIDatabase`) | [`tables/transcripts-lookup.md`](tables/transcripts-lookup.md) |
| `call_signals_features.csv` | `signals` | CSV (derived — no longer manual) | [`tables/call-signals.md`](tables/call-signals.md) |
| `[rep].[trf_marketing_opps_dimension]` | `M` | `rep` | used in [`tables/sku-nacv-fact.md`](tables/sku-nacv-fact.md) |
| `[src].[trf_account_dimension]` | `acc` | `src` | used in [`tables/sku-nacv-fact.md`](tables/sku-nacv-fact.md) |
