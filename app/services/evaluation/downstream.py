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