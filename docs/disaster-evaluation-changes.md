# Disaster Evaluation Service — Change Walkthrough

## Overview

This document walks through every change made to add the disaster evaluation service (DRQ-42). The feature adds a POST endpoint that takes a disaster report ID, runs it through an XGBoost ML classifier, and returns a structured evaluation: predicted severity, confidence score, recommended emergency services, and deployment triggers.

---

## 1. Dependencies (`requirements.txt`)

Seven new packages added:

| Package | Purpose |
|---|---|
| `xgboost` | The ML classifier |
| `scikit-learn` | Label encoding, train/test split, metrics |
| `numpy` | Array handling for model input/output |
| `pandas` | CSV loading in the training script |
| `joblib` | Saving and loading the trained model artifact |
| `imbalanced-learn` | SMOTE oversampling to balance the CRITICAL class during training |
| `gdacs-api` | Fetches real disaster events from the GDACS API for training data |

> **macOS note:** XGBoost requires OpenMP — run `brew install libomp` once before using it.

---

## 2. Configuration (`app/core/config.py`)

One setting added to the `Settings` class:

```python
MODEL_PATH: str = Field(default="models/severity_classifier_v2.joblib")
```

This controls where the app looks for the trained model at startup. Override it via the `.env` file if the artifact lives elsewhere.

---

## 3. Database

### New enums (`app/db/models/enums.py`)

Two new enums added at the bottom of the file:

- **`EvaluationFlag`** — `NORMAL`, `LIMITED_DATA`, `FALSE_ALARM`, `PENDING_REVIEW`
- **`RecommendedService`** — `FIRE_BRIGADE`, `AMBULANCE`, `POLICE`, `RESCUE`

### New model (`app/db/models/disaster_evaluation.py`)

A new SQLAlchemy table `disaster_evaluations` that persists every evaluation result. Key columns:

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `report_id` | UUID (FK) | Links to `disaster_reports` |
| `severity` | String | ML-predicted severity (uppercase) |
| `confidence` | Float | Model confidence [0.0–1.0] |
| `recommended_services` | JSONB | List of service strings |
| `trigger_deploy` | Boolean | Whether to deploy resources |
| `trigger_reroute` | Boolean | Whether to reroute traffic |
| `trigger_evacuation` | Boolean | Whether to evacuate |
| `flag` | String | Evaluation quality flag |
| `strategy_used` | String | `"xgboost_v1"` or `"rules_v1"` |
| `evaluated_at` | DateTime | Timestamp |

### Model registration (`app/db/models/__init__.py`)

The new model and enums are imported here so Alembic and SQLAlchemy can see them.

### Migration (`alembic/versions/add_disaster_evaluations_table.py`)

Creates the `disaster_evaluations` table and the two new Postgres enum types (`evaluationflag`, `recommendedservice`). Uses `checkfirst=True` so it's safe to run multiple times.

```bash
venv/bin/alembic upgrade head
```

---

## 4. Evaluation Service (`app/services/evaluation/`)

This is the core of the feature. Six files, each with a single responsibility.

### `base.py` — Shared contracts

Defines three things everything else depends on:

- **`EvaluationContext`** — dataclass holding all available data for a report (type, severity, casualties, coordinates, enrichment data, etc.)
- **`EvaluationResult`** — fixed-schema dataclass for the output (severity, confidence, services, triggers, flag, strategy used)
- **`BaseEvaluationStrategy`** — abstract base class with one method: `async evaluate(context) -> EvaluationResult`

Any strategy (rules engine or ML) must implement `BaseEvaluationStrategy`. This is the swap point.

### `rules_engine.py` — Deterministic fallback (`strategy_id = "rules_v1"`)

A purely heuristic strategy used in two roles:

1. **Fallback** when the XGBoost model confidence is below 0.60
2. **Service/trigger derivation** — after XGBoost predicts a severity, the rules engine is called with that severity to determine which services to dispatch and whether to trigger deployment, rerouting, or evacuation

Key rules:
- Services are mapped per disaster type (fire → fire_brigade, ambulance, police; tsunami → rescue, police, ambulance; etc.)
- Structural damage adds fire brigade; casualties add rescue; road blocked adds police
- Evacuation triggers at CRITICAL, or HIGH + casualties, or tsunami/hurricane at MEDIUM+
- Confidence is calculated from base values per severity, adjusted for flags and traffic context

### `enrichment.py` — Context enrichment

`EnrichmentPipeline` fetches real-world data before evaluation:

- **Traffic context** — calls TomTom via `TrafficProvider` using the report's coordinates. Returns flow data including congestion level.
- **Weather context** — calls `MockWeatherProvider` (returns static Dublin weather data). Designed to be swapped for a real weather API.

If either provider fails, the pipeline returns `None` for that context rather than crashing — the evaluation continues with reduced data.

### `features.py` — Feature engineering

Single public function: `build_feature_vector(context) -> list[float]`

Converts an `EvaluationContext` into a **24-element numeric vector** that the XGBoost model can consume. Fixed order — changing it invalidates any trained artifact.

| Index | Feature | Notes |
|---|---|---|
| 0–10 | Disaster type one-hot | 11 types: fire, flood, earthquake, … |
| 11 | Severity ordinal | LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4 |
| 12 | multiple_casualties | 0 or 1 |
| 13 | structural_damage | 0 or 1 |
| 14 | road_blocked | 0 or 1 |
| 15 | people_affected_log | log1p(people_affected) |
| 16–17 | hour_sin / hour_cos | Cyclical encoding — handles 23→0 wraparound |
| 18 | traffic_congestion_score | none=0, light=1, moderate=2, heavy=3, severe=4 |
| 19 | temperature_c | 0.0 if no weather context |
| 20 | wind_speed_kmh | 0.0 if no weather context |
| 21 | weather_condition_score | clear=0, cloudy/overcast=1, rain=2, storm=3 |
| 22 | population_density_tier | Dublin city centre=3, inner suburbs=2, outer=1, outside=0 |
| 23 | reporter_credibility | Hardcoded 1.0 |

Also exports `FEATURE_NAMES` — the 24 names in the same order, used by the training script.

### `xgboost_strategy.py` — ML classifier (`strategy_id = "xgboost_v1"`)

The primary evaluation strategy. Loaded once at startup, reused per request.

**Flow for each request:**
1. Build the 24-element feature vector from the context
2. Call `model.predict_proba()` → probability for each of the 4 severity classes
3. Take the class with the highest probability as the prediction
4. If confidence (max probability) **< 0.60** → delegate entirely to `RulesEngineStrategy` and return its result with `strategy_used = "rules_v1"`
5. If confidence **≥ 0.60** → replace context severity with the ML prediction, run `RulesEngineStrategy` with the corrected context to derive services/triggers/flag, return result with `strategy_used = "xgboost_v1"`

The fallback ensures no response is ever degraded — uncertain predictions are handled by the proven rules engine.

### `service.py` — Orchestrator

`DisasterEvaluationService.evaluate(report_id)`:
1. Fetch the disaster report from the DB (404 if not found)
2. Build an `EvaluationContext` from the report fields
3. If the report has coordinates, call `EnrichmentPipeline` to fetch traffic and weather
4. Call the strategy's `evaluate()` method
5. Persist the result via `DisasterEvaluationRepository`
6. Return the result as a dict

---

## 5. Repository (`app/repositories/disaster_evaluation_repository.py`)

Two methods:
- `save(result)` — inserts a new `DisasterEvaluation` row
- `get_evaluations_by_report_id(report_id)` — returns all evaluations for a report, newest first

---

## 6. API (`app/api/v1/disaster_evaluation.py`)

### Endpoint

```
POST /api/v1/disaster-evaluation/evaluate/{report_id}
```

Returns `EvaluationResponse`. 404 if the report doesn't exist. 503 if providers aren't initialised.

### Startup wiring

`set_evaluation_providers(traffic_provider)` is called from `main.py` on startup. It:
1. Stores the traffic provider globally
2. Instantiates `XGBoostStrategy` and calls `.load()` — **fails fast** at startup if the model artifact is missing rather than serving bad results silently
3. Stores the loaded strategy as a global singleton

### Dependency factory

`get_evaluation_service_dependency` wires everything together per request — repositories, strategy singleton, enrichment pipeline. Both globals are guarded with 503 checks.

---

## 7. Schema (`app/schemas/disaster_evaluation_schemas.py`)

`EvaluationResponse` — the Pydantic model returned by the endpoint:

```json
{
  "disaster_id": "uuid",
  "severity": "HIGH",
  "confidence": 0.959,
  "recommended_services": ["fire_brigade", "ambulance"],
  "trigger_deploy": true,
  "trigger_reroute": true,
  "trigger_evacuation": false,
  "flag": "NORMAL",
  "strategy_used": "xgboost_v1",
  "evaluated_at": "2026-02-22T10:00:00Z"
}
```

The schema is identical regardless of which strategy produced the result.

---

## 8. Main App (`app/main.py`)

Two lines added to the lifespan startup:

```python
from app.api.v1.disaster_evaluation import set_evaluation_providers
set_evaluation_providers(traffic_provider)
```

The router is registered:

```python
app.include_router(disaster_evaluation.router, prefix="/api/v1")
```

---

## 9. Training Scripts (`scripts/`)

### `generate_training_data.py`

Generates `data/synthetic.csv` — 10,000 rows of synthetic disaster scenarios.

- Randomly samples disaster type, severity, boolean flags, people affected, coordinates, hour, traffic, and weather
- Runs each scenario through `RulesEngineStrategy` to get a ground-truth severity label
- Applies **15% label noise** (shifts label one tier up or down randomly) to prevent the model from just memorising the rules
- Writes all 24 feature values + label to CSV

```bash
venv/bin/python scripts/generate_training_data.py
```

### `fetch_gdacs_data.py`

Fetches real disaster events from the GDACS API and maps them to the same 24-feature format as the synthetic data.

- Pulls events for types: FL (flood), EQ (earthquake), TC (tropical cyclone), WF (wildfire), VO (volcano), DR (drought)
- Maps GDACS alert levels to severity labels: Green → LOW, Orange → MEDIUM, Red → HIGH; upgrades to CRITICAL if deaths > 50 or total affected > 10,000
- Infers boolean flags (structural damage, road blocked, casualties) from event type and severity
- Skipped event types (e.g. network timeout) are logged and the script continues
- Outputs `data/gdacs_events.csv` — automatically picked up by `train_model.py` if present

```bash
venv/bin/python scripts/fetch_gdacs_data.py
```

### `train_model.py`

Trains the XGBoost classifier and saves the artifact (v2 pipeline).

- Loads `data/synthetic.csv` (required) and `data/gdacs_events.csv` (optional — combined if present)
- Stratified 80/20 hold-out split
- **SMOTE** applied to training fold only to balance the CRITICAL class
- **Stratified 5-fold cross-validation** — reports weighted F1 per fold
- Final model trained on the full resampled training set
- Evaluation on held-out test set: classification report, confusion matrix (ASCII), MCC
- **CRITICAL class recall is the key metric** (achieved 0.870, target ≥ 0.70)
- Saves `models/severity_classifier_v2.joblib` containing the model, label encoder, feature names, and version string

```bash
venv/bin/python scripts/train_model.py
```

---

## 10. Tests

111 tests total, all passing.

| File | Count | What it tests |
|---|---|---|
| `test_rules_engine.py` | 30 | Service mapping, triggers, flags, confidence calculation |
| `test_enrichment_pipeline.py` | 7 | Traffic/weather provider calls, failure handling |
| `test_evaluation_service.py` | 8 | Orchestration, 404 handling, DB persistence |
| `test_disaster_evaluation_api.py` | 9 | HTTP endpoint, response schema, 503 guard |
| `test_features.py` | 45 | Every feature: one-hot encoding, ordinals, cyclical hour, congestion scores, density tiers |
| `test_xgboost_strategy.py` | 12 | load() errors, confident path, fallback path, robustness |

Integration tests mock the database entirely using `dependency_overrides` — no real DB or model file needed to run any tests.

```bash
venv/bin/pytest app/tests/ -v
```

---

## Architecture Diagram

```
POST /evaluate/{report_id}
         │
         ▼
DisasterEvaluationService.evaluate()
         │
         ├─ fetch report from DB (404 if missing)
         │
         ├─ EnrichmentPipeline
         │    ├─ TrafficProvider  → traffic_context
         │    └─ MockWeatherProvider → weather_context
         │
         ▼
  EvaluationContext (13 fields)
         │
         ▼
  XGBoostStrategy.evaluate()
         │
         ├─ build_feature_vector()  →  24-element float list
         │
         ├─ model.predict_proba()   →  [P(CRITICAL), P(HIGH), P(LOW), P(MEDIUM)]
         │
         ├─ confidence ≥ 0.60?
         │    YES → use ML label, run RulesEngine for services/triggers/flag
         │    NO  → delegate entirely to RulesEngineStrategy
         │
         ▼
  EvaluationResult
         │
         ├─ persisted to disaster_evaluations table
         │
         └─ returned as EvaluationResponse JSON
```

