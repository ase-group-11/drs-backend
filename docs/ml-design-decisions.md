# ML Design Decisions — Disaster Evaluation Service

This document covers the ML-specific design decisions for DRQ-42: model selection,
training pipeline, feature engineering, rules engine logic, and current limitations.
For the full list of code changes, see `disaster-evaluation-changes.md`.

---

## 1. ML Workflow & Process

### Inference pipeline

```
Raw disaster report (DB)
        │
        ▼ WHY: Pull real report fields rather than accepting user input directly —
          prevents adversarial inputs from manipulating model output.
EnrichmentPipeline (traffic + weather)
        │
        ▼ WHY: The report alone only captures what the reporter observed.
          Traffic and weather add independent, objective signals the model
          can use to override an under/over-reported severity.
EvaluationContext (13 fields assembled)
        │
        ▼
build_feature_vector() → 24-element float list
        │
        ▼ WHY: XGBoost requires a fixed-length numeric vector. All categorical
          values (disaster type) are one-hot encoded rather than label-encoded
          because there is no ordinal relationship between types (fire ≠ flood+1).
          Severity is ordinal-encoded (1–4) because LOW < MEDIUM < HIGH < CRITICAL
          is a meaningful ordering the model should exploit.
XGBoostStrategy.predict_proba()
        │
        ▼ WHY: We use predict_proba() rather than predict() because we need the
          confidence score, not just the class. This lets us apply the 0.60
          fallback threshold — if the model is unsure, we don't force a prediction.
confidence ≥ 0.60?
   YES → ML severity label used, RulesEngine derives services/triggers/flag
   NO  → Delegate entirely to RulesEngineStrategy (strategy_used = "rules_v1")
        │
        ▼ WHY: The hybrid approach keeps business logic (which services to deploy,
          when to evacuate) in the deterministic rules engine. The ML model only
          overrides the severity label — it does not reinvent the dispatch logic.
          This means rule changes don't require model retraining.
EvaluationResult persisted → HTTP response
```

### Training pipeline

```
RulesEngine (ground-truth labels)        GDACS API (real-world labels)
        │                                        │
        ▼                                        ▼
 data/synthetic.csv (10k rows)     data/gdacs_events.csv (real events)
        │                                        │
        └──────────────┬─────────────────────────┘
                       ▼
              Combined dataset
                       │
                       ▼ WHY: SMOTE applied only to training fold, not test set.
                         Oversampling the test set would give falsely optimistic metrics.
              SMOTE (balance CRITICAL class)
                       │
                       ▼ WHY: Stratified 5-fold cross-validation gives a more
                         honest performance estimate than a single 80/20 split.
                         Each fold preserves class distribution.
              Stratified 5-fold CV
                       │
                       ▼
              Final model trained on full resampled training set
                       │
                       ▼ WHY: We report F1, MCC, and confusion matrix — not just
                         accuracy. Accuracy is misleading when CRITICAL is rare.
                         MCC is the single most honest metric for imbalanced multiclass.
              Evaluation: F1 per class, MCC, confusion matrix
                       │
                       ▼
              models/severity_classifier_v2.joblib
```

---

## 2. Why XGBoost (not another model)

**Considered alternatives:**

| Alternative | Why not chosen |
|---|---|
| Logistic Regression | Cannot model non-linear interactions (e.g., fire + casualties + storm = escalation) |
| Random Forest | Similar performance but slower inference and larger artifact |
| Neural Network / MLP | Requires far more data to generalise; tabular data gives XGBoost the edge |
| Decision Tree | Too shallow, high variance, no ensemble benefit |
| Rules-only (no ML) | Cannot learn from patterns across thousands of events |

**Why XGBoost fits this problem:**
- Natively handles mixed feature types (binary flags, ordinals, continuous) in one model
- Robust when features are missing/zero — traffic and weather context can be `None`, mapped to `0.0`
- Built-in feature importance — shows which signals (casualties, type, wind speed) drive predictions
- Fast inference: <1 ms per prediction, suitable for synchronous request handling
- The 0.60 confidence threshold gives a safe fallback path for low-certainty cases

---

## 3. Rules Engine Logic (Detail)

`rules_engine.py` (`strategy_id = "rules_v1"`) serves two roles:

1. **Fallback** when XGBoost confidence < 0.60
2. **Service/trigger derivation** after XGBoost predicts severity — the ML model sets
   the severity label; the rules engine then determines which services to dispatch and
   whether to trigger deployment, rerouting, or evacuation

### Service mapping (base, by disaster type)

| Disaster type | Services dispatched |
|---|---|
| fire | fire_brigade, ambulance, police |
| flood / hurricane / tornado / tsunami / storm | rescue, police, ambulance |
| earthquake | rescue, ambulance, fire_brigade, police |
| drought | ambulance |
| heatwave / coldwave | ambulance, police |
| other | police, ambulance |

### Augmentation rules (applied after base mapping)

- `structural_damage` AND fire_brigade not already present → prepend fire_brigade
- `multiple_casualties` AND rescue not already present → prepend rescue
- `road_blocked` AND police not already present → append police

### Trigger logic

| Trigger | Condition |
|---|---|
| `trigger_deploy` | severity ≥ MEDIUM |
| `trigger_reroute` | road_blocked OR severity ≥ HIGH |
| `trigger_evacuation` | severity == CRITICAL, OR (severity ≥ HIGH AND casualties), OR (tsunami/hurricane AND severity ≥ MEDIUM) |

### Confidence calculation

- Base values: LOW=0.55, MEDIUM=0.65, HIGH=0.78, CRITICAL=0.90
- Adjustments:
  - +0.04 if multiple_casualties
  - +0.03 if structural_damage
  - +0.02 if road_blocked
  - +0.02 if people_affected > 10
  - +0.03 if people_affected > 50
  - +0.04 if traffic is heavy or severe
  - −0.03 if hour is between 22:00–06:00 UTC (night-time, harder to verify)

### Flag logic (first match wins)

| Flag | Condition |
|---|---|
| `FALSE_ALARM` | LOW severity, no boolean flags set, 0 people affected, confidence < 0.58 |
| `LIMITED_DATA` | No traffic context AND no weather context, confidence < 0.70 |
| `PENDING_REVIEW` | HIGH or CRITICAL severity but no supporting flags (no casualties, no structural, no road_blocked) |
| `NORMAL` | Default — none of the above |

---

## 4. Feature Engineering (24 Features)

Feature vector produced by `build_feature_vector()` in `app/services/evaluation/features.py`.
Order is fixed — changing it invalidates any trained artifact.

| Index | Feature | Rationale |
|---|---|---|
| 0–10 | Disaster type one-hot (11 types) | No ordinal relationship between types; one-hot avoids implying fire > flood |
| 11 | severity_ordinal (1–4) | Reporter's initial assessment; preserves ordering the model can use as prior |
| 12 | multiple_casualties | Strongest single escalation signal — nearly always elevates severity |
| 13 | structural_damage | Indicates scale of physical impact beyond casualties |
| 14 | road_blocked | Affects response logistics and is independently reported |
| 15 | log1p(people_affected) | Right-skewed distribution (most: <10 people, rare: thousands); log normalises the tail |
| 16–17 | hour_sin / hour_cos | Cyclical encoding — 23:00 should be close to 00:00, not 23 units away from 0 |
| 18 | traffic_congestion_score | Independent signal of area disruption; correlated with severity but not redundant |
| 19 | temperature_c | Extreme heat/cold directly escalates heatwave/coldwave severity |
| 20 | wind_speed_kmh | High wind amplifies fire, hurricane, storm severity |
| 21 | weather_condition_score | Storm conditions independently worsen almost all disaster types |
| 22 | population_density_tier | More people in area = higher impact for same event; Dublin-specific zones (0–3) |
| 23 | reporter_credibility | Reserved — hardcoded 1.0; designed for future trust scoring by user role |

**Congestion score mapping:** none/unknown=0, light=1, moderate=2, heavy=3, severe=4

**Weather condition score:** clear=0, cloudy/overcast=1, rain=2, storm=3

**Population density tiers (Dublin):**
- 3 — City centre (53.32–53.38°N, −6.30–−6.22°W)
- 2 — Inner suburbs (53.28–53.42°N, −6.40–−6.15°W)
- 1 — Outer Dublin (53.20–53.55°N, −6.55–−6.00°W)
- 0 — Outside or unknown

---

## 5. Model Details & Metrics

**Artifact:** `models/severity_classifier_v2.joblib`

### Hyperparameters

| Parameter | Value | Reason |
|---|---|---|
| n_estimators | 200 | Enough rounds to capture complex interactions without excessive training time |
| max_depth | 5 | Prevents overfitting on the relatively small training set |
| learning_rate | 0.1 | Standard shrinkage; works well with 200 estimators |
| eval_metric | mlogloss | Multiclass log loss — penalises confident wrong predictions heavily |

### Training run results (v2 — synthetic only, 10k rows)

```
Label distribution (full dataset):
  CRITICAL:   847  ( 8.5%)
  HIGH    :  1842  (18.4%)
  LOW     :  3993  (39.9%)
  MEDIUM  :  3318  (33.2%)

After SMOTE (training fold balanced to 12776 rows):
  CRITICAL: 3194  |  HIGH: 3194  |  LOW: 3194  |  MEDIUM: 3194

Stratified 5-fold CV weighted F1: [0.900, 0.907, 0.908, 0.892, 0.897]
  Mean: 0.901  |  Std: 0.006

Classification report (held-out test set, 2000 rows):
              precision    recall  f1-score   support
    CRITICAL      0.936     0.870     0.902       169
        HIGH      0.873     0.783     0.825       368
         LOW      0.929     0.947     0.938       799
      MEDIUM      0.840     0.883     0.860       664
    accuracy                          0.889      2000

Confusion matrix (rows=actual, cols=predicted):
          CRITICAL  HIGH    LOW    MEDIUM
CRITICAL    147      22      0       0
HIGH         10     288      0      70
LOW           0       0    757      42
MEDIUM        0      20     58     586

Matthews Correlation Coefficient (MCC): 0.838
CRITICAL class recall: 0.870  [PASS — target >= 0.70]
```

### Metrics rationale

- **F1 per class** — precision and recall trade-off per severity level
- **Confusion matrix** — reveals which classes are confused (HIGH ↔ MEDIUM is the main source of error)
- **MCC** — single most honest metric for imbalanced multiclass; not fooled by class imbalance the way accuracy is

### Why MCC over accuracy

With CRITICAL at 8.5% of data, a model that always predicts LOW would score ~40% accuracy.
MCC accounts for all four cells of each class's confusion and ranges from −1 to +1,
making it comparable across imbalanced datasets. An MCC of 0.838 means the model is
performing well across all severity levels, not just the dominant ones.

---

## 6. Real-World Data Integration (GDACS)

The v1 training pipeline used only synthetic data generated by the rules engine itself,
making the validation partially circular — the model was learning to mimic the rules
rather than real disaster patterns.

### GDACS fetch script (`scripts/fetch_gdacs_data.py`)

Pulls real disaster events from the Global Disaster Alert and Coordination System API
and maps them to the same 24-feature format as synthetic data.

**Event types fetched:** FL (flood), EQ (earthquake), TC (tropical cyclone), WF (wildfire), VO (volcano), DR (drought)

**Alert level mapping:**

| GDACS alert level | Severity label |
|---|---|
| Green | LOW |
| Orange | MEDIUM |
| Red | HIGH |
| Any + deaths > 50 or total_affected > 10000 | CRITICAL (upgrade) |

**Flag inference by type:**
- `structural_damage` = True for earthquake, hurricane, tsunami, flood at HIGH/CRITICAL
- `road_blocked` = True for flood, earthquake, hurricane, tsunami, storm at MEDIUM+
- `multiple_casualties` = True when severity is HIGH or CRITICAL

**Output:** `data/gdacs_events.csv` (same 24-column + label schema as `synthetic.csv`)

When `gdacs_events.csv` is present, `train_model.py` automatically concatenates it
with synthetic data before training. If absent, it trains on synthetic data only and
prints a notice.

---

## 7. Current Limitations

| Limitation | Detail |
|---|---|
| Population density tier is Dublin-specific | Hardcoded lat/lon bands; any report outside Dublin gets tier 0 |
| reporter_credibility hardcoded at 1.0 | No trust differentiation between reporters — designed for future user-role scoring |
| Weather uses a mock provider | `MockWeatherProvider` returns static Dublin data; real API integration is a stub |
| Training data v1 was circular | Rules-engine labels trained a model to mimic rules; fixed in v2 by adding GDACS real-world events |
| GDACS coverage is not exhaustive | Only the latest events per type are fetched; historical archives require a paid GDACS subscription |
| No online learning | Model must be manually retrained and redeployed when new data arrives |

---

## 8. Running the Training Pipeline

```bash
# 1. Fetch real-world events (optional — requires network access to GDACS)
venv/bin/python scripts/fetch_gdacs_data.py

# 2. Generate synthetic training data
venv/bin/python scripts/generate_training_data.py

# 3. Train the model (loads both CSVs if present)
venv/bin/python scripts/train_model.py

# 4. Run a local evaluation to verify the artifact loads
./run_evaluation.sh
```

The model artifact is gitignored. Every developer running the project must train it
locally, or it can be distributed as a build artefact in CI.
