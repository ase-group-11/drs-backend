"""
app/services/mapping_service.py

Mapping Service — pushes route overlay data to the frontend via Socket.IO.

Responsibilities:
  - Convert parsed routes to GeoJSON FeatureCollection
  - Emit to the correct Socket.IO room (region-based)
  - highlight_alternative_routes: show detour overlays on the map
  - clear_detours:                remove all active overlays when disaster cleared
  - send_updated_routes:          push recalculated routes after congestion / override

Room convention:
  reroute:{region_id}   — all users subscribed to a geographic region
  Fallback: reroute:global when region_id is not provided

Events emitted:
  reroute_alert           — initial detour overlays
  updated_recommendation  — recalculated routes
  all_clear               — disaster resolved, overlays removed
"""

import logging
from typing import Optional

from app.providers.tomtom_parser import extract_geojson

logger = logging.getLogger(__name__)


class MappingService:
    """
    Sends reroute map data to connected frontend clients via Socket.IO.

    Injected into RerouteService via constructor.
    In Phase 3+, sio is the shared socketio.AsyncServer from app.socket.manager.
    """

    def __init__(self, sio=None):
        """
        Args:
            sio: socketio.AsyncServer instance (injected from app.socket.manager)
        """
        self.sio = sio

    async def highlight_alternative_routes(
        self,
        routes: list,
        region_id: Optional[str] = None,
    ) -> dict:
        """
        Send alternative route overlays to the frontend map.

        Converts routes to a GeoJSON FeatureCollection and emits
        a 'reroute_alert' event to the region Socket.IO room.

        Args:
            routes:    List of parsed route dicts
            region_id: Region identifier — used to target the correct room

        Returns:
            Status dict
        """
        geojson = extract_geojson(routes)
        room = f"reroute:{region_id}" if region_id else "reroute:global"

        if self.sio:
            await self.sio.emit(
                "reroute_alert",
                {
                    "geojson": geojson,
                    "routes_count": len(routes),
                    "region_id": region_id,
                },
                room=room,
            )
            logger.info(
                f"MappingService: emitted reroute_alert "
                f"routes={len(routes)} room={room}"
            )
        else:
            logger.info(
                f"MappingService (no sio): highlight_alternative_routes "
                f"routes={len(routes)} room={room}"
            )

        return {"status": "displayed", "routes_count": len(routes), "room": room}

    async def clear_detours(
        self,
        region_id: Optional[str] = None,
    ) -> dict:
        """
        Remove all active route overlays from the map.

        Emits an 'all_clear' event to the region Socket.IO room.

        Args:
            region_id: Region identifier

        Returns:
            Status dict
        """
        room = f"reroute:{region_id}" if region_id else "reroute:global"

        if self.sio:
            await self.sio.emit(
                "all_clear",
                {"message": "Routes cleared. Normal flow restored.", "region_id": region_id},
                room=room,
            )
            logger.info(f"MappingService: emitted all_clear room={room}")
        else:
            logger.info(f"MappingService (no sio): clear_detours room={room}")

        return {"status": "cleared", "room": room}

    async def send_updated_routes(
        self,
        routes: list,
        region_id: Optional[str] = None,
        reason: str = "congestion",
    ) -> dict:
        """
        Send updated routes after a recalculation (congestion / override trigger).

        Emits 'updated_recommendation' to the region room.

        Args:
            routes:    Updated route objects
            region_id: Region identifier
            reason:    Why routes were recalculated

        Returns:
            Status dict
        """
        geojson = extract_geojson(routes)
        room = f"reroute:{region_id}" if region_id else "reroute:global"

        if self.sio:
            await self.sio.emit(
                "updated_recommendation",
                {
                    "geojson": geojson,
                    "routes_count": len(routes),
                    "reason": reason,
                    "region_id": region_id,
                },
                room=room,
            )
            logger.info(
                f"MappingService: emitted updated_recommendation "
                f"routes={len(routes)} reason={reason} room={room}"
            )
        else:
            logger.info(
                f"MappingService (no sio): send_updated_routes "
                f"routes={len(routes)} room={room}"
            )

        return {"status": "updated", "routes_count": len(routes), "room": room}