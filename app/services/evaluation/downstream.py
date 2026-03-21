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

    async def trigger_reroute(self, disaster_id, affected_roads) -> None:
        logger.debug(
            "NoopRerouteClient.trigger_reroute: disaster=%s roads=%s",
            disaster_id, affected_roads,
        )
