> **ARCHIVED — not context.** See `archive/README.md`. Describes the old
> dashboard project, not this repo.

# Win Probability Model — Implementation

**When to load**: When writing or modifying model training/scoring code.  
**Requires first**: [`models/win-probability-design.md`](win-probability-design.md) — do not write code without reading the design file first.  
**Also load**: [`tables/call-signals.md`](../tables/call-signals.md) if adding or changing signal features.  
**Used by**: [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md) — the pipeline calls `train.py` and `score.py` from this spec.

**Language**: Python, authored in a **Jupyter notebook** (`notebooks/win_probability.ipynb`) — one markdown cell per code block. The runnable, canonical cell-by-cell version lives in [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md); the snippets below are the same logic shown as standalone reference.  
**Key principle**: Simplest code, fewest lines. No unnecessary abstraction.  
**Related**: [`models/win-probability-design.md`](win-probability-design.md) (design decisions) · [`tables/sku-nacv-fact.md`](../tables/sku-nacv-fact.md) · [`tables/opp-daily-snapshot.md`](../tables/opp-daily-snapshot.md)

---

## Dependencies

```python
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import joblib
```

---

## Feature and column definitions

```python
CAT_FEATURES = ['Deal_Type', 'Source', 'Segment', 'Product']
NUM_FEATURES = ['Stage_Age', 'S1_Age', 'NextStep_Age']
TARGET       = 'Won'   # 1 = Closed Won, 0 = Closed Lost/Deferred
OPP_ID       = 'Opportunity_Id'
```

---

## Data loading

Pull from SQL. Apply all business logic mappings from `sku_nacv_fact` in the query itself — do not remap in Python.
Join snapshot age features in SQL as well to keep Python code minimal.

```sql
-- Training data: closed deals only, with current age features joined in.
-- Two CTEs: latest_snapshot (age features) + closed_deals (label + categoricals).
WITH latest_snapshot AS (
    -- Most recent daily snapshot per opp = current age features.
    -- NO quarter filter: training spans every historical closed deal, so the
    -- age pull must reach all quarters. The table is a daily snapshot, so the
    -- latest snapshot_date per opp always carries the most up-to-date ages.
    SELECT Opp_Id, Stage_Age, S1_Age, NextStep_Age
    FROM (
        SELECT
            Opp_Id, Stage_Age, S1_Age, NextStep_Age,
            ROW_NUMBER() OVER (PARTITION BY Opp_Id ORDER BY snapshot_date DESC) AS rn
        FROM [rep].[trf_opp_daily_snapshot_new]
    ) s
    WHERE rn = 1
),
closed_deals AS (
    SELECT
        N.Opportunity_id                                AS Opportunity_Id,
        CASE WHEN N.Deal_Type = 'New Business'
             THEN 'New Customer' ELSE N.Deal_Type END   AS Deal_Type,
        CASE WHEN N.Opportunity_Source_Logic = 'Lead Sourced'
             THEN 'Marketing Sourced'
             ELSE N.Opportunity_Source_Logic END        AS Source,
        N.Segment,
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
        END                                             AS Product,
        CASE WHEN N.StageName IN ('Closed Won','6 - Closed/Pending',
                                  'Stage 5 - Closed Won') THEN 1 ELSE 0
        END                                             AS Won
    FROM [src].[sku_nacv_fact] AS N
    WHERE N.Period               = 'Period_1'
      AND N.NACV_USD            != 0
      AND N.Record_Type         IN ('Product','Service','Platinum support')
      AND N.Deal_Type           IN ('New Business','Expansion','Upsell','Professional services')
      AND N.Booking_Team_Static NOT IN ('Account Management','Global','QAS Account Management')
      AND N.Booking_Team_Static IS NOT NULL
      AND N.StageName           IN (   -- training: Closed Won + Closed Lost only
          'Closed Lost',               -- exclude Closed Deferred — ambiguous outcome
          '6 - Closed/Pending','Closed Won','Stage 5 - Closed Won'
      )
)
SELECT
    c.Opportunity_Id,
    c.Deal_Type,
    c.Source,
    c.Segment,
    c.Product,
    s.Stage_Age,
    s.S1_Age,
    s.NextStep_Age,
    c.Won
FROM closed_deals c
LEFT JOIN latest_snapshot s
    ON s.Opp_Id = c.Opportunity_Id
```

> The `StageName IN (...closed set...)` filter in `closed_deals` replaces the old
> `NOT IN (...junk stages...)` filter — the closed set is stricter, so the junk
> stages are already excluded. `LEFT JOIN` keeps any closed deal that was never
> snapshotted; handle those NULL ages in Python (drop or fill) before fitting.

---

## Build and train — complete script

```python
import pandas as pd
import joblib
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.metrics import roc_auc_score, classification_report

CAT  = ['Deal_Type', 'Source', 'Segment', 'Product']
NUM  = ['Stage_Age', 'S1_Age', 'NextStep_Age']

# df_closed = Closed Won + Closed Lost only (SQL above). No open opps, no Deferred.
X = df_closed[CAT + NUM]
y = df_closed['Won']                       # 1 = Closed Won, 0 = Closed Lost

# Hold out a test set the model never sees during fitting (stratified on outcome)
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.25, stratify=y, random_state=42)

pre = ColumnTransformer([
    ('cat', OneHotEncoder(handle_unknown='ignore'), CAT),
    ('num', StandardScaler(),                       NUM),
])
model = Pipeline([('pre', pre), ('clf', LogisticRegression(max_iter=1000))])

# 1. Cross-validated AUC on the train split — stability estimate
cv_auc = cross_val_score(model, X_train, y_train, cv=5, scoring='roc_auc')
print(f"CV AUC (train): {cv_auc.mean():.3f} ± {cv_auc.std():.3f}")

# 2. Fit on train, validate on the held-out test set — honest generalization
model.fit(X_train, y_train)
test_auc = roc_auc_score(y_test, model.predict_proba(X_test)[:, 1])
print(f"Held-out test AUC: {test_auc:.3f}")
print(classification_report(y_test, model.predict(X_test),
                            target_names=['Closed Lost', 'Closed Won']))

# 3. Refit on ALL closed deals for the deployed model, then save
model.fit(X, y)
joblib.dump(model, 'win_prob_model.pkl')
```

The reported metrics come from the **held-out test set** and cross-validation —
never from the training data. The deployed model is then refit on all closed deals.

---

## Score open pipeline — complete script

```python
import pandas as pd
import joblib

model   = joblib.load('win_prob_model.pkl')
CAT     = ['Deal_Type', 'Source', 'Segment', 'Product']
NUM     = ['Stage_Age', 'S1_Age']

# df_open = open pipeline only, same SQL shape as training minus the Won column
df_open['Win_Prob'] = model.predict_proba(df_open[CAT + NUM])[:, 1]

scores = df_open[['Opportunity_Id', 'Win_Prob']]
scores.to_parquet('scored_opps.parquet', index=False)
```

---

## Evaluation

Evaluation is built into the training script above: **cross-validated AUC** on the
train split plus **held-out test AUC** and a classification report on a test set the
model never saw during fitting. Never evaluate on the training data itself —
in-sample AUC is optimistically biased. Target for initial deployment: test/CV
AUC > 0.70; below 0.65, investigate class imbalance or feature quality.

---

## Class imbalance

If closed deals are heavily imbalanced (e.g. 80% won, 20% lost), add `class_weight='balanced'`:

```python
LogisticRegression(max_iter=1000, class_weight='balanced')
```

One parameter change. Do not reach for SMOTE or resampling unless `class_weight='balanced'` is insufficient.

---

## Inspect feature coefficients

```python
import numpy as np

feat_names = (
    model.named_steps['pre']
         .named_transformers_['cat']
         .get_feature_names_out(CAT).tolist()
    + NUM
)
coefs = model.named_steps['clf'].coef_[0]

pd.Series(coefs, index=feat_names).sort_values().to_frame('coefficient')
```

Positive coefficient → increases win probability.
Negative coefficient → decreases win probability.
Use this to sense-check the model before deploying scores.

---

## Output schema

`scored_opps.parquet`

| Column | Type | Notes |
|--------|------|-------|
| `Opportunity_Id` | string | Renamed from `sku_nacv_fact.Opportunity_id` — joins to `opportunity_live.Id` and snapshot `Opp_Id` |
| `Win_Prob` | float 0–1 | Predicted probability of Closed Won |

---

## What not to do

- Do not scale or encode outside the Pipeline — causes data leakage if you ever retrain
- Do not use raw `Family` or raw `StageName` as features — use the mapped values
- Do not include open pipeline rows in training
- Do not add more features without checking coefficients first — start minimal
- Do not use a more complex model (RandomForest, XGBoost) unless AUC is below 0.65 after tuning
---

## Handoff

- Integrating model output into the dashboard → load [`analysis/gtm-dashboard.md`](../analysis/gtm-dashboard.md)
- Model is performing poorly → re-read [`models/win-probability-design.md`](win-probability-design.md) before changing anything
- Adding new features → load the relevant table file first to confirm column names
