# Disaster Evaluation Service

When a citizen submits a disaster report, the evaluation service decides: **is this real, how bad is it, and what should happen next?**

---

## Pipeline Overview

```
  +-------------------+
  |  Disaster Report  |   citizen submits via app
  +--------+----------+
           |
           v
  +--------+----------+
  | 1. LOAD REPORT    |   read full report from DB
  +--------+----------+
           |
           v
  +--------+----------+     +---------------------------+
  | 2. ENRICHMENT     +---->| 6 external APIs (parallel)|
  +--------+----------+     |  - Traffic    (TomTom)    |
           |                |  - Weather    (OWM)       |
           |                |  - Cameras    (OSM)       |
           |                |  - Facilities (OSM)       |
           |                |  - Population (GeoNames)  |
           |                |  - Images     (CLIP)      |
           |                +---------------------------+
           v
  +--------+-------------------+
  | 3. NEARBY INCIDENT CHECK   |   same type + area + last 15 min?
  +--------+-------------------+
           |
           v
  +--------+----------+
  | 4. BUILD CONTEXT   |   report + enrichment + nearby check
  +--------+----------+       => EvaluationContext
           |
           v
  +--------+----------+
  | 5. FEATURE VECTOR  |   30 numbers for the ML model
  +--------+----------+
           |
           v
  +--------+-----------+     +------------------------+
  | 6. RUN STRATEGIES  +---->| rules engine + XGBoost |
  +--------+-----------+     | (run in parallel)      |
           |                 +------------------------+
           v
  +--------+-----------+
  | 7. ENSEMBLE BLEND  |   combine both strategies + CLIP
  +--------+-----------+
           |
           v
  +--------+-----------+     +----------------------------+
  | 8. IMPACT          +---->| - impact radius (lookup)   |
  +--------+-----------+     | - estimated population     |
                             | - affected roads (TomTom)  |
                             | - affected facilities (OSM)|
                             +----------------------------+
           |
           v
  +--------+-----------+
  | 9. PERSIST         |   write to DB, set status from flag
  +--------+-----------+
           |
           v
  +--------+-----------+
  | 10. PUBLISH        |   send to RabbitMQ (if actionable)
  +---+----------+-----+
      |          |
      v          v
   ACTIVE    MONITORING
  (dispatch)  (ERT reviews)
```

---

## Step-by-Step

### 1. Load Report

Read the disaster report from the `disaster_reports` table.

Key fields used by evaluation:

| Field | Example | Purpose |
|-------|---------|---------|
| `disaster_type` | `"fire"` | Determines services and one-hot encoding |
| `severity` | `HIGH` | Reporter's initial assessment |
| `location` | PostGIS point → `{lat, lon}` | Coordinates for all enrichment queries |
| `people_affected` | `45` | Scale of the disaster |
| `multiple_casualties` | `true` | Severity signal |
| `structural_damage` | `true` | Severity signal |
| `road_blocked` | `true` | Triggers reroute |
| `description` | `"Large fire..."` | Length = credibility signal |
| `photo_urls` | `["https://..."]` | Fed to CLIP for image analysis |

> `app/repositories/disaster_report_repository.py` — `get_report_by_id()`
> PostGIS binary location is converted to a `{lat, lon}` dict via `ST_AsGeoJSON()`.

---

### 2. Enrichment

Six external data sources queried in parallel using the report's lat/lon. Each one is wrapped in try/except — if one fails, the others still work.

| Source | API | What we get | Why it matters |
|--------|-----|------------|----------------|
| Traffic | TomTom Flow Segment | Congestion level (`light` → `severe`), road speed | Heavy traffic = worse impact, validates location |
| Weather | OpenWeatherMap | Temperature, wind speed, condition | Storm + flood = worse; calm + "hurricane" = suspicious |
| Cameras | OSM Overpass | Camera count, types, quality score (0-1) | More cameras = more verifiable area |
| Facilities | OSM Overpass | Hospitals, fire stations, schools, police nearby | What's at risk, what resources are close |
| Population | GeoNames | Nearest populated place, population | Urban vs rural context |
| Images | CLIP (local ML) | Disaster score (0-1), detected disaster type | Does the photo actually look like a disaster? |

If any source fails, that enrichment returns `None` and the evaluation flag may be set to `LIMITED_DATA`.

> `app/services/evaluation/enrichment.py` — `EnrichmentPipeline.enrich()`
> `app/providers/traffic.py`, `app/providers/surveillance.py`, `app/providers/infrastructure.py`, `app/providers/population_density.py`, `app/providers/image_analysis.py`

**Surveillance quality scoring** — cameras aren't just counted. Each one is scored:

```
per_camera_score = 0.35 * indoor_outdoor     (outdoor=1.0, indoor=0.1)
                 + 0.30 * surveillance_type   (CCTV=1.0, ALPR=0.2, guard=0.3)
                 + 0.20 * camera_hardware     (dome=1.0, panning=0.9, fixed=0.5)
                 + 0.15 * (1.0 + operator_bonus)  (+0.15 if Garda/Luas/council)

overall = 0.60 * avg(per_camera_scores) + 0.40 * min(camera_count / 5, 1.0)
```

> `app/providers/surveillance.py` — `_score_cameras()`

---

### 3. Nearby Incident Check

Before evaluating, query the database: **has anyone else reported the same disaster type near this location in the last 15 minutes?**

| Nearby count | Meaning | Flag set |
|--------------|---------|----------|
| 0 | New incident | — |
| 1 | Possibly same incident | `DUPLICATE` |
| 2+ | Multiple people confirm it | `CORROBORATED` |
| Higher severity than nearby | Situation escalating | `ESCALATED` |

Also queries historical outcomes for the area — if this location has a high false alarm rate, confidence gets penalised.

> `app/repositories/disaster_report_repository.py` — `get_recent_reports_near()`
> `app/repositories/disaster_repository.py` — `get_historical_outcomes()`

---

### 4. Build EvaluationContext

Everything from steps 1-3 gets packed into a single dataclass. This is the one input object that both strategies read.

> `app/services/evaluation/base.py` — `EvaluationContext`
> `app/services/evaluation/service.py` — `_build_context()`

---

### 5. Feature Vector

The EvaluationContext is converted into **30 numbers** — the only thing XGBoost sees.

```
Index   Name                        How it's calculated
------  --------------------------  -------------------------------------------
[0-10]  disaster_type one-hot       11 types, alphabetical. 1.0 for match, 0.0 otherwise
[11]    severity_ordinal            LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4
[12]    multiple_casualties         0 or 1
[13]    structural_damage           0 or 1
[14]    road_blocked                0 or 1
[15]    people_affected_log         log(1 + people_affected) — dampens outliers
[16]    hour_sin                    sin(2pi * hour / 24) — cyclical encoding
[17]    hour_cos                    cos(2pi * hour / 24) — so 23:00 is near 01:00
[18]    traffic_congestion_score    light=1, moderate=2, heavy=3, severe=4
[19]    temperature_c               raw celsius from weather API
[20]    wind_speed_kmh              raw km/h from weather API
[21]    weather_condition_score     clear=0, cloudy=1, rain=2, storm=3
[22]    population_density_tier     0-3 based on Dublin bounding boxes (see below)
[23]    reporter_credibility        hardcoded 1.0 (placeholder for future use)
[24]    nearby_report_count         from nearby incident check, capped at 5
[25]    historical_false_alarm_rate 0.0-1.0 — area credibility
[26]    camera_count_nearby         from surveillance, capped at 5
[27]    photo_count                 how many photos attached, capped at 5
[28]    description_length_tier     0 (<20 chars), 1 (<100), 2 (<300), 3 (300+)
[29]    surveillance_quality_score  0.0-1.0 — composite camera quality
```

**Population density tiers** (hardcoded Dublin bounding boxes):

```
Tier 3 — City centre:   53.32-53.38 N, -6.30 to -6.22 W
Tier 2 — Inner suburbs: 53.28-53.42 N, -6.40 to -6.15 W
Tier 1 — Outer Dublin:  53.20-53.55 N, -6.55 to -6.00 W
Tier 0 — Outside Dublin
```

> `app/services/evaluation/features.py` — `build_feature_vector()`, `FEATURE_NAMES`

---

### 6. Run Strategies

Two engines run in parallel on the same EvaluationContext.

#### Rules Engine

Pure if/then logic. No ML. Decides four things:

**Services to send** — lookup by disaster type, then augment:

```
fire     → [fire, medical, police]
flood    → [medical, police]
earthquake → [medical, fire, police]
...
+ structural_damage?  → add "fire"
+ multiple_casualties? → add "medical"
+ road_blocked?       → add "police"
```

**Confidence score** — starts at a base, then adjusted:

```
Base:  LOW=0.55  MEDIUM=0.65  HIGH=0.78  CRITICAL=0.90

Adjustments:
  +0.04  multiple casualties
  +0.03  structural damage
  +0.02  road blocked
  +0.02  people > 10
  +0.03  people > 50 (cumulative, so +0.05 total for 50+)
  +0.08  CLIP disaster score >= 0.75
  +0.05  CLIP disaster score >= 0.50
  +0.02  CLIP disaster score < 0.50
  +0.03  photos present but CLIP unavailable
  +0.02  description > 100 chars
  +0.04  traffic heavy or severe
  +0.04  3+ cameras nearby
  +0.02  1-2 cameras nearby
  +0.03  area false alarm rate <= 10%
  -0.05  area false alarm rate >= 50%
  -0.03  night time (10pm-6am)

Clamped to [0.0, 1.0]
```

**Triggers** (binary go/no-go):

```
Deploy:   severity >= MEDIUM
Reroute:  road blocked  OR  severity >= HIGH
Evacuate: CRITICAL
       OR HIGH + multiple casualties
       OR (tsunami | hurricane) + severity >= MEDIUM
```

**Flag** (first match wins, priority order):

```
1. FALSE_ALARM     — LOW + no flags + 0 people + confidence < 0.58
2. ESCALATED       — worse than anything nearby
3. CORROBORATED    — 2+ reports confirm it
4. DUPLICATE       — 1 report nearby within 15 min
5. LIMITED_DATA    — traffic or weather enrichment failed
6. PENDING_REVIEW  — HIGH/CRITICAL but no supporting evidence
7. NORMAL          — none of the above
```

> `app/services/evaluation/rules_engine.py` — `RulesEngineStrategy`

#### XGBoost ML Model

Takes the 30-number feature vector, predicts severity class (LOW/MEDIUM/HIGH/CRITICAL) with a probability distribution. If its internal confidence is below 0.60, it falls back to the rules engine.

Trained on 10,000 synthetic rows generated from the rules engine + 15% label noise.

```
Training:  scripts/generate_training_data.py → data/synthetic.csv
           scripts/train_model.py → models/severity_classifier_v2.joblib
CV F1:     0.914
CRITICAL recall: 88.3%
```

> `app/services/evaluation/xgboost_strategy.py` — `XGBoostStrategy`
> `app/services/evaluation/features.py` — feature vector input

---

### 7. Ensemble Blend

Both strategies have produced a severity and a confidence. Now we combine them.

**Severity** — weighted ordinal vote:

```
ordinal = 0.60 * rules_severity + 0.40 * xgboost_severity

where LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4
result rounded to nearest tier
```

**Confidence** — three-way split (when photos exist):

```
engine_confidence = (5/7) * rules_confidence + (2/7) * xgboost_confidence
                  = ~71.4% rules + ~28.6% XGBoost

final_confidence  = 0.70 * engine_confidence + 0.30 * clip_disaster_score

Effective split:  50% rules  +  20% XGBoost  +  30% CLIP
```

Without photos, CLIP is excluded and it becomes 71.4% rules / 28.6% XGBoost.

After blending, the ensemble **re-runs the rules engine** with the blended severity to get coherent services, triggers, and flags.

> `app/services/evaluation/ensemble.py` — `EnsembleStrategy`
> `app/services/evaluation/service.py` — `_blend_confidence()`

---

### 8. Impact Assessment

Four things happen after the strategy runs:

**a) Impact radius** — lookup table by `(disaster_type, severity)`:

```
            LOW    MEDIUM   HIGH    CRITICAL
fire        0.5    1.0      2.0     5.0      km
flood       1.0    3.0      8.0     15.0     km
earthquake  5.0    15.0     30.0    60.0     km
hurricane   10.0   30.0     60.0    100.0    km
tornado     0.5    2.0      5.0     10.0     km
tsunami     5.0    15.0     30.0    50.0     km
storm       5.0    15.0     30.0    60.0     km
drought     10.0   30.0     80.0    200.0    km
heatwave    5.0    20.0     50.0    100.0    km
coldwave    5.0    20.0     50.0    100.0    km
```

**b) Estimated population** — area-based calculation:

```
area = pi * radius^2
density = from GeoNames population (or fallback: 4500/km² urban Dublin)
estimate = area * density / 10   (penetration factor — not everyone is affected)
final = max(estimate, reporter_figure, 1)
```

**c) Affected roads** — extracted from the TomTom traffic data already fetched during enrichment. TomTom returns road segment names for the disaster's coordinates (e.g. "O'Connell Street"). No extra API call. If no traffic data, falls back to `"area near (lat, lon)"`.

**d) Affected facilities** — extracted from the infrastructure data already fetched during enrichment. Pulls the human-readable names (e.g. "Dublin Castle Garda Station", "Loreto College Junior School").

> `app/services/evaluation/impact.py` — `determine_impact_radius()`, `estimate_affected_population()`
> `app/services/evaluation/enrichment.py` — `identify_affected_roads()`
> `app/services/evaluation/service.py` — `_extract_facility_names()`

---

### 9. Persist to Database

Write the evaluation result to the `disasters` table. The **flag determines the initial status**:

| Flag | Status | What happens |
|------|--------|-------------|
| `NORMAL` | **ACTIVE** | Disaster on the map, services dispatched |
| `CORROBORATED` | **ACTIVE** | Confirmed, linked to original, confidence boosted |
| `ESCALATED` | **ACTIVE** | Original disaster severity upgraded |
| `LIMITED_DATA` | **MONITORING** | Created but flagged — ERT knows data is incomplete |
| `PENDING_REVIEW` | **MONITORING** | Created but held — ERT must approve before dispatch |
| `FALSE_ALARM` | **ARCHIVED** | Not real — reporter's false count incremented |
| `DUPLICATE` | — | No new disaster — original's confidence boosted instead |

Every evaluation is also snapshot into `evaluation_history[]` on the disaster record for audit.

> `app/services/evaluation/service.py` — `_persist_result()`
> `app/repositories/disaster_repository.py`
> `app/db/models/enums.py` — `EvaluationFlag`, `DisasterStatus`

---

### 10. Publish to RabbitMQ

The evaluation result is published to the `disaster_events` topic exchange with routing key `disaster.evaluated`.

Four queues consume from this exchange:
- `evaluation_queue` — evaluation results
- `coordination_queue` — unit dispatch
- `notification_queue` — alerts
- `reroute_queue` — traffic rerouting

**FALSE_ALARM and DUPLICATE are not published** — there's nothing to act on.

The message includes a `status` field (`"active"` or `"monitoring"`) so consumers know whether to auto-dispatch or wait for ERT review.

> `app/services/rabbitmq_service.py` — `publish_disaster_evaluated()`

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/evaluate/{report_id}` | Run evaluation for a new report |
| `GET` | `/result/{report_id}` | Retrieve a stored evaluation result |
| `POST` | `/reassess/{disaster_id}` | Re-run evaluation with all linked reports |
| `POST` | `/review/{disaster_id}` | ERT approves or rejects a `PENDING_REVIEW` disaster |
| `GET` | `/active-ranked` | All active disasters ranked by severity |

> `app/api/v1/disaster_evaluation.py`

---

## Design Decisions

**Pluggable strategy** — swap `RulesEngineStrategy` for any new strategy without touching anything else. Just implement `BaseEvaluationStrategy.evaluate()`.

**Graceful degradation** — if an enrichment source fails, evaluation continues with `None` for that context. The flag gets set to `LIMITED_DATA`, not a crash.

**Flag vs status** — the flag is the evaluation's conclusion about the report (is it real, a duplicate, etc.). The status is the disaster's lifecycle (active → monitoring → resolved → archived). They're different things. The flag determines the initial status, but status changes over time.

**Three-way confidence blend** — rules engine is the backbone (50%), CLIP provides hard visual evidence (30%), XGBoost adds a learned second opinion (20%). Without photos, rules engine takes 71.4% and XGBoost takes 28.6%.

**Nearby incident check** — before evaluating, we check for other reports of the same type within a geographic radius and 15-minute window. This prevents duplicate dispatches and detects corroboration.

**Audit trail** — every reassessment or ERT review snapshots the prior evaluation into `evaluation_history[]` on the disaster record.

---

## SLA Targets (from spec)

| Metric | Target | Measured |
|--------|--------|----------|
| Single evaluation | 5-10 seconds | ~3.2s avg (6.4s with CLIP) |
| API response p95 | < 200ms | Passing |
| DB query p90 | < 100ms | 68ms (warm connection pool) |
| 4 concurrent evaluations | — | 6.4s total |

---

## Key Files

```
app/services/evaluation/
  service.py              — orchestrator (evaluate, reassess, review)
  base.py                 — EvaluationContext, EvaluationResult, BaseEvaluationStrategy
  rules_engine.py         — deterministic if/then rules
  xgboost_strategy.py     — ML severity classifier
  ensemble.py             — combines rules + XGBoost + CLIP
  features.py             — 30-feature vector builder
  enrichment.py           — 6-source parallel enrichment pipeline
  impact.py               — blast radius + population estimate
  downstream.py           — coordination/reroute clients (noop stubs)

app/providers/
  traffic.py              — TomTom Flow Segment API
  surveillance.py         — OSM Overpass cameras + quality scoring
  infrastructure.py       — OSM Overpass critical facilities
  population_density.py   — GeoNames population lookup
  image_analysis.py       — CLIP zero-shot image classification

app/api/v1/
  disaster_evaluation.py  — REST endpoints + startup wiring

app/db/models/
  enums.py                — EvaluationFlag (7 values), DisasterStatus, DisasterType
  disaster.py             — Disaster ORM model

app/repositories/
  disaster_repository.py  — disaster CRUD, confidence updates, active-ranked
  disaster_report_repository.py — report CRUD, nearby incident check

app/schemas/
  disaster_evaluation_schemas.py — API request/response shapes

scripts/
  generate_training_data.py — 10,000 synthetic rows for XGBoost training
  train_model.py            — train + evaluate the XGBoost model

models/
  severity_classifier_v2.joblib — trained model artifact (30 features)
```
