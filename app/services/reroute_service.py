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
import math
from typing import Dict, Any, List, Optional

from app.providers.integration_service import IntegrationService
from app.repositories.reroute_repository import RerouteRepository
from app.repositories.disaster_repository import DisasterRepository
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
        affected_roads=None,
    ):
        """
        fetch the disaster record to get:
            lat, lon         → disaster centre point
            impact_radius_km → from disaster_metadata["evaluation"]["impact_radius_km"]
                            (stored by _persist_result in evaluation service)
    
        Fallback radius: 3.0 km if metadata is missing (e.g. scenario_engine disasters
        which don't go through evaluation).
        """
        from app.repositories.disaster_repository import DisasterRepository
    
        # Read disaster coordinates and impact radius from DB
        disaster_repo = DisasterRepository(self.db.db)   # self.db is RerouteRepository
        disaster = await disaster_repo.get_disaster_by_id(disaster_id)
    
        if not disaster:
            return {"status": "error", "detail": f"Disaster {disaster_id} not found"}
    
        lat = disaster["location"]["lat"]
        lon = disaster["location"]["lon"]
        radius_km = (
            disaster.get("disaster_metadata", {})
            .get("evaluation", {})
            .get("impact_radius_km", 3.0)
        )
    
        # Step 2 — blocked roads
        if affected_roads:
            blocked_roads = await self.enrich_segments_with_geometry(affected_roads)
            await self.db.upsert_road_segments(blocked_roads, disaster_id)
        else:
            blocked_roads = await self.get_blocked_roads(disaster_id)
            # Fallback: roads were identified by evaluation but not yet written
            # to road_segments (happens when trigger_reroute=false was used).
            # Read them from disaster_metadata and upsert now.
        if not blocked_roads and disaster:
            meta_roads = (
                disaster.get("disaster_metadata", {})
                .get("evaluation", {})
                .get("affected_roads", [])
            )
            if meta_roads:
                # Convert string road names to segment dicts if needed
                blocked_roads = []
                for i, road in enumerate(meta_roads):
                    if isinstance(road, dict):
                        blocked_roads.append({
                            "segment_id": road.get(
                                "segment_id",
                                f"eval-seg-{disaster_id[:8]}-{i}"
                            ),
                            "road_name":  road.get("road_name", f"Road {i}"),
                            "start_lat":  road.get("start_lat", lat),
                            "start_lng":  road.get("start_lng", lon),
                            "end_lat":    road.get("end_lat", lat),
                            "end_lng":    road.get("end_lng", lon),
                            "status":     road.get("status", "closed"),
                            "reason":     road.get("reason", "disaster"),
                            "capacity":   road.get("capacity", 300),
                        })
                    else:
                        # String fallback — use disaster lat/lon as coords
                        blocked_roads.append({
                            "segment_id": f"eval-seg-{disaster_id[:8]}-{i}",
                            "road_name": road,
                            "start_lat": lat,
                            "start_lng": lon,
                            "end_lat": lat,
                            "end_lng": lon,
                            "status": "closed",
                            "reason": "disaster",
                            "capacity": 300,
                        })
                if blocked_roads:
                    blocked_roads = await self.enrich_segments_with_geometry(blocked_roads)
                    await self.db.upsert_road_segments(blocked_roads, disaster_id)

    
        if not blocked_roads:
            return {"status": "no_blocked_roads", "disaster_id": disaster_id}
    
        # Step 3 — traffic (uses lat/lon/radius)
        traffic_data = await self.external.fetch_traffic_data(lat, lon, radius_km)
        traffic_segments = traffic_data.get("segments", [])
    
        # Step 4 — vehicles (uses lat/lon/radius)
        vehicles = await self.db.get_users_in_affected_area(lat, lon, radius_km)
 

        if not vehicles:
            logger.info(f"No impacted vehicles for disaster {disaster_id}")
            await self.db.log_event(
                disaster_id=disaster_id,
                event_type="traffic_rerouted",
                data={"status": "no_vehicles_affected"},
            )
            return {"status": "no_vehicles_affected", "disaster_id": disaster_id}

        # Step 5 — alternative routes
        # Deduplicate destinations — simulator uses 3 fixed destinations so this
        # gives exactly 3 unique clusters = 3 TomTom calls, no 429 risk.
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles
            if v.get("destination")
        })
        destination_dicts = [{"lat": lat, "lng": lng} for lat, lng in destinations]
        logger.info(f"Routing to {len(destination_dicts)} destination clusters: {destination_dicts}")

        alternative_routes = await self.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
            vehicles=vehicles,
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
        await self.mapping.highlight_alternative_routes(alternative_routes, disaster_id=disaster_id)

        # Step 11 — publish to RabbitMQ (fire-and-forget, non-blocking)
        await self.publisher.publish_reroute_triggered(
            disaster_id=disaster_id,
            plan_id=saved_plan.get("id", ""),
            vehicles=vehicles,
            route_assignments=plan.route_assignments,
            routes=alternative_routes,
            overflow_count=plan.overflow_count,
            location={"lat": lat, "lon": lon},
            tracking_id=disaster.get("tracking_id", ""),
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

        # Register region for traffic monitoring loop
        await register_active_region(
            disaster_id=disaster_id,
            lat=lat, lon=lon, radius_km=radius_km,
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


    async def enrich_segments_with_geometry(
        self,
        segments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Fetch road-following geometry from TomTom for each blocked segment.
        Runs in parallel. Results stored in DB so subsequent reads are free.

        Zero-length segments (start == end, from evaluation string fallback)
        get a small circle drawn around the disaster point instead of a
        TomTom call — gives the frontend something meaningful to render.
        """
        def _is_mock_points(points: list) -> bool:
            """Detect the 5-point diagonal fallback generated by MOCK_ROUTING_RESPONSE."""
            if not points or len(points) != 5:
                return False
            return points[0] == [53.302, -6.361] and points[-1] == [53.342, -6.341]

        async def _enrich_one(seg: Dict[str, Any]) -> Dict[str, Any]:
            # Skip if already enriched (re-trigger scenario),
            # but re-fetch if the stored points are mock/fallback data.
            if seg.get("points") and not _is_mock_points(seg["points"]):
                return seg

            start_lat = seg["start_lat"]
            start_lng = seg["start_lng"]
            end_lat   = seg["end_lat"]
            end_lng   = seg["end_lng"]

            # Zero-length segment — evaluation gave us only a centre point
            # Draw a small circle so the frontend shows a blocked zone
            if start_lat == end_lat and start_lng == end_lng:
                radius_deg = 0.005  # ~500m radius
                points = [
                    [
                        start_lat + radius_deg * math.cos(math.radians(a)),
                        start_lng + radius_deg * math.sin(math.radians(a)),
                    ]
                    for a in range(0, 360, 45)
                ]
                points.append(points[0])  # close the ring
                return {
                    **seg,
                    "points": points,
                    "geojson": {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[p[1], p[0]] for p in points],
                        },
                        "properties": {},
                    },
                }

            # Real segment — ask TomTom for road-following geometry.
            # Per-segment 12s timeout so a single hanging TomTom call never
            # blocks the entire gather — the circle fallback fires instead.
            try:
                geometry = await asyncio.wait_for(
                    self.external.fetch_segment_geometry(
                        start_lat=start_lat,
                        start_lng=start_lng,
                        end_lat=end_lat,
                        end_lng=end_lng,
                    ),
                    timeout=12.0,
                )
                if geometry.get("points"):
                    return {**seg, **geometry}
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning(
                    f"fetch_segment_geometry failed for {seg.get('segment_id')}: {exc} "
                    "— falling back to midpoint circle"
                )

            # Fallback: draw a small circle centred at the segment midpoint
            mid_lat = (start_lat + end_lat) / 2
            mid_lng = (start_lng + end_lng) / 2
            radius_deg = 0.003  # ~300 m
            fallback_points = [
                [
                    mid_lat + radius_deg * math.cos(math.radians(a)),
                    mid_lng + radius_deg * math.sin(math.radians(a)),
                ]
                for a in range(0, 360, 45)
            ]
            fallback_points.append(fallback_points[0])
            return {
                **seg,
                "points": fallback_points,
                "geojson": {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[p[1], p[0]] for p in fallback_points],
                    },
                    "properties": {},
                },
            }

        # return_exceptions=True so one TomTom failure doesn't kill all segments.
        # Any Exception result is replaced with the original unenriched segment
        # (the per-segment 12s timeout in _enrich_one ensures each call falls
        # back to circle geometry independently rather than blocking the batch).
        raw_results = await asyncio.gather(
            *[_enrich_one(s) for s in segments],
            return_exceptions=True,
        )
        enriched = []
        for i, res in enumerate(raw_results):
            if isinstance(res, Exception):
                logger.error(
                    f"_enrich_one raised for segment {segments[i].get('segment_id')}: {res}"
                )
                enriched.append(segments[i])   # keep unenriched rather than drop
            else:
                enriched.append(res)
        return enriched

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
        vehicles: List[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:

        if vehicles:
            # Pair each unique origin+destination combination
            # so each vehicle only gets routes relevant to their journey.
            seen_pairs: set = set()
            tasks = []
            for v in vehicles:
                loc = v.get("current_location", {})
                dest = v.get("destination", {})
                if not (loc.get("lat") and loc.get("lng") and dest.get("lat") and dest.get("lng")):
                    continue
                pair = (
                    round(loc["lat"], 3), round(loc["lng"], 3),
                    round(dest["lat"], 3), round(dest["lng"], 3),
                )
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                tasks.append(
                    self.external.get_directions(
                        origin={"lat": loc["lat"], "lng": loc["lng"]},
                        destination={"lat": dest["lat"], "lng": dest["lng"]},
                        avoid=blocked_roads,
                        alternatives=True,
                    )
                )
        else:
            # Fallback: fixed origin to all destinations
            tasks = [
                self.external.get_directions(
                    origin={"lat": 53.2900, "lng": -6.3800},
                    destination=dest,
                    avoid=blocked_roads,
                    alternatives=True,
                )
                for dest in destinations
            ]

        # Launch all routing calls concurrently — rate limiting is enforced
        # inside IntegrationService via _tomtom_rate_limiter (token bucket,
        # 2.5 req/s / burst=2) so these gather normally without 429s.
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_routes = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Route calculation failed: {result}")
                continue
            # Skip degraded results — they carry no real geometry and would
            # pollute the map with mock coordinates if saved to the plan.
            if result.get("mode") == "degraded":
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
          2. Fetch disaster lat/lon/radius from DB
          3. Recompute routes with override constraints (TomTom)
          4. Update map (synchronous)
          5. Publish route.updated event to RabbitMQ (async)
        """
        from app.repositories.disaster_repository import DisasterRepository
 
        disaster_id = override.get("disaster_id", "unknown")
 
        # Step 1 — persist
        await self.db.apply_override(override, disaster_id)
 
        # Step 2 — read disaster coordinates from DB
        disaster_repo = DisasterRepository(self.db.db)
        disaster = await disaster_repo.get_disaster_by_id(disaster_id)
 
        lat = disaster["location"]["lat"] if disaster else 53.30
        lon = disaster["location"]["lon"] if disaster else -6.36
        radius_km = (
            disaster.get("disaster_metadata", {})
            .get("evaluation", {})
            .get("impact_radius_km", 3.0)
        ) if disaster else 3.0
 
        # Step 3 — recompute using actual blocked roads and all destinations
        active_overrides = await self.db.get_active_overrides(disaster_id)
        blocked_roads = await self.db.get_blocked_roads(disaster_id)
        vehicles = await self.db.get_users_in_affected_area(lat, lon, radius_km)
 
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles if v.get("destination")
        })[:3]
 
        all_routes = []
        for dest in destinations:
            dest_dict = {"lat": dest[0], "lng": dest[1]}
            result = await self.external.recompute_with_overrides(
                origin={"lat": 53.2900, "lng": -6.3800},
                destination=dest_dict,
                blocked_roads=blocked_roads,
                active_overrides=active_overrides,
            )
            all_routes.extend(result.get("routes", []))
 
        # Deduplicate
        seen = set()
        updated_routes = []
        for r in all_routes:
            rid = r.get("route_id")
            if rid and rid not in seen:
                seen.add(rid)
                updated_routes.append(r)
 
        if not updated_routes:
            updated_routes = all_routes[:4]
 
        # Step 3b — persist updated plan
        if updated_routes and vehicles:
            plan: DistributionPlan = optimize_traffic_distribution(
                vehicles=vehicles,
                routes=updated_routes,
            )
            await self.db.save_reroute_plan(
                disaster_id=disaster_id,
                blocked_roads=blocked_roads,
                chosen_routes=updated_routes,
                route_assignments=plan.route_assignments,
                estimated_times=plan.estimated_times,
                capacity_usage=plan.capacity_usage,
                vehicles_affected=len(vehicles),
                trigger_source="operator_override",
            )
 
        # Step 4 — map
        await self.mapping.highlight_alternative_routes(
            updated_routes, disaster_id=disaster_id
        )
 
        # Step 5 — publish
        await self.publisher.publish_route_updated(
            disaster_id=disaster_id,
            reason="operator_override",
            vehicles=vehicles,
            route_assignments={},
            routes=updated_routes,
        )
 
        await self.db.log_event(
            disaster_id=disaster_id,
            event_type="operator_override",
            data={
                "override_type": override.get("type"),
                "operator_id": override.get("operator_id"),
                "routes_recomputed": len(updated_routes),
            },
        )
 
        return {"status": "override_applied", "routes_recomputed": len(updated_routes)}
    # -------------------------------------------------------------------------
    # Monitoring cycle (called by Celery task + tests)
    # -------------------------------------------------------------------------

    async def run_monitoring_cycle(self, disaster_id: str) -> dict:
        """
        Run one monitoring cycle for a disaster.
 
        Parameter changed from region_id: str  →  disaster_id: str
        lat/lon/radius_km are read from the active regions registry
        (stored there by register_active_region after trigger_reroute_traffic).
 
        Called by:
          - Celery monitor_traffic_conditions task (every 30s)
          - POST /scenarios/trigger-monitoring-cycle (manual, for testing)
        """
        from app.services.predictive_congestion import dual_congestion_check
        from app.workers.tasks import get_active_regions

        active = await get_active_regions()
        region_entry = active.get(disaster_id)
 
        if not region_entry:
            logger.debug(f"run_monitoring_cycle: no active region for {disaster_id}")
            return {"status": "no_active_region", "disaster_id": disaster_id}
 
        lat        = region_entry["lat"]
        lon        = region_entry["lon"]
        radius_km  = region_entry["radius_km"]
        route_plan = region_entry["route_plan"]
        segment_capacities = region_entry["segment_capacities"]
 
        try:
            traffic_data = await self.external.fetch_traffic_data(lat, lon, radius_km)
            live_segments = traffic_data.get("segments", [])
        except Exception as e:
            logger.warning(f"run_monitoring_cycle: traffic fetch failed — {e}")
            live_segments = []
 
        check = dual_congestion_check(
            live_traffic_data=live_segments,
            route_plan=route_plan,
            segment_capacities=segment_capacities,
        )
 
        if not check["should_recalculate"]:
            logger.debug(f"run_monitoring_cycle: no recalculation needed for {disaster_id}")
            return {"status": "ok", "should_recalculate": False, "disaster_id": disaster_id}
 
        logger.info(
            f"run_monitoring_cycle: recalculation triggered "
            f"disaster={disaster_id} reason={check['triggered_by']}"
        )
 
        blocked_roads = await self.db.get_blocked_roads(disaster_id)
        vehicles = await self.db.get_users_in_affected_area(lat, lon, radius_km)
 
        destinations = list({
            (v["destination"]["lat"], v["destination"]["lng"])
            for v in vehicles if v.get("destination")
        })
        destination_dicts = [{"lat": la, "lng": lo} for la, lo in destinations]
 
        new_routes = await self.calculate_alternative_routes(
            blocked_roads=blocked_roads,
            destinations=destination_dicts,
        )
 
        if new_routes:
            await self.mapping.highlight_alternative_routes(
                new_routes, disaster_id=disaster_id
            )
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
            "disaster_id": disaster_id,
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
        active = await get_active_regions()

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
        from app.repositories.disaster_repository import DisasterRepository
        disaster_repo = DisasterRepository(self.db.db)
        disaster = await disaster_repo.get_disaster_by_id(disaster_id)

        users = []
        if disaster:
            lat = disaster["location"]["lat"]
            lon = disaster["location"]["lon"]
            radius_km = (
                disaster.get("disaster_metadata", {})
                .get("evaluation", {})
                .get("impact_radius_km", 3.0)
            )
            users = await self.db.get_users_in_affected_area(lat, lon, radius_km)


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

        # Deregister from traffic monitoring loop
        await deregister_active_region(disaster_id)

        logger.info(
            f"restore_normal_flow: disaster={disaster_id}"
            f"segments={len(cleared_segments)} users_notified={len(users)}"
        )

        return {
            "status": "restored",
            "disaster_id": disaster_id,
            "cleared_segments": len(cleared_segments),
            "users_notified": len(users),
        }