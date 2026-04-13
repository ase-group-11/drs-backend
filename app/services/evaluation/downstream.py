# """
# Downstream trigger clients for the Disaster Evaluation Service.

# After evaluation the service calls these clients to notify the
# Emergency Coordination Service and Re-route Service of the outcome.

# Both are no-ops until the real services are built.
# To wire in a real service: implement the ABC and pass it to
# DisasterEvaluationService in the dependency factory.
# """

# from __future__ import annotations

# import logging
# from abc import ABC, abstractmethod
# from typing import List

# logger = logging.getLogger(__name__)


# class BaseCoordinationClient(ABC):
#     """Abstract client for the Emergency Coordination Service."""

#     @abstractmethod
#     async def trigger_deploy(
#         self,
#         disaster_id: str,
#         services: List[str],
#         severity: str,
#     ) -> None:
#         """Dispatch emergency services for a verified disaster."""
#         ...

#     @abstractmethod
#     async def trigger_evacuation(
#         self,
#         disaster_id: str,
#         estimated_population: int,
#         impact_radius_km: float,
#     ) -> None:
#         """Initiate evacuation planning for a disaster zone."""
#         ...


# class BaseRerouteClient(ABC):
#     """Abstract client for the Re-route Service."""

#     @abstractmethod
#     async def trigger_reroute(
#         self,
#         disaster_id: str,
#         affected_roads: List[str],
#     ) -> None:
#         """Initiate traffic rerouting around affected roads."""
#         ...


# class NoopCoordinationClient(BaseCoordinationClient):
#     """
#     No-op placeholder — does nothing until the Emergency Coordination
#     Service is implemented and injected.
#     """

#     async def trigger_deploy(self, disaster_id, services, severity) -> None:
#         logger.debug(
#             "NoopCoordinationClient.trigger_deploy: disaster=%s services=%s severity=%s",
#             disaster_id, services, severity,
#         )

#     async def trigger_evacuation(self, disaster_id, estimated_population, impact_radius_km) -> None:
#         logger.debug(
#             "NoopCoordinationClient.trigger_evacuation: disaster=%s population=%d radius=%.1f",
#             disaster_id, estimated_population, impact_radius_km,
#         )


# class NoopRerouteClient(BaseRerouteClient):
#     """
#     No-op placeholder — does nothing until the Re-route Service is
#     implemented and injected.
#     """

#     async def trigger_reroute(self, disaster_id, affected_roads) -> None:
#         logger.debug(
#             "NoopRerouteClient.trigger_reroute: disaster=%s roads=%s",
#             disaster_id, affected_roads,
#         )



"""
Downstream trigger clients for the Disaster Evaluation Service.

After evaluation the service calls these clients to notify the
Emergency Coordination Service and Re-route Service of the outcome.

Both are no-ops until the real services are built.
To wire in a real service: implement the ABC and pass it to
DisasterEvaluationService in the dependency factory.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

logger = logging.getLogger(__name__)


class BaseCoordinationClient(ABC):
    """Abstract client for the Emergency Coordination Service."""

    @abstractmethod
    async def trigger_deploy(
        self,
        disaster_id: str,
        services: List[str],
        severity: str,
    ) -> None:
        """Dispatch emergency services for a verified disaster."""
        ...

    @abstractmethod
    async def trigger_evacuation(
        self,
        disaster_id: str,
        estimated_population: int,
        impact_radius_km: float,
    ) -> None:
        """Initiate evacuation planning for a disaster zone."""
        ...


class BaseRerouteClient(ABC):
    """Abstract client for the Re-route Service."""

    @abstractmethod
    async def trigger_reroute(
        self,
        disaster_id: str,
        affected_roads: List[str],
        lat: float, 
        lon: float, 
    ) -> None:
        """Initiate traffic rerouting around affected roads."""
        ...


class NoopCoordinationClient(BaseCoordinationClient):
    """
    No-op placeholder — does nothing until the Emergency Coordination
    Service is implemented and injected.
    """

    async def trigger_deploy(self, disaster_id, services, severity) -> None:
        logger.debug(
            "NoopCoordinationClient.trigger_deploy: disaster=%s services=%s severity=%s",
            disaster_id, services, severity,
        )

    async def trigger_evacuation(self, disaster_id, estimated_population, impact_radius_km) -> None:
        logger.debug(
            "NoopCoordinationClient.trigger_evacuation: disaster=%s population=%d radius=%.1f",
            disaster_id, estimated_population, impact_radius_km,
        )


class NoopRerouteClient(BaseRerouteClient):
    """
    No-op placeholder — does nothing until the Re-route Service is
    implemented and injected.
    """

    async def trigger_reroute(self, disaster_id, affected_roads, lat = 0.0, lon = 0.0) -> None:
        logger.debug(
            "NoopRerouteClient.trigger_reroute: disaster=%s roads=%s lat=%.4f lon=%.4f",
            disaster_id, affected_roads, lat, lon
        )

class HttpRerouteClient(BaseRerouteClient):
    """
    Real implementation — calls the ReRoute Service API directly.

    Called by DisasterEvaluationService._dispatch_downstream() when
    trigger_reroute=True after a disaster is evaluated as ACTIVE.

    The reroute trigger endpoint expects:
        POST /api/v1/reroute/trigger
        {
            "disaster_id": "<uuid>",
            "region_id":   "<region>",
            "affected_roads": [...]
        }

    region_id is derived from the disaster location — defaults to
    region-dublin-m50 for Dublin-area disasters.
    """

    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self._base_url = base_url.rstrip("/")

    async def trigger_reroute(
        self,
        disaster_id: str,
        affected_roads: List,
        lat: float, 
        lon: float
    ) -> None:
        """
        POST /api/v1/reroute/trigger with disaster_id and affected roads.

        affected_roads from the evaluation pipeline is a list of road name
        strings (e.g. ["M50 Northbound J6-J7"]). We convert them to the
        RoadSegmentInput format the reroute API expects.
        """
        import aiohttp

        # Derive region_id from disaster — defaulting to M50 region
        # In production this would use the disaster location to pick the region
        region_id = "region-dublin-m50"

        # Convert affected_roads (list of dicts with coords) to RoadSegmentInput
        road_segments = []
        for i, road in enumerate(affected_roads or []):
            if isinstance(road, dict):
                # Full dict from identify_affected_roads_async — has real coords
                road_segments.append({
                    "segment_id": road.get("segment_id", f"eval-seg-{disaster_id[:8]}-{i}"),
                    "road_name": road.get("road_name", f"Road {i+1}"),
                    "start_lat": road.get("start_lat", 53.302),
                    "start_lng": road.get("start_lng", -6.3615),
                    "end_lat":   road.get("end_lat", 53.338),
                    "end_lng":   road.get("end_lng", -6.351),
                    "status": "closed",
                    "reason": "disaster",
                    "capacity": 300,
                })
            elif isinstance(road, str):
                # Fallback — string road name with no coords
                road_segments.append({
                    "segment_id": f"eval-seg-{disaster_id[:8]}-{i}",
                    "road_name": road,
                    "start_lat": 53.302,
                    "start_lng": -6.3615,
                    "end_lat": 53.338,
                    "end_lng": -6.351,
                    "status": "closed",
                    "reason": "disaster",
                    "capacity": 300,
                })

        payload = {
            "disaster_id": disaster_id,
        }
        if road_segments:
            payload["affected_roads"] = road_segments

        url = f"{self._base_url}/api/v1/reroute/trigger"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        logger.info(
                            "HttpRerouteClient: reroute triggered for disaster=%s "
                            "vehicles=%s routes=%s",
                            disaster_id,
                            data.get("vehicles_affected"),
                            data.get("routes_count"),
                        )
                    else:
                        body = await response.text()
                        logger.warning(
                            "HttpRerouteClient: trigger returned %s for disaster=%s — %s",
                            response.status, disaster_id, body[:200],
                        )
        except Exception as e:
            logger.error(
                "HttpRerouteClient: failed to trigger reroute for disaster=%s — %s",
                disaster_id, e,
            )
            # Never raise — downstream failures must not block evaluation response

# ─────────────────────────────────────────────────────────────────────────────
# DirectCoordinationClient
# ─────────────────────────────────────────────────────────────────────────────

class DirectCoordinationClient(BaseCoordinationClient):
    """
    Real implementation of BaseCoordinationClient that calls the service layer
    directly — no HTTP round-trips, no auth tokens needed.

    trigger_deploy:
        1. Maps recommended_services (["fire", "medical", "police"]) to unit types
        2. Queries EmergencyUnitService.list_available_units(disaster_id, unit_type)
           PostGIS orders results by nearest station automatically
        3. Picks top N units per type based on severity
        4. Calls DeploymentService.dispatch_units(disaster_id, unit_ids, priority)
        5. Publishes disaster.dispatched RabbitMQ event

    trigger_evacuation:
        1. Calls EvacuationService.plan_evacuation(disaster_id, auto_approve=True)
        2. Calls EvacuationService.activate_evacuation(plan_id)

    Both methods are fire-and-forget — failures are logged but never raised.
    """

    # Map evaluation service strings to emergency_units.unit_type enum values
    _SERVICE_TO_UNIT_TYPE: dict = {
        "fire":    "FIRE_ENGINE",
        "medical": "AMBULANCE",
        "police":  "PATROL_CAR",
        "rescue":  "RESCUE",
        "hazmat":  "HAZMAT",
    }

    # How many units to dispatch per service type, keyed by severity
    _UNITS_PER_TYPE: dict = {
        "CRITICAL": 3,
        "HIGH":     2,
        "MEDIUM":   1,
        "LOW":      1,
        "INFO":     1,
    }

    # Map severity to dispatch priority_level
    _PRIORITY_MAP: dict = {
        "CRITICAL": "CRITICAL",
        "HIGH":     "HIGH",
        "MEDIUM":   "STANDARD",
        "LOW":      "STANDARD",
        "INFO":     "STANDARD",
    }

    def __init__(self, db) -> None:
        """
        Args:
            db: AsyncSession shared with the evaluation service request.
        """
        self._db = db

    # -------------------------------------------------------------------------
    # trigger_deploy
    # -------------------------------------------------------------------------

    async def trigger_deploy(
        self,
        disaster_id: str,
        services: List[str],
        severity: str,
    ) -> None:
        """
        Dispatch the nearest available emergency units for each recommended service.

        Steps:
          1. For each service in recommended_services:
               a. Map service name to unit_type
               b. list_available_units(disaster_id, unit_type) sorted nearest first
               c. Take top N based on severity
          2. Collect all selected unit IDs
          3. dispatch_units(disaster_id, unit_ids, priority_level)
          4. Publish disaster.dispatched via RabbitMQ
        """
        try:
            from app.services.emergency_unit_service import EmergencyUnitService
            from app.services.deployment_service import DeploymentService
            from app.services.rabbitmq_service import get_rabbitmq_service

            unit_service       = EmergencyUnitService(self._db)
            deployment_service = DeploymentService(self._db)

            severity_upper = (severity or "MEDIUM").upper()
            units_per_type = self._UNITS_PER_TYPE.get(severity_upper, 1)
            priority_level = self._PRIORITY_MAP.get(severity_upper, "STANDARD")

            selected_unit_ids: List[str] = []

            for service_name in (services or []):
                unit_type = self._SERVICE_TO_UNIT_TYPE.get(service_name.lower())
                if not unit_type:
                    logger.warning(
                        "DirectCoordinationClient.trigger_deploy: "
                        "unknown service '%s' — skipping",
                        service_name,
                    )
                    continue

                available = await unit_service.list_available_units(
                    disaster_id=disaster_id,
                    unit_type=unit_type,
                )

                # Take top N nearest units (PostGIS already sorted by distance)
                chosen = [u["id"] for u in available[:units_per_type]]
                if not chosen:
                    logger.warning(
                        "DirectCoordinationClient.trigger_deploy: "
                        "no available %s units for disaster=%s",
                        unit_type, disaster_id,
                    )
                    continue

                selected_unit_ids.extend(chosen)
                logger.info(
                    "DirectCoordinationClient.trigger_deploy: "
                    "selected %d %s unit(s) for disaster=%s",
                    len(chosen), unit_type, disaster_id,
                )

            if not selected_unit_ids:
                logger.warning(
                    "DirectCoordinationClient.trigger_deploy: "
                    "no units available for disaster=%s services=%s — skipping dispatch",
                    disaster_id, services,
                )
                return

            result = await deployment_service.dispatch_units(
                disaster_id=disaster_id,
                unit_ids=selected_unit_ids,
                priority_level=priority_level,
                special_instructions="Auto-dispatched by evaluation service",
            )

            # ── CRITICAL: commit before publishing ───────────────────────────
            # dispatch_units only calls flush() — the unit-status UPDATEs and
            # deployment INSERTs are in an uncommitted transaction on self._db.
            # The Celery task's `async with async_session_factory() as db:`
            # calls session.close() on exit which ROLLS BACK any uncommitted
            # transaction. Without this commit, all deployment records and unit
            # status changes are silently discarded even though RabbitMQ already
            # fired "units dispatched" — leaving the frontend showing 0 units.
            await self._db.commit()

            # Publish disaster.dispatched RabbitMQ event — only after commit so
            # the deployment records are durably stored before notifying clients.
            pending_event = result.pop("_pending_event", None)
            if pending_event:
                try:
                    svc = get_rabbitmq_service()
                    svc.publish(pending_event["topic"], pending_event["payload"])
                except Exception as pub_exc:
                    logger.warning(
                        "DirectCoordinationClient: RabbitMQ publish failed "
                        "for disaster=%s — %s",
                        disaster_id, pub_exc,
                    )

            logger.info(
                "DirectCoordinationClient.trigger_deploy: "
                "dispatched %d unit(s) to disaster=%s priority=%s",
                len(selected_unit_ids), disaster_id, priority_level,
            )

        except Exception:
            logger.exception(
                "DirectCoordinationClient.trigger_deploy failed for disaster=%s",
                disaster_id,
            )
            # Never raise — downstream failures must not block evaluation response

    # -------------------------------------------------------------------------
    # trigger_evacuation
    # -------------------------------------------------------------------------

    async def trigger_evacuation(
        self,
        disaster_id: str,
        estimated_population: int,
        impact_radius_km: float,
    ) -> None:
        """
        Plan and immediately activate an evacuation for the disaster zone.

        Steps:
          1. plan_evacuation(disaster_id, auto_approve=True)
             auto_approve skips the manual approval step — plan comes back APPROVED
          2. activate_evacuation(plan_id)
             sends evacuation alerts to all users in affected zones and
             triggers evacuation.triggered notification via RabbitMQ
        """
        try:
            from app.providers.integration_service import get_integration_service
            from app.repositories.evacuation_repository import EvacuationRepository
            from app.services.evacuation_service import EvacuationService
            from app.services.instant_map_updates import MappingService
            from app.socket.manager import sio
            from app.workers.reroute_publisher import get_publisher

            # get_publisher is sync (returns module-level singleton)
            publisher       = get_publisher()
            evacuation_repo = EvacuationRepository(self._db)
            integration     = get_integration_service()
            mapping         = MappingService(sio=sio)

            evacuation_service = EvacuationService(
                db=evacuation_repo,
                external=integration,
                mapping=mapping,
                publisher=publisher,
            )

            # Phase 1 — plan with auto_approve=True (skips manual approval)
            plan_result = await evacuation_service.plan_evacuation(
                disaster_id=disaster_id,
                auto_approve=True,
            )
            plan_id = plan_result["plan_id"]

            logger.info(
                "DirectCoordinationClient.trigger_evacuation: "
                "plan %s created and auto-approved for disaster=%s "
                "population=%d radius=%.1fkm",
                plan_id, disaster_id, estimated_population, impact_radius_km,
            )

            # Phase 2 — commit the plan before activating so activate_evacuation
            # can read the plan record in the same session or a fresh one.
            await self._db.commit()

            # Phase 3 — activate (sends alerts, pushes notifications)
            await evacuation_service.activate_evacuation(plan_id=plan_id)

            # Commit activation changes (status update, notification records, etc.)
            await self._db.commit()

            logger.info(
                "DirectCoordinationClient.trigger_evacuation: "
                "evacuation activated for disaster=%s plan=%s",
                disaster_id, plan_id,
            )

        except Exception:
            logger.exception(
                "DirectCoordinationClient.trigger_evacuation failed for disaster=%s",
                disaster_id,
            )
            # Never raise — downstream failures must not block evaluation response


# ─────────────────────────────────────────────────────────────────────────────
# DirectRerouteClient
# ─────────────────────────────────────────────────────────────────────────────

class DirectRerouteClient(BaseRerouteClient):
    """
    Direct service-layer implementation — no HTTP round-trip.

    Calls RerouteService.trigger_reroute_traffic() directly using the
    same DB session as the evaluation pipeline. This avoids the
    HttpRerouteClient's localhost:8000 issue in Kubernetes where the
    Celery worker pod cannot reach the FastAPI pod via localhost.
    """

    def __init__(self, db) -> None:
        self._db = db

    async def trigger_reroute(
        self,
        disaster_id: str,
        affected_roads: list,
        lat: float = 0.0,
        lon: float = 0.0,
    ) -> None:
        try:
            from app.repositories.reroute_repository import RerouteRepository
            from app.services.reroute_service import RerouteService
            from app.services.instant_map_updates import MappingService
            from app.socket.manager import sio
            from app.workers.reroute_publisher import get_publisher
            from app.providers.integration_service import get_integration_service

            publisher   = get_publisher()
            integration = get_integration_service()
            mapping     = MappingService(sio=sio)

            reroute_service = RerouteService(
                db=RerouteRepository(self._db),
                external=integration,
                mapping=mapping,
                publisher=publisher,
            )

            # Convert affected_roads list to the format expected by the service.
            #
            # IMPORTANT: identify_affected_roads_async() in the enrichment pipeline
            # returns dicts with keys {road_name, start_lat, start_lng, end_lat,
            # end_lng} — NO segment_id.  upsert_road_segments() does
            # seg["segment_id"] unconditionally → KeyError without this guard.
            roads = []
            for i, road in enumerate(affected_roads or []):
                if isinstance(road, dict):
                    if "segment_id" not in road:
                        # Synthesise a stable ID from disaster + position index
                        road = {
                            **road,
                            "segment_id": f"eval-seg-{disaster_id[:8]}-{i}",
                            "status":     road.get("status", "closed"),
                            "reason":     road.get("reason", "disaster"),
                            "capacity":   road.get("capacity", 300),
                        }
                    roads.append(road)
                elif isinstance(road, str):
                    roads.append({
                        "segment_id": f"eval-seg-{disaster_id[:8]}-{i}",
                        "road_name": road,
                        "start_lat": lat,
                        "start_lng": lon,
                        "end_lat":   lat,
                        "end_lng":   lon,
                        "status": "closed",
                        "reason": "disaster",
                        "capacity": 300,
                    })

            result = await reroute_service.trigger_reroute_traffic(
                disaster_id=disaster_id,
                affected_roads=roads if roads else None,
            )
            # Commit reroute changes (road_segments status update, reroute_plan insert)
            await self._db.commit()

            logger.info(
                "DirectRerouteClient: reroute triggered for disaster=%s result=%s",
                disaster_id, result,
            )
        except Exception:
            logger.exception(
                "DirectRerouteClient.trigger_reroute failed for disaster=%s",
                disaster_id,
            )
            # Never raise — downstream failures must not block evaluation