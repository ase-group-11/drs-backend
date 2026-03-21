"""
app/services/reroute_service.py

ReRoute Service — orchestrates the full reroute traffic pipeline.

Sequence diagram steps implemented here:
  Step 1  — triggerRerouteTraffic (entry point)
  Step 2  — getBlockedRoads (DB)
  Step 3  — fetchTrafficData (ExternalIntegration) + degraded mode
  Step 4  — findImpactedVehicles
  Step 5  — getDirections (ExternalIntegration, parallel per destination)
  Step 6  — Innovation 1: optimize_traffic_distribution
  Step 7  — evaluateFeasibility + temporaryControls if no feasible routes
  Step 8  — persistReroutePlan (DB)
  Step 9  — updateRoadStatus (DB)
  Step 10 — highlightAlternativeRoutes (MappingService)
  Step 11 — publish reroute.triggered event (RabbitMQ → NotificationService)
  Step 12 — logEvent (DB audit log)
  Step 13b — reprioritizeFlows (priority routing override)
  Step 13c — receiveOverride (operator override)
  Step 14 — restoreNormalFlow (clearance)

Notification delivery is fully async via RabbitMQ — RerouteService
publishes events and returns immediately. NotificationService consumes
and delivers SMS/push/Socket.IO independently.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from app.providers.integration_service import IntegrationService
from app.repositories.reroute_repository import RerouteRepository
from app.services.traffic_distribution import optimize_traffic_distribution, DistributionPlan
from app.services.instant_map_updates import MappingService
from app.workers.reroute_publisher import ReroutePublisher
from app.workers.tasks import register_active_region, deregister_active_region

logger = logging.getLogger(__name__)


class RerouteService:
    """
    Orchestrator for the full reroute traffic pipeline.

    All dependencies are injected via constructor — fully testable with mocks.
    """

    def __init__(
        self,
        db: RerouteRepository,
        external: IntegrationService,
        mapping: MappingService,
        publisher: ReroutePublisher,
    ):
        self.db = db
        self.external = external
        self.mapping = mapping
        self.publisher = publisher

    # -------------------------------------------------------------------------
    # Step 1 — Entry point
    # -------------------------------------------------------------------------

    async def trigger_reroute_traffic(
        self,
        disaster_id: str,
        region_id: str,
        affected_roads: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point — called by POST /reroute/trigger.

        Orchestrates the full pipeline from Steps 2–12.
        """
        logger.info(f"triggerRerouteTraffic: disaster={disaster_id} region={region_id}")

        # Step 2 — blocked roads
        if affected_roads:
            blocked_roads = affected_roads
            await self.db.upsert_road_segments(blocked_roads, disaster_id)
        else:
            blocked_roads = await self.get_blocked_roads(disaster_id)

        if not blocked_roads:
            logger.warning(f"No blocked roads found for disaster {disaster_id}")
            return {"status": "no_blocked_roads", "disaster_id": disaster_id}

        # Step 3 — traffic data
        traffic_data = await self.fetch_traffic_data(region_id)
        traffic_segments = traffic_data.get("segments", [])

        # Step 4 — impacted vehicles
        vehicles = await self.find_impacted_vehicles(region_id, blocked_roads)

        if not vehicles:
            logger.info(f"No impacted vehicles for disaster {disaster_id}")
            await self.db.log_event(
                disaster_id=disaster_id,
                event_type="traffic_rerouted",
                data={"status": "no_vehicles_affected"},
            )
            return {"status": "no_vehicles_affected", "disaster_id": disaster_id}

        # Step 5 — alternative routes
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles
            if v.get("destination")
        })
        destination_dicts = [{"lat": lat, "lng": lng} for lat, lng in destinations]

        alternative_routes = await self.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
        )

        # Step 7 — feasibility check
        feasibility = await self.evaluate_feasibility(alternative_routes)
        if not feasibility["feasible"]:
            logger.warning("No feasible routes — activating temporary controls")
            controls_result = await self.handle_no_feasible_routes(
                blocked_roads=blocked_roads,
                destinations=destination_dicts,
            )
            alternative_routes = controls_result.get("routes", alternative_routes)

        # Step 6 — Innovation 1: distribute vehicles across routes
        plan: DistributionPlan = optimize_traffic_distribution(
            vehicles=vehicles,
            routes=alternative_routes,
            traffic_segments=traffic_segments,
        )

        # Step 8 — persist plan
        saved_plan = await self.db.save_reroute_plan(
            disaster_id=disaster_id,
            blocked_roads=blocked_roads,
            chosen_routes=alternative_routes,
            route_assignments=plan.route_assignments,
            estimated_times=plan.estimated_times if hasattr(plan, "estimated_times") else {},
            capacity_usage=plan.capacity_usage if hasattr(plan, "capacity_usage") else {},
            vehicles_affected=len(vehicles),
            trigger_source="disaster_trigger",
        )

        # Step 9 — update road status
        await self.update_road_status(blocked_roads, "closed")

        # Step 10 — map update (synchronous — must complete before response)
        await self.mapping.highlight_alternative_routes(alternative_routes, region_id=region_id)

        # Step 11 — publish to RabbitMQ (fire-and-forget, non-blocking)
        await self.publisher.publish_reroute_triggered(
            disaster_id=disaster_id,
            plan_id=saved_plan.get("id", ""),
            vehicles=vehicles,
            route_assignments=plan.route_assignments,
            routes=alternative_routes,
            overflow_count=plan.overflow_count,
        )

        # Step 12 — audit log
        await self.db.log_event(
            disaster_id=disaster_id,
            event_type="traffic_rerouted",
            reroute_plan_id=saved_plan.get("id"),
            data={
                "vehicles_affected": len(vehicles),
                "routes_count": len(alternative_routes),
                "overflow_count": plan.overflow_count,
                "traffic_mode": traffic_data.get("mode", "live"),
            },
        )

        # Register region for Celery monitoring loop (Phase 4a)
        register_active_region(
            disaster_id=disaster_id,
            region_id=region_id,
            route_plan={
                r["route_id"]: {
                    "vehicles_assigned": plan.route_stats.get(r["route_id"], {}).get("assigned", 0),
                    "segments": list(r.get("segment_capacities", {}).keys()),
                    "average_speed_kmh": 60,
                    "segment_length_km": {s: 1.0 for s in r.get("segment_capacities", {})},
                }
                for r in alternative_routes
            },
            segment_capacities={
                seg: cap
                for r in alternative_routes
                for seg, cap in r.get("segment_capacities", {}).items()
            },
        )

        logger.info(
            f"triggerRerouteTraffic complete: disaster={disaster_id} "
            f"vehicles={len(vehicles)} routes={len(alternative_routes)}"
        )

        return {
            "status": "rerouted",
            "disaster_id": disaster_id,
            "plan_id": saved_plan.get("id"),
            "vehicles_affected": len(vehicles),
            "routes_count": len(alternative_routes),
            "overflow_count": plan.overflow_count,
        }

    # -------------------------------------------------------------------------
    # Step 2 — Blocked roads
    # -------------------------------------------------------------------------

    async def get_blocked_roads(self, disaster_id: str) -> List[Dict[str, Any]]:
        return await self.db.get_blocked_roads(disaster_id)

    # -------------------------------------------------------------------------
    # Step 3 — Traffic data
    # -------------------------------------------------------------------------

    async def fetch_traffic_data(self, region_id: str) -> Dict[str, Any]:
        try:
            return await self.external.fetch_traffic_data(region_id)
        except Exception as e:
            logger.error(f"fetch_traffic_data failed: {e} — switching to degraded mode")
            return {"segments": [], "mode": "degraded", "cached_graph": True}

    # -------------------------------------------------------------------------
    # Step 4 — Impacted vehicles
    # -------------------------------------------------------------------------

    async def find_impacted_vehicles(
        self, region_id: str, blocked_roads: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        return await self._query_vehicles_on_segments(region_id, blocked_roads)

    async def _query_vehicles_on_segments(
        self, region_id: str, blocked_roads: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        return await self.db.get_users_in_affected_area(region_id)

    # -------------------------------------------------------------------------
    # Step 5 — Alternative routes
    # -------------------------------------------------------------------------

    async def calculate_alternative_routes(
        self,
        blocked_roads: List[Dict[str, Any]],
        destinations: List[Dict[str, float]],
    ) -> List[Dict[str, Any]]:
        # Origin: south of M50 J6 — vehicles coming from southwest Dublin
        # heading north through the M50. When flooded, routes detour around it.
        REROUTE_ORIGIN = {"lat": 53.2900, "lng": -6.3800}

        tasks = [
            self.external.get_directions(
                origin=REROUTE_ORIGIN,
                destination=dest,
                avoid=blocked_roads,
                alternatives=True,
            )
            for dest in destinations
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_routes = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Route calculation failed: {result}")
                continue
            all_routes.extend(result.get("routes", []))

        seen = set()
        unique_routes = []
        for route in all_routes:
            rid = route.get("route_id")
            if rid and rid not in seen:
                seen.add(rid)
                unique_routes.append(route)

        return unique_routes

    # -------------------------------------------------------------------------
    # Step 7 — Feasibility + temporary controls
    # -------------------------------------------------------------------------

    async def evaluate_feasibility(
        self, routes: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        feasible = any(r.get("travel_time_seconds", 0) > 0 for r in routes)
        return {"feasible": feasible, "route_count": len(routes)}

    async def handle_no_feasible_routes(
        self,
        blocked_roads: List[Dict[str, Any]],
        destinations: List[Dict[str, float]],
    ) -> Dict[str, Any]:
        logger.info("Activating temporary traffic controls: contraflow + signal_priority")
        routes = await self.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destinations,
        )
        return {
            "controls_activated": True,
            "activated_controls": ["contraflow", "signal_priority", "variable_message_signs"],
            "routes": routes,
        }

    # -------------------------------------------------------------------------
    # Steps 8–9 — Persistence
    # -------------------------------------------------------------------------

    async def save_reroute_plan(
        self,
        disaster_id: str,
        blocked_roads: List[Dict[str, Any]],
        chosen_routes: List[Dict[str, Any]],
        **kwargs,
    ) -> Dict[str, Any]:
        return await self.db.save_reroute_plan(
            disaster_id=disaster_id,
            blocked_roads=blocked_roads,
            chosen_routes=chosen_routes,
            route_assignments=kwargs.get("route_assignments", {}),
            vehicles_affected=kwargs.get("vehicles_affected", 0),
        )

    async def update_road_status(
        self, segments: List[Dict[str, Any]], status: str
    ) -> bool:
        return await self.db.update_road_status(segments, status)

    async def log_event(
        self,
        disaster_id: str,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        return await self.db.log_event(
            disaster_id=disaster_id,
            event_type=event_type,
            data=data or {},
        )

    # -------------------------------------------------------------------------
    # Step 13b — Priority routing
    # -------------------------------------------------------------------------

    async def reprioritize_flows(
        self,
        vehicles: List[Dict[str, Any]],
        routes: List[Dict[str, Any]],
    ) -> DistributionPlan:
        return optimize_traffic_distribution(vehicles=vehicles, routes=routes)

    # -------------------------------------------------------------------------
    # Step 13c — Operator overrides
    # -------------------------------------------------------------------------

    async def receive_override(self, override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Apply an operator override and recompute the active reroute plan.

        Steps:
          1. Persist override to DB
          2. Recompute routes with override constraints (TomTom)
          3. Update map (synchronous)
          4. Publish route.updated event to RabbitMQ (async)
        """
        disaster_id = override.get("disaster_id", "unknown")

        # Step 1 — persist
        await self.db.apply_override(override, disaster_id)

        # Step 2 — recompute
        active_overrides = await self.db.get_active_overrides(disaster_id)
        result = await self.external.recompute_with_overrides(
            origin=override.get("origin", {"lat": 53.3498, "lng": -6.2603}),
            destination=override.get("destination", {"lat": 53.4000, "lng": -6.2000}),
            blocked_roads=override.get("blocked_roads", []),
            active_overrides=active_overrides,
        )
        updated_routes = result.get("routes", [])

        # Step 3 — map (synchronous)
        await self.mapping.highlight_alternative_routes(updated_routes)

        # Step 4 — publish to RabbitMQ (fire-and-forget)
        await self.publisher.publish_route_updated(
            disaster_id=disaster_id,
            reason="operator_override",
            vehicles=[],
            route_assignments={},
            routes=updated_routes,
        )

        await self.db.log_event(
            disaster_id=disaster_id,
            event_type="operator_override",
            data={
                "override_type": override.get("type"),
                "operator_id": override.get("operator_id"),
            },
        )

        return {"status": "override_applied", "routes_recomputed": len(updated_routes)}
    # -------------------------------------------------------------------------
    # Monitoring cycle (called by Celery task + tests)
    # -------------------------------------------------------------------------

    async def run_monitoring_cycle(self, region_id: str) -> dict:
        """
        Run one monitoring cycle for a region.

        Steps:
          1. Fetch live traffic from TomTom
          2. Run dual congestion check (reactive + predictive)
          3. If recalculation needed → recompute routes, update map, publish

        Called by Celery monitor_traffic_conditions task every 30s,
        and directly in tests.
        """
        from app.services.predictive_congestion import dual_congestion_check

        try:
            traffic_data = await self.external.fetch_traffic_data(region_id)
            live_segments = traffic_data.get("segments", [])
        except Exception as e:
            logger.warning(f"run_monitoring_cycle: traffic fetch failed — {e}")
            live_segments = []

        # Get current route plan for this region from registry
        from app.workers.tasks import get_active_regions
        active = get_active_regions()
        region_entry = next(
            (v for v in active.values() if v["region_id"] == region_id),
            None,
        )

        route_plan = region_entry["route_plan"] if region_entry else {}
        segment_capacities = region_entry["segment_capacities"] if region_entry else {}

        check = dual_congestion_check(
            live_traffic_data=live_segments,
            route_plan=route_plan,
            segment_capacities=segment_capacities,
        )

        if not check["should_recalculate"]:
            logger.debug(f"run_monitoring_cycle: no recalculation needed for {region_id}")
            return {"status": "ok", "should_recalculate": False, "region_id": region_id}

        logger.info(
            f"run_monitoring_cycle: recalculation triggered "
            f"region={region_id} reason={check['triggered_by']}"
        )

        # Fetch vehicles + blocked roads for this region
        disaster_id = region_entry.get("disaster_id", region_id) if region_entry else region_id
        blocked_roads = await self.db.get_blocked_roads(disaster_id)
        vehicles = await self.db.get_users_in_affected_area(region_id)

        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles
            if v.get("destination")
        })
        destination_dicts = [{"lat": lat, "lng": lng} for lat, lng in destinations]

        new_routes = await self.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
        )

        if new_routes:
            await self.mapping.highlight_alternative_routes(new_routes, region_id=region_id)
            await self.publisher.publish_route_updated(
                disaster_id=disaster_id,
                reason=check["triggered_by"],
                vehicles=vehicles,
                route_assignments={},
                routes=new_routes,
            )

        return {
            "status": "recalculated",
            "should_recalculate": True,
            "triggered_by": check["triggered_by"],
            "region_id": region_id,
            "new_routes_count": len(new_routes),
        }

    # -------------------------------------------------------------------------
    # Multi-incident handling (Phase 5)
    # -------------------------------------------------------------------------

    async def handle_concurrent_incident(
        self,
        incident: dict,
        existing_vehicles: Optional[List[Dict[str, Any]]] = None,
    ) -> dict:
        """
        Handle a second concurrent incident alongside an existing active reroute.

        Steps:
          1. Recompute detours considering all active incidents
          2. Reprioritize flows (emergency first)
          3. Update map
          4. Publish route.updated event

        Args:
            incident: New incident dict with disaster_id, blocked_roads, severity
            existing_vehicles: Vehicle pool to reprioritize (fetched from DB if None)

        Returns:
            Summary dict
        """
        disaster_id = incident.get("disaster_id", "unknown")
        blocked_roads = incident.get("blocked_roads", [])

        # Fetch existing active incidents
        from app.workers.tasks import get_active_regions
        active = get_active_regions()

        all_incidents = list(active.values()) + [{
            "region_id": incident.get("region_id", "region-dublin"),
            "blocked_roads": blocked_roads,
        }]

        # Recompute multi-incident detours
        vehicles = existing_vehicles or await self.db.get_users_in_affected_area(
            incident.get("region_id", disaster_id)
        )

        result = await self.external.recompute_multi_incident_detours(
            incidents=all_incidents,
            vehicles=vehicles,
        )
        new_routes = result.get("routes", [])

        # Reprioritize — emergency vehicles get best routes first
        if new_routes and vehicles:
            plan = self.reprioritize_flows.__func__(self, vehicles, new_routes)
            if hasattr(plan, "route_assignments"):
                route_assignments = plan.route_assignments
            else:
                route_assignments = {}
        else:
            route_assignments = {}

        # Update map
        if new_routes:
            await self.mapping.highlight_alternative_routes(new_routes)

        # Publish
        await self.publisher.publish_route_updated(
            disaster_id=disaster_id,
            reason="multi_incident",
            vehicles=vehicles,
            route_assignments=route_assignments,
            routes=new_routes,
        )

        await self.db.log_event(
            disaster_id=disaster_id,
            event_type="multi_incident",
            data={
                "concurrent_incidents": len(active),
                "new_routes": len(new_routes),
                "severity": incident.get("severity"),
            },
        )

        return {
            "status": "reprioritized",
            "disaster_id": disaster_id,
            "concurrent_incidents": len(active) + 1,
            "new_routes_count": len(new_routes),
            "vehicles_reprioritized": len(vehicles),
        }


    # -------------------------------------------------------------------------
    # Step 14 — Clearance / restoration
    # -------------------------------------------------------------------------

    async def restore_normal_flow(
        self,
        disaster_id: str,
        cleared_segments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Restore normal traffic flow after a disaster is cleared.

        Steps:
          1. Update road status → open
          2. Clear active detours on map (synchronous)
          3. Fetch users in affected area
          4. Publish disaster.cleared event to RabbitMQ (async)
          5. Log restored event
        """
        # Step 1
        await self.db.update_road_status(cleared_segments, "open")

        # Step 1b — update disaster status to RESOLVED in disasters table
        await self.db.resolve_disaster(disaster_id)

        # Step 2 — map (synchronous)
        await self.mapping.clear_detours()

        # Step 3
        users = await self.db.get_users_in_affected_area(disaster_id)

        # Step 4 — publish to RabbitMQ (fire-and-forget)
        await self.publisher.publish_all_clear(
            disaster_id=disaster_id,
            users=users,
            cleared_segments=len(cleared_segments),
        )

        # Step 5
        await self.db.log_event(
            disaster_id=disaster_id,
            event_type="restored",
            data={"cleared_segments": len(cleared_segments)},
        )

        await self.db.clear_reroute_plans(disaster_id)

        # Deregister from Celery monitoring loop
        deregister_active_region(disaster_id)

        logger.info(
            f"restore_normal_flow: disaster={disaster_id} "
            f"segments={len(cleared_segments)} users_notified={len(users)}"
        )

        return {
            "status": "restored",
            "disaster_id": disaster_id,
            "cleared_segments": len(cleared_segments),
            "users_notified": len(users),
        }