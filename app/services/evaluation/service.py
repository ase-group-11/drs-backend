"""
DisasterEvaluationService — orchestrator for the evaluation pipeline.

Responsibilities:
1. Load the disaster report (dict) from DisasterReportRepository
2. Run the enrichment pipeline (traffic + weather)
3. Build an EvaluationContext
4. Call the injected strategy
5. Persist the result via DisasterEvaluationRepository
6. Notify the reporter via SMS (spec step 10 — notifyReporter)
7. Return a dict matching EvaluationResponse schema
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from geoalchemy2.elements import WKTElement

from app.db.models.disaster import Disaster
from app.db.models.enums import (
    DisasterReportStatus,
    DisasterSeverity,
    DisasterStatus,
    DisasterType,
    EvaluationFlag,
)
from app.repositories.disaster_repository import DisasterRepository
from app.repositories.disaster_report_repository import DisasterReportRepository
from app.repositories.user_repository import UserRepository
from app.services.evaluation.base import BaseEvaluationStrategy, EvaluationContext
from app.services.evaluation.downstream import (
    BaseCoordinationClient,
    BaseRerouteClient,
    NoopCoordinationClient,
    NoopRerouteClient,
)
from app.services.evaluation.enrichment import EnrichmentPipeline
from app.services.evaluation.impact import determine_impact_radius, estimate_affected_population
from app.services.twilio_service import send_sms

logger = logging.getLogger(__name__)


class DisasterEvaluationService:
    """
    Orchestrates disaster report evaluation end-to-end.

    All dependencies are injected — swapping strategy or enrichment
    requires no changes here.
    """

    def __init__(
        self,
        report_repo: DisasterReportRepository,
        disaster_repo: DisasterRepository,
        strategy: BaseEvaluationStrategy,
        enrichment: EnrichmentPipeline,
        user_repo: UserRepository,
        coordination_client: Optional[BaseCoordinationClient] = None,
        reroute_client: Optional[BaseRerouteClient] = None,
    ) -> None:
        self._report_repo = report_repo
        self._disaster_repo = disaster_repo
        self._strategy = strategy
        self._enrichment = enrichment
        self._user_repo = user_repo
        self._coordination = coordination_client or NoopCoordinationClient()
        self._reroute = reroute_client or NoopRerouteClient()

    async def get_evaluation(self, report_id: str) -> Dict[str, Any]:
        """
        Retrieve the evaluation result for a previously evaluated report.

        Reads from disaster_report → disaster_id → disasters.disaster_metadata.

        Raises:
            HTTPException 404 if report not found or not yet evaluated.
        """
        report = await self._report_repo.get_report_by_id(report_id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster report '{report_id}' not found",
            )

        disaster_id = report.get("disaster_id")
        if not disaster_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Report '{report_id}' has not been evaluated yet or was rejected/duplicate",
            )

        disaster = await self._disaster_repo.get_disaster_by_id(disaster_id)
        if disaster is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster record '{disaster_id}' not found",
            )

        meta = (disaster.get("disaster_metadata") or {}).get("evaluation")
        if not meta:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Evaluation metadata not found on disaster record",
            )

        return {
            "disaster_id": report_id,
            "severity": disaster["severity"].upper(),
            "confidence": meta["confidence"],
            "recommended_services": meta["recommended_services"],
            "trigger_deploy": meta["trigger_deploy"],
            "trigger_reroute": meta["trigger_reroute"],
            "trigger_evacuation": meta["trigger_evacuation"],
            "flag": meta["flag"],
            "strategy_used": meta["strategy_used"],
            "evaluated_at": meta["evaluated_at"],
            "impact_radius_km": meta.get("impact_radius_km"),
            "estimated_population": meta.get("estimated_population"),
            "affected_roads": meta.get("affected_roads"),
            "affected_facilities": meta.get("affected_facilities"),
        }

    async def evaluate(self, report_id: str) -> Dict[str, Any]:
        """
        Evaluate a disaster report and return the fixed-schema result dict.

        Args:
            report_id: UUID string of the disaster report

        Returns:
            Dict matching EvaluationResponse schema

        Raises:
            HTTPException 404 if the report does not exist
        """
        # 1. Load report (returns dict, not ORM object)
        report = await self._report_repo.get_report_by_id(report_id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster report '{report_id}' not found",
            )

        # 2. Extract coordinates (may be None)
        lat: Optional[float] = report.get("location", {}).get("lat")
        lon: Optional[float] = report.get("location", {}).get("lon")

        # 3. Enrichment — skipped gracefully if no coordinates
        traffic_ctx: Optional[dict] = None
        weather_ctx: Optional[dict] = None
        surveillance_ctx: Optional[dict] = None
        population_ctx: Optional[dict] = None

        if lat is not None and lon is not None:
            traffic_ctx, weather_ctx, surveillance_ctx, population_ctx, infrastructure_ctx = (
                await self._enrichment.enrich(lat, lon)
            )
        else:
            logger.info(
                "Report %s has no coordinates; skipping enrichment", report_id
            )
            infrastructure_ctx: Optional[dict] = None

        # 4. Sliding window + historical outcomes — both are DB queries, run in parallel
        nearby_reports: list = []
        historical_ctx: Optional[dict] = None

        if lat is not None and lon is not None:
            nearby_reports, historical_ctx = await asyncio.gather(
                self._report_repo.get_recent_reports_near(
                    lat=lat,
                    lon=lon,
                    disaster_type=report["disaster_type"],
                    exclude_report_id=report_id,
                    exclude_user_id=report.get("user_id"),
                ),
                self._disaster_repo.get_historical_outcomes(
                    lat=lat,
                    lon=lon,
                    disaster_type=report["disaster_type"],
                ),
            )

        # 5. Build EvaluationContext
        context = self._build_context(
            report, traffic_ctx, weather_ctx, surveillance_ctx,
            population_ctx, infrastructure_ctx, historical_ctx, nearby_reports,
        )

        # 6. Run strategy
        result = await self._strategy.evaluate(context)

        # 7. Impact assessment (determineImpactRadius / estimateAffectedPopulation /
        #    identifyAffectedRoads) — runs after strategy so severity is known
        impact_radius_km = determine_impact_radius(
            report["disaster_type"], result.severity.lower()
        )
        estimated_population = estimate_affected_population(
            impact_radius_km, report.get("people_affected", 0), population_ctx
        )
        affected_roads = self._enrichment.identify_affected_roads(
            traffic_ctx, lat or 0.0, lon or 0.0
        )
        affected_facilities = _extract_facility_names(infrastructure_ctx)

        # 8. Persist — write to disasters table and update report status
        evaluated_at = datetime.now(timezone.utc)
        tracking_id = await self._persist_result(
            report, result, traffic_ctx, weather_ctx, surveillance_ctx,
            population_ctx, infrastructure_ctx, historical_ctx, evaluated_at,
            impact_radius_km, estimated_population, affected_roads, affected_facilities,
        )

        # 9. Fire downstream triggers — fire-and-forget; never blocks the response
        await self._dispatch_downstream(
            disaster_id=result.disaster_id,
            result=result,
            impact_radius_km=impact_radius_km,
            estimated_population=estimated_population,
            affected_roads=affected_roads,
        )

        # 10. Notify reporter — spec step 10: "System gives confirmation to the
        #    user who reported it."  Fire-and-forget; SMS failure never blocks.
        await self._notify_reporter(report, result, tracking_id)

        # 11. Return response dict (matches EvaluationResponse schema)
        return {
            "disaster_id": result.disaster_id,
            "severity": result.severity,
            "confidence": result.confidence,
            "recommended_services": result.recommended_services,
            "trigger_deploy": result.trigger_deploy,
            "trigger_reroute": result.trigger_reroute,
            "trigger_evacuation": result.trigger_evacuation,
            "flag": result.flag,
            "strategy_used": result.strategy_used,
            "evaluated_at": evaluated_at,
            "impact_radius_km": impact_radius_km,
            "estimated_population": estimated_population,
            "affected_roads": affected_roads,
            "affected_facilities": affected_facilities,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_context(
        report: Dict[str, Any],
        traffic_ctx: Optional[dict],
        weather_ctx: Optional[dict],
        surveillance_ctx: Optional[dict],
        population_ctx: Optional[dict],
        infrastructure_ctx: Optional[dict],
        historical_ctx: Optional[dict],
        nearby_reports: list,
    ) -> EvaluationContext:
        """Convert a report dict + enrichment + window into an EvaluationContext."""
        severity_value: str = report["severity"]
        severity = DisasterSeverity(severity_value)

        lat: Optional[float] = report.get("location", {}).get("lat")
        lon: Optional[float] = report.get("location", {}).get("lon")

        # Derive hour_of_day from report's created_at — not evaluation time
        now = datetime.now(timezone.utc)
        created_at: Optional[datetime] = None
        created_at_str = report.get("created_at")
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                created_at = now

        hour_of_day = created_at.hour if created_at else now.hour
        report_age_minutes = (
            (now - created_at).total_seconds() / 60 if created_at else 0.0
        )

        # Sliding window — derive summary stats from nearby reports
        nearby_count = len(nearby_reports)
        max_nearby_severity: Optional[str] = None
        if nearby_reports:
            sev_order = ["low", "medium", "high", "critical"]
            max_nearby_severity = max(
                nearby_reports,
                key=lambda r: sev_order.index(r["severity"])
            )["severity"]

        description = report.get("description") or ""
        return EvaluationContext(
            report_id=report["id"],
            disaster_type=report["disaster_type"],
            severity=severity,
            description=description,
            people_affected=report.get("people_affected", 0),
            multiple_casualties=report.get("multiple_casualties", False),
            structural_damage=report.get("structural_damage", False),
            road_blocked=report.get("road_blocked", False),
            lat=lat,
            lon=lon,
            hour_of_day=hour_of_day,
            traffic_context=traffic_ctx,
            weather_context=weather_ctx,
            surveillance_context=surveillance_ctx,
            population_context=population_ctx,
            infrastructure_context=infrastructure_ctx,
            historical_context=historical_ctx,
            user_id=report.get("user_id"),
            photo_count=report.get("photo_count", 0),
            description_length=len(description),
            created_at=created_at,
            report_age_minutes=report_age_minutes,
            nearby_report_count=nearby_count,
            max_nearby_severity=max_nearby_severity,
        )

    async def _persist_result(
        self,
        report: Dict[str, Any],
        result,
        traffic_ctx: Optional[dict],
        weather_ctx: Optional[dict],
        surveillance_ctx: Optional[dict],
        population_ctx: Optional[dict],
        infrastructure_ctx: Optional[dict],
        historical_ctx: Optional[dict],
        evaluated_at: datetime,
        impact_radius_km: float = 0.0,
        estimated_population: int = 0,
        affected_roads: Optional[list] = None,
        affected_facilities: Optional[list] = None,
    ) -> Optional[str]:
        """
        Write evaluation outcome to the disasters table and update the report status.

        - FALSE_ALARM → report_status = REJECTED, no disaster record created
        - DUPLICATE   → updateDisasterConfidence on original, report_status = DUPLICATE
        - all others  → insert into disasters, report_status = VERIFIED

        Returns:
            tracking_id of the created/linked disaster, or None for FALSE_ALARM.
        """
        report_id = report["id"]
        flag = result.flag
        lat: Optional[float] = report.get("location", {}).get("lat")
        lon: Optional[float] = report.get("location", {}).get("lon")

        if flag == EvaluationFlag.FALSE_ALARM.name:
            # If a disaster was previously linked to this report, archive it from the live map
            existing_disaster_id = report.get("disaster_id")
            if existing_disaster_id:
                await self._disaster_repo.set_disaster_status(
                    disaster_id=existing_disaster_id,
                    status=DisasterStatus.ARCHIVED,
                )
                logger.info(
                    "Archived disaster %s — report %s re-evaluated as FALSE_ALARM",
                    existing_disaster_id,
                    report_id,
                )
            await self._report_repo.update_report_status(
                report_id=report_id,
                status=DisasterReportStatus.REJECTED,
            )
            # flagReporter: track false report rate on the submitting user
            user_id = report.get("user_id")
            if user_id:
                try:
                    await self._user_repo.increment_false_report_count(user_id)
                    logger.info("Incremented false_report_count for user %s", user_id)
                except Exception:
                    logger.exception("Failed to increment false_report_count for user %s", user_id)
            return None

        if flag == EvaluationFlag.DUPLICATE.name:
            # updateDisasterConfidence: find the original disaster and boost its confidence
            original_id: Optional[str] = None
            if lat is not None and lon is not None:
                original_id = await self._disaster_repo.get_active_disaster_near(
                    lat=lat,
                    lon=lon,
                    disaster_type=report["disaster_type"],
                )

            if original_id:
                await self._disaster_repo.update_confidence(
                    disaster_id=original_id,
                    confidence_boost=0.05,
                )
                # Alt Flow 2: update original disaster's fields if new report has higher values
                await self._disaster_repo.update_disaster_fields_if_higher(
                    disaster_id=original_id,
                    people_affected=report.get("people_affected") or 0,
                    multiple_casualties=report.get("multiple_casualties", False),
                    structural_damage=report.get("structural_damage", False),
                    road_blocked=report.get("road_blocked", False),
                )
                await self._report_repo.update_report_status(
                    report_id=report_id,
                    status=DisasterReportStatus.DUPLICATE,
                    disaster_id=original_id,
                )
            else:
                await self._report_repo.update_report_status(
                    report_id=report_id,
                    status=DisasterReportStatus.DUPLICATE,
                )
            return None

        if flag in (EvaluationFlag.CORROBORATED.name, EvaluationFlag.ESCALATED.name):
            # Find the existing disaster this report corroborates/escalates
            original_id: Optional[str] = None
            if lat is not None and lon is not None:
                original_id = await self._disaster_repo.get_active_disaster_near(
                    lat=lat,
                    lon=lon,
                    disaster_type=report["disaster_type"],
                )

            if original_id:
                # ESCALATED: upgrade the original disaster's severity to the higher value
                new_severity = (
                    result.severity.lower()
                    if flag == EvaluationFlag.ESCALATED.name
                    else None
                )
                await self._disaster_repo.update_confidence(
                    disaster_id=original_id,
                    confidence_boost=0.05,
                    new_severity=new_severity,
                )
                await self._report_repo.update_report_status(
                    report_id=report_id,
                    status=DisasterReportStatus.VERIFIED,
                    disaster_id=original_id,
                )
                logger.info(
                    "Report %s (%s) linked to existing disaster %s",
                    report_id,
                    flag,
                    original_id,
                )
                return None
            # No existing disaster found — fall through to create a new record

        # All other flags (NORMAL, LIMITED_DATA, PENDING_REVIEW) and
        # CORROBORATED/ESCALATED with no existing nearby disaster → create new record
        if lat is None or lon is None:
            logger.warning(
                "Report %s has no coordinates; verified without creating disaster record",
                report_id,
            )
            await self._report_repo.update_report_status(
                report_id=report_id,
                status=DisasterReportStatus.VERIFIED,
            )
            return None

        tracking_id = _generate_tracking_id()

        disaster = Disaster(
            tracking_id=tracking_id,
            type=DisasterType(report["disaster_type"]),
            severity=DisasterSeverity(result.severity.lower()),
            disaster_status=DisasterStatus.ACTIVE,
            location=WKTElement(f"POINT({lon} {lat})", srid=4326),
            location_address=report.get("location_address"),
            description=report.get("description", ""),
            people_affected=report.get("people_affected", 0),
            multiple_casualties=report.get("multiple_casualties", False),
            structural_damage=report.get("structural_damage", False),
            road_blocked=report.get("road_blocked", False),
            disaster_metadata={
                "evaluation": {
                    "confidence": result.confidence,
                    "recommended_services": result.recommended_services,
                    "trigger_deploy": result.trigger_deploy,
                    "trigger_reroute": result.trigger_reroute,
                    "trigger_evacuation": result.trigger_evacuation,
                    "flag": result.flag,
                    "strategy_used": result.strategy_used,
                    "evaluated_at": evaluated_at.isoformat(),
                    "impact_radius_km": impact_radius_km,
                    "estimated_population": estimated_population,
                    "affected_roads": affected_roads or [],
                    "affected_facilities": affected_facilities or [],
                },
                "evaluation_history": [],  # audit trail — prior evaluations appended here on reassess/review
                "enrichment": {
                    "traffic": traffic_ctx,
                    "weather": weather_ctx,
                    "surveillance": surveillance_ctx,
                    "population": population_ctx,
                    "infrastructure": infrastructure_ctx,
                    "historical": historical_ctx,
                },
            },
        )

        created = await self._disaster_repo.create_disaster(disaster)
        await self._report_repo.update_report_status(
            report_id=report_id,
            status=DisasterReportStatus.VERIFIED,
            disaster_id=str(created.id),
        )
        logger.info(
            "Created disaster %s (tracking=%s) from report %s",
            created.id,
            tracking_id,
            report_id,
        )
        return tracking_id


    async def _dispatch_downstream(
        self,
        disaster_id: str,
        result,
        impact_radius_km: float,
        estimated_population: int,
        affected_roads: list,
    ) -> None:
        """
        Fire downstream triggers after a successful evaluation.

        Fire-and-forget — any failure is logged but never surfaces to the caller.
        No-op clients are used until the real services are implemented.
        """
        flag = result.flag
        if flag in (EvaluationFlag.FALSE_ALARM.name, EvaluationFlag.DUPLICATE.name):
            return

        try:
            if result.trigger_deploy:
                await self._coordination.trigger_deploy(
                    disaster_id, result.recommended_services, result.severity
                )
            if result.trigger_reroute:
                await self._reroute.trigger_reroute(disaster_id, affected_roads)
            if result.trigger_evacuation:
                await self._coordination.trigger_evacuation(
                    disaster_id, estimated_population, impact_radius_km
                )
        except Exception:
            logger.exception("Downstream dispatch failed for disaster %s", disaster_id)

    @staticmethod
    def _aggregate_reports(primary: Dict[str, Any], linked: list) -> Dict[str, Any]:
        """
        Merge the incoming report with all existing reports for a disaster.

        Takes the most severe reading across all reports so the evaluation
        reflects the full picture of the incident rather than a single
        reporter's snapshot.
        """
        all_reports = [primary] + linked
        sev_order = ["low", "medium", "high", "critical"]

        return {
            "severity": max(
                (r.get("severity", "low") for r in all_reports),
                key=lambda s: sev_order.index(s) if s in sev_order else 0,
            ),
            "people_affected": max((r.get("people_affected") or 0) for r in all_reports),
            "multiple_casualties": any(r.get("multiple_casualties", False) for r in all_reports),
            "structural_damage": any(r.get("structural_damage", False) for r in all_reports),
            "road_blocked": any(r.get("road_blocked", False) for r in all_reports),
        }

    async def reassess(self, disaster_id: str) -> Dict[str, Any]:
        """
        On-demand re-evaluation of an existing disaster using all its linked reports.

        Aggregates every report attached to this disaster, re-runs enrichment and
        the evaluation strategy, then overwrites the disaster's evaluation metadata.

        Use this endpoint after multiple corroborating reports have been linked to
        a disaster within the 15-minute consolidation window to get an updated
        severity, confidence, and deployment recommendation.

        Raises:
            HTTPException 404 if disaster not found or no reports are linked to it
        """
        disaster = await self._disaster_repo.get_disaster_by_id(disaster_id)
        if disaster is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster '{disaster_id}' not found",
            )

        lat: Optional[float] = disaster.get("location", {}).get("lat")
        lon: Optional[float] = disaster.get("location", {}).get("lon")
        disaster_type: str = disaster.get("type", "other")

        linked_reports = await self._report_repo.get_reports_by_disaster_id(disaster_id)
        if not linked_reports:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No reports linked to disaster '{disaster_id}'",
            )

        # Enrichment + sliding window data — run in parallel where possible
        traffic_ctx: Optional[dict] = None
        weather_ctx: Optional[dict] = None
        surveillance_ctx: Optional[dict] = None
        population_ctx: Optional[dict] = None
        historical_ctx: Optional[dict] = None
        nearby_reports: list = []

        if lat is not None and lon is not None:
            (
                traffic_ctx, weather_ctx, surveillance_ctx,
                population_ctx, infrastructure_ctx,
            ), (
                historical_ctx,
                nearby_reports,
            ) = await asyncio.gather(
                self._enrichment.enrich(lat, lon),
                asyncio.gather(
                    self._disaster_repo.get_historical_outcomes(
                        lat=lat, lon=lon, disaster_type=disaster_type
                    ),
                    self._report_repo.get_recent_reports_near(
                        lat=lat,
                        lon=lon,
                        disaster_type=disaster_type,
                        exclude_report_id=linked_reports[0]["id"],
                        exclude_user_id=linked_reports[0].get("user_id"),
                    ),
                ),
            )

        # Aggregate all linked reports — worst-case severity, max people, OR booleans
        primary = linked_reports[0]
        aggregated = self._aggregate_reports(primary, linked_reports[1:])
        merged_report = {
            **primary,
            **aggregated,
            "id": disaster_id,
            "location": disaster.get("location", {}),
            "location_address": disaster.get("location_address"),
        }

        context = self._build_context(
            merged_report, traffic_ctx, weather_ctx, surveillance_ctx,
            population_ctx, infrastructure_ctx, historical_ctx, nearby_reports,
        )

        result = await self._strategy.evaluate(context)

        impact_radius_km = determine_impact_radius(disaster_type, result.severity.lower())
        estimated_population = estimate_affected_population(
            impact_radius_km, merged_report.get("people_affected", 0), population_ctx
        )
        affected_roads = self._enrichment.identify_affected_roads(
            traffic_ctx, lat or 0.0, lon or 0.0
        )
        affected_facilities = _extract_facility_names(infrastructure_ctx)

        evaluated_at = datetime.now(timezone.utc)
        eval_meta = {
            "confidence": result.confidence,
            "recommended_services": result.recommended_services,
            "trigger_deploy": result.trigger_deploy,
            "trigger_reroute": result.trigger_reroute,
            "trigger_evacuation": result.trigger_evacuation,
            "flag": result.flag,
            "strategy_used": result.strategy_used,
            "evaluated_at": evaluated_at.isoformat(),
            "impact_radius_km": impact_radius_km,
            "estimated_population": estimated_population,
            "affected_roads": affected_roads or [],
            "affected_facilities": affected_facilities or [],
            "corroboration_count": len(linked_reports),
            "reassessed_at": evaluated_at.isoformat(),
        }

        await self._disaster_repo.update_evaluation_metadata(
            disaster_id=disaster_id,
            eval_meta=eval_meta,
            new_severity=result.severity.lower(),
        )

        logger.info(
            "Reassessed disaster %s with %d linked report(s) — severity=%s confidence=%.2f",
            disaster_id, len(linked_reports), result.severity, result.confidence,
        )

        return {
            "disaster_id": disaster_id,
            "severity": result.severity,
            "confidence": result.confidence,
            "recommended_services": result.recommended_services,
            "trigger_deploy": result.trigger_deploy,
            "trigger_reroute": result.trigger_reroute,
            "trigger_evacuation": result.trigger_evacuation,
            "flag": result.flag,
            "strategy_used": result.strategy_used,
            "evaluated_at": evaluated_at,
            "impact_radius_km": impact_radius_km,
            "estimated_population": estimated_population,
            "affected_roads": affected_roads,
            "affected_facilities": affected_facilities,
        }

    async def get_active_ranked(self) -> list:
        """
        Return all active disasters ranked by severity, with resource trade-off notes.

        Alternative Flow 1: Multiple Concurrent Disasters.
        Orders by severity (critical → high → medium → low) then by creation time
        (oldest first so longest-running incidents appear before newer ones of equal severity).

        Each entry includes a `resource_note` field that flags potential resource contention
        when multiple HIGH/CRITICAL disasters are active simultaneously.
        """
        disasters = await self._disaster_repo.get_all_active_disasters()

        _sev_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

        # Count how many high/critical disasters exist for the trade-off note
        high_critical_count = sum(
            1 for d in disasters
            if _sev_rank.get(d.get("severity", "low"), 1) >= 3
        )

        ranked = []
        for rank, d in enumerate(disasters, start=1):
            sev = d.get("severity", "low")
            meta_eval = (d.get("disaster_metadata") or {}).get("evaluation", {})

            resource_note: Optional[str] = None
            if high_critical_count > 1 and _sev_rank.get(sev, 1) >= 3:
                resource_note = (
                    f"{high_critical_count} HIGH/CRITICAL incidents active simultaneously. "
                    "Emergency resources may need to be prioritised across multiple sites."
                )

            ranked.append({
                "rank": rank,
                "disaster_id": d["id"],
                "tracking_id": d.get("tracking_id"),
                "type": d.get("type"),
                "severity": sev.upper(),
                "status": d.get("status"),
                "location_address": d.get("location_address"),
                "people_affected": d.get("people_affected"),
                "confidence": meta_eval.get("confidence"),
                "flag": meta_eval.get("flag"),
                "recommended_services": meta_eval.get("recommended_services", []),
                "trigger_deploy": meta_eval.get("trigger_deploy", False),
                "created_at": d.get("created_at"),
                "resource_note": resource_note,
            })

        return ranked

    async def _notify_reporter(
        self,
        report: Dict[str, Any],
        result,
        tracking_id: Optional[str],
    ) -> None:
        """
        Send an SMS confirmation to the citizen who submitted the report.

        Implements spec step 10: "System gives confirmation to the user who
        reported it." Also covers notifyFalseAlarmReporter() from the
        architecture API spec.

        Fire-and-forget — any exception is caught and logged so the
        evaluation result is never blocked by an SMS failure.
        """
        user_id = report.get("user_id")
        if not user_id:
            logger.warning("Report %s has no user_id; skipping reporter notification", report["id"])
            return

        try:
            user = await self._user_repo.get_by_id(user_id)
            if user is None:
                logger.warning("User %s not found; skipping reporter notification", user_id)
                return

            phone_number = user.phone_number
            message = self._build_reporter_message(result, tracking_id)

            sent = await send_sms(phone_number, message)
            if sent:
                logger.info(
                    "Reporter notification sent to %s (flag=%s, report=%s)",
                    phone_number,
                    result.flag,
                    report["id"],
                )
            else:
                logger.warning(
                    "Reporter notification failed (flag=%s, report=%s)",
                    result.flag,
                    report["id"],
                )
        except Exception:
            logger.exception(
                "Unexpected error sending reporter notification for report %s",
                report["id"],
            )

    @staticmethod
    def _build_reporter_message(result, tracking_id: Optional[str]) -> str:
        """Build the SMS body appropriate for the evaluation outcome."""
        ref = f" (Ref: {tracking_id})" if tracking_id else ""

        if result.flag == EvaluationFlag.FALSE_ALARM.name:
            return (
                f"Dublin City Emergency Services{ref}: Your disaster report has been "
                f"reviewed. After cross-referencing live data, it has been classified "
                f"as a false alarm. No emergency response will be deployed. "
                f"Thank you for your vigilance."
            )

        if result.flag == EvaluationFlag.DUPLICATE.name:
            return (
                f"Dublin City Emergency Services{ref}: Your disaster report has been "
                f"received. It matches an existing incident already being addressed "
                f"by emergency services. No additional action is required from you."
            )

        # Verified outcomes: NORMAL, LIMITED_DATA, PENDING_REVIEW, CORROBORATED, ESCALATED
        parts = [
            f"Dublin City Emergency Services{ref}: Your disaster report "
            f"(severity: {result.severity}) has been verified and is being actioned."
        ]

        if result.trigger_deploy and result.recommended_services:
            services_str = ", ".join(s.capitalize() for s in result.recommended_services)
            parts.append(f"Dispatching: {services_str}.")

        if result.trigger_reroute:
            parts.append("Traffic is being rerouted around the affected area.")

        if result.trigger_evacuation:
            parts.append(
                "An evacuation advisory may be issued for your area. "
                "Follow instructions from emergency personnel."
            )

        parts.append("Stay safe and away from the affected area.")
        return " ".join(parts)


    async def review(
        self,
        disaster_id: str,
        approved: bool,
        reviewed_by_id: str,
        notes: Optional[str],
    ) -> Dict[str, Any]:
        """
        Record an ERT approval or rejection for a PENDING_REVIEW disaster.

        Approved  → triggers deployment (flag→NORMAL, trigger_deploy=True)
        Rejected  → archives the disaster (flag→FALSE_ALARM, status→ARCHIVED)

        Notifies the original reporter by SMS after the decision.

        Raises:
            HTTPException 404 if disaster not found
            HTTPException 409 if disaster is not in PENDING_REVIEW state
        """
        disaster = await self._disaster_repo.get_disaster_by_id(disaster_id)
        if disaster is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Disaster '{disaster_id}' not found",
            )

        current_flag = (disaster.get("disaster_metadata") or {}).get("evaluation", {}).get("flag")
        if current_flag != EvaluationFlag.PENDING_REVIEW.name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Disaster '{disaster_id}' is not awaiting review (current flag: {current_flag})",
            )

        reviewed_at = datetime.now(timezone.utc)
        await self._disaster_repo.apply_ert_review(
            disaster_id=disaster_id,
            approved=approved,
            reviewed_by_id=reviewed_by_id,
            notes=notes,
            reviewed_at=reviewed_at,
        )

        action_taken = (
            "Disaster approved: response teams deployed."
            if approved
            else f"Disaster rejected and archived. Reason: {notes}"
        )
        logger.info("ERT review completed for disaster %s: %s", disaster_id, action_taken)

        # Fire downstream triggers if ERT approved — reads stored flags from metadata
        if approved:
            meta = (disaster.get("disaster_metadata") or {}).get("evaluation", {})
            try:
                await self._coordination.trigger_deploy(
                    disaster_id,
                    meta.get("recommended_services", []),
                    disaster.get("severity", "medium").upper(),
                )
                if meta.get("trigger_reroute"):
                    await self._reroute.trigger_reroute(
                        disaster_id, meta.get("affected_roads", [])
                    )
                if meta.get("trigger_evacuation"):
                    await self._coordination.trigger_evacuation(
                        disaster_id,
                        meta.get("estimated_population", 0),
                        meta.get("impact_radius_km", 0.0),
                    )
            except Exception:
                logger.exception(
                    "Downstream dispatch failed for approved disaster %s", disaster_id
                )

        return {
            "disaster_id": disaster_id,
            "approved": approved,
            "reviewed_by_id": reviewed_by_id,
            "reviewed_at": reviewed_at,
            "action_taken": action_taken,
        }


def _generate_tracking_id() -> str:
    """Generate a user-facing tracking ID e.g. DIS-2026-A3F1B2C4."""
    year = datetime.now(timezone.utc).year
    short = uuid.uuid4().hex[:8].upper()
    return f"DIS-{year}-{short}"


def _extract_facility_names(infrastructure_ctx: Optional[dict]) -> list:
    """
    Extract human-readable facility names from infrastructure enrichment context.

    Returns a list of strings like "St James's Hospital", "Fire Station",
    "Dublin City Police Station" for inclusion in the evaluation response.
    """
    if not infrastructure_ctx:
        return []
    facilities = infrastructure_ctx.get("facilities", [])
    names = []
    for f in facilities:
        label = f.get("name") or f.get("amenity", "").replace("_", " ").title()
        if label:
            names.append(label)
    return names
