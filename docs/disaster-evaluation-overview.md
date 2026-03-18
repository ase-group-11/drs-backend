# Disaster Evaluation Service — High Level Overview

## What It Does
When a citizen submits a disaster report, the evaluation service automatically decides: **is this real, how bad is it, and what should happen next?**

---

## Pipeline (in order)

```
Report submitted
      ↓
1. LOAD REPORT          — fetch full report dict from DB
                          app/repositories/disaster_report_repository.py
      ↓
2. ENRICH               — parallel fetch of 5 external data sources:
                          • Traffic (LiveMapService)
                            app/services/live_map_service.py
                          • Weather (OpenWeatherMap)
                            app/services/evaluation/enrichment.py
                          • Surveillance cameras (OpenStreetMap Overpass)
                            app/providers/surveillance.py
                          • Population density (GeoNames)
                            app/providers/population_density.py
                          • Nearby infrastructure (hospitals, fire stations, schools)
                            app/providers/infrastructure.py
                          Pipeline orchestrator:
                            app/services/evaluation/enrichment.py
      ↓
3. SLIDING WINDOW       — query reports near same location in last 15 min
   + HISTORICAL           app/repositories/disaster_report_repository.py  (get_recent_reports_near)
                          app/repositories/disaster_repository.py         (get_historical_outcomes)
      ↓
4. BUILD CONTEXT        — assemble everything into EvaluationContext dataclass
                          app/services/evaluation/base.py                 (EvaluationContext)
                          app/services/evaluation/service.py              (_build_context)
      ↓
5. STRATEGY             — pluggable evaluation engine produces EvaluationResult:
                          • RulesEngineStrategy (default / fallback)
                            app/services/evaluation/rules_engine.py
                          • XGBoostStrategy (loads from model artifact)
                            app/services/evaluation/xgboost_strategy.py
                          • Feature extraction (XGBoost only)
                            app/services/evaluation/features.py
                          Base contract:
                            app/services/evaluation/base.py               (BaseEvaluationStrategy, EvaluationResult)
      ↓
6. IMPACT ASSESSMENT    — determine impact radius, estimate population affected,
                          identify affected roads, extract facility names
                          app/services/evaluation/impact.py
                          app/services/evaluation/enrichment.py           (identify_affected_roads)
                          app/services/evaluation/service.py              (_extract_facility_names)
      ↓
7. PERSIST              — write outcome to disasters table (see flags below)
                          app/services/evaluation/service.py              (_persist_result)
                          app/repositories/disaster_repository.py
                          app/db/models/disaster.py
                          app/db/models/enums.py                          (EvaluationFlag, DisasterStatus)
      ↓
<!-- 8. DOWNSTREAM           — fire-and-forget triggers (deploy / reroute / evacuate)
                          app/services/evaluation/service.py              (_dispatch_downstream)
                          app/services/evaluation/downstream.py           (NoopCoordinationClient, NoopRerouteClient) -->
      ↓
<!-- 9. NOTIFY REPORTER      — SMS confirmation via Twilio
                          app/services/evaluation/service.py              (_notify_reporter, _build_reporter_message)
                          app/services/twilio_service.py -->

Set as monitoring instead of active
Remove any notify and downstream. 
Performance and check for scalabilty
Understand the open report - camera data


Image classification 
SLA
```

---

## Evaluation Flags (the 7 outcomes)

| Flag | Meaning | What happens | File |
|------|---------|-------------|------|
| `NORMAL` | Verified, high confidence | Disaster created, services deployed | `app/db/models/enums.py` |
| `LIMITED_DATA` | Verified but enrichment failed | Disaster created, flagged for awareness | `app/services/evaluation/rules_engine.py` |
| `PENDING_REVIEW` | Uncertain — needs ERT sign-off | Disaster created, held until ERT approves/rejects | `app/services/evaluation/rules_engine.py` |
| `DUPLICATE` | Same incident already exists | Boosts original's confidence + updates its fields | `app/services/evaluation/service.py` (`_persist_result`) |
| `CORROBORATED` | 2+ reports confirm same incident | Linked to original, confidence boosted | `app/services/evaluation/service.py` (`_persist_result`) |
| `ESCALATED` | New report has higher severity | Original disaster severity upgraded | `app/services/evaluation/service.py` (`_persist_result`) |
| `FALSE_ALARM` | Not a real disaster | Archived from map, reporter's false count incremented | `app/services/evaluation/service.py` (`_persist_result`) |

---

## Key Design Decisions

- **Pluggable strategy** — swap `RulesEngineStrategy` for `XGBoostStrategy` without touching anything else
  `app/services/evaluation/base.py`, `app/api/v1/disaster_evaluation.py` (`set_evaluation_providers`)
- **Repository pattern** — all DB access returns `dict`, never raw ORM objects
  `app/repositories/disaster_repository.py`, `app/repositories/disaster_report_repository.py`
- **Fire-and-forget** — SMS and downstream triggers never block the evaluation response
  `app/services/evaluation/service.py` (`_dispatch_downstream`, `_notify_reporter`)
- **Graceful degradation** — missing enrichment data → `LIMITED_DATA` flag, not a crash
  `app/services/evaluation/enrichment.py`
- **Audit trail** — every reassess or ERT review snapshots the prior evaluation into `evaluation_history[]`
  `app/repositories/disaster_repository.py` (`update_evaluation_metadata`, `apply_ert_review`)

---

## Entry Points

| Endpoint | Purpose | File |
|----------|---------|------|
| `POST /evaluate/{report_id}` | Run evaluation for a new report | `app/api/v1/disaster_evaluation.py` → `app/services/evaluation/service.py` (`evaluate`) |
| `GET /result/{report_id}` | Retrieve a stored evaluation result | `app/api/v1/disaster_evaluation.py` → `service.py` (`get_evaluation`) |
| `POST /reassess/{disaster_id}` | Re-run evaluation using all linked reports | `app/api/v1/disaster_evaluation.py` → `service.py` (`reassess`) |
| `POST /review/{disaster_id}` | ERT approves or rejects a `PENDING_REVIEW` disaster | `app/api/v1/disaster_evaluation.py` → `service.py` (`review`) |
| `GET /active-ranked` | All active disasters ranked by severity with resource notes | `app/api/v1/disaster_evaluation.py` → `service.py` (`get_active_ranked`) |

---

## Schemas & Config

| Purpose | File |
|---------|------|
| API request/response shapes | `app/schemas/disaster_evaluation_schemas.py` |
| DB models | `app/db/models/disaster.py`, `app/db/models/enums.py` |
| DB migrations | `alembic/versions/add_disaster_evaluations_table.py`, `alembic/versions/add_false_report_count_to_users.py` |
| Settings (model path, API keys) | `app/core/config.py` |
| Startup wiring | `app/main.py`, `app/api/v1/disaster_evaluation.py` (`set_evaluation_providers`) |
