> **ARCHIVED — not context.** See `archive/README.md`. Describes the old
> dashboard project, not this repo.

# Win Probability Model — Design & Context

**When to load**: Before writing any model code. Defines target variable, features, data sources, train/score split.  
**Requires**: [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) (categorical features) · [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) (age features)  
**Note**: [`tables/call-signals.md`](../tables/call-signals.md) is **not** a feature source — call signals are a separate agent layer, not model inputs.  
**Then load**: [`models/implementation.md`](implementation.md) for the actual Python code  
**Used by**: [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md) (pipeline integration)

**Goal**: Score every open opportunity with a probability of closing won  
**Algorithm**: Logistic regression — chosen for simplicity, interpretability, and auditability  
**Related**: [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) (features) · [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) (age features) · [`models/implementation.md`](implementation.md) (code)

---

## Core principle

Always use the simplest code with the fewest lines possible.
Logistic regression is the right choice here — it is transparent, fast, and easy to retrain.
Do not add complexity (ensembles, deep learning, feature engineering) unless logistic regression demonstrably fails.

---

## Target variable

```
Target = 1  →  Stage      = 'Closed Won'
Target = 0  →  Raw stage  = 'Closed Lost'   (exclude Closed Deferred — ambiguous)
```

- Train only on **decided** closed deals — Closed Won vs Closed Lost. Exclude `Closed Deferred` (pushed out, not a true loss), all `Open`, and all `Other`
- Open pipeline (`Stage = 'Open'`) is the scoring population only, never used in training or test
- `Stage = 'Closed Won'` comes from the bucketed column in `sku_nacv_fact`; the negative class filters raw `StageName = 'Closed Lost'` to keep Deferred out

---

## Features

### Categorical — from `[src].[sku_nacv_fact]`

| Feature | Column | Notes |
|---------|--------|-------|
| Deal Type | `Deal_Type` mapped → `Deal_Type` | New Customer · Expansion · Upsell · Professional services |
| Source | `Opportunity_Source_Logic` mapped → `Source` | Sales Sourced · BDR Sourced · Marketing Sourced · Partner Sourced |
| Segment | `Segment` | Customer tier |
| Product | `Family` mapped → `Product` | Tosca · qTest · Testim · NeoLoad · etc. — use mapped product, not raw Family |

### Call signals are NOT model features

Call signals (`call_signals_features.csv`) are **not** inputs to this logistic
regression. They are handled by a **separate agent layer** that reads each
opportunity's calls, documents them, and flags positive/negative factors tied to
the deal. Do not join them into the training or scoring feature matrix.

They live alongside the model in the output (`gtm_intel.parquet`) purely for the
dashboard sentiment view and the agent's narrative — the model never sees them.
See [`tables/call-signals.md`](../tables/call-signals.md) for how that signal layer is produced and consumed.

### Numeric — from `[rep].[trf_opp_daily_snapshot_new]`

| Feature | Column | Notes |
|---------|--------|-------|
| Stage age | `Stage_Age` | Days in current stage at time of snapshot |
| S1 age | `S1_Age` | Total deal age since stage 1 entry |
| Next steps age | `NextStep_Age` | Days since next steps were last updated |

Join snapshot to sku_nacv_fact on `Opportunity_id = Opp_Id`.
Use the most recent snapshot row per opportunity —
`ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) = 1` in a CTE.
Do **not** filter the snapshot by `QuarterStartDate`: training spans all historical
closed deals, so the age pull must reach every quarter. Because the table is a daily
snapshot, the latest `snapshot_date` per opp always gives the most up-to-date ages.

---

## Data split

| Population | Filter | Role |
|------------|--------|------|
| Train | Closed Won + Closed Lost, 75% (stratified) | Fit the model |
| Test | Closed Won + Closed Lost, 25% held out | Validate — AUC + classification report |
| Scoring | `Stage = 'Open'` | Predict win probability — never in train or test |

- Split the **closed** population (Closed Won vs Closed Lost, Deferred excluded) into train/test, stratified on the target, so the reported metric is measured on deals the model did **not** see during fitting.
- Also run k-fold cross-validation on the train split for a stability estimate.
- Evaluate on the held-out test set only — **never** score the training data (in-sample AUC is optimistically biased).
- After validation, **refit** the final model on all closed deals, then score the open pipeline.
- Open opps are the scoring population only — excluded from both train and test. No leakage by construction.

---

## Preprocessing

| Feature type | Transform |
|-------------|-----------|
| Categorical | `OneHotEncoder(handle_unknown='ignore')` — `handle_unknown='ignore'` drops unseen categories at score time rather than erroring |
| Numeric | `StandardScaler()` |

Use a single `ColumnTransformer` to apply both in one step.
Wrap everything in a `Pipeline` — this is the entire model object, fit once, used for both training and scoring.

---

## Output

The model produces `predict_proba()[:, 1]` — the probability of `Closed Won`.
Write this as `Win_Prob` (float 0–1) back to the scoring output alongside `Opportunity_Id`.

---

## Retraining

Retrain when:
- A new quarter of closed deals is available
- Win rate shifts by more than ~5 percentage points vs model predictions
- New products or deal types appear that weren't in the training set

Retraining is a single `.fit()` call on the updated closed population — no architectural changes needed.
---

## Handoff

- Ready to write code → load [`models/implementation.md`](implementation.md)
- Need feature source details → [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) (categorical) · [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md) (age)
- Call signals are **not** model features → see [`tables/call-signals.md`](../tables/call-signals.md) for the separate agent layer
- Integrating into the full pipeline → load [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md)
