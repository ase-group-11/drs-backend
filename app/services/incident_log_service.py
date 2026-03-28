# File: app/services/incident_log_service.py
"""
Incident Timeline Service — Disaster Logs / Activity History

13 events pulled from EXISTING tables only. No new table needed.

Event              Source Table        Column                      Actor       Badge
─────────────────  ──────────────────  ──────────────────────────  ──────────  ───────
Disaster Reported  disaster_reports    created_at                  Citizen     Citizen
Response Started   disasters           response_time               System      System
Units Deployed     deployments         dispatched_at               Admin User  Admin
Units Arrived      deployments         arrived_at / on_scene_at    System      System
Backup Requested   deployments         on_scene_at                 System      System
                                       WHERE request_immediate_backup = true
Mission Completed  deployments         completed_at                System      System
Reroute Triggered  audit_logs          created_at                  System      System
                                       WHERE event_type='traffic_rerouted'
Operator Override  audit_logs          created_at                  Admin User  Admin
                                       WHERE event_type='operator_override'
Traffic Restored   audit_logs          created_at                  System      System
                                       WHERE event_type='restored'
Evacuation Created evacuation_plans    created_at                  System      System
Evacuation Approved evacuation_plans   approved_at                 Admin User  Admin
Evacuation Activated evacuation_plans  activated_at                System      System
Disaster Resolved  disasters           resolved_time               Admin User  Admin
"""

import logging
from typing import Any, Dict, List

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class IncidentLogService:

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_timeline(self, disaster_id: str) -> Dict[str, Any]:
        """
        Return the full incident timeline for a disaster.

        Each entry returns:
          - title       : event name
          - actor       : System | Admin User | Citizen
          - badge       : System | Admin | Citizen
          - time        : HH:MM  (for display)
          - timestamp   : ISO    (for sorting)
          - event_type  : machine-readable key
        """
        logger.info(f"Building timeline for disaster {disaster_id}")

        try:
            # ── validate disaster ─────────────────────────────────
            result = await self.db.execute(
                text("""
                    SELECT id, tracking_id
                    FROM disasters
                    WHERE id = :did AND deleted_at IS NULL
                """),
                {"did": disaster_id},
            )
            disaster = result.mappings().first()
            if not disaster:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster not found.",
                )

            # ── UNION ALL — 13 event sources ──────────────────────
            rows = await self.db.execute(
                text("""
                    WITH timeline AS (

                        /* 1 — DISASTER REPORTED */
                        SELECT
                            dr.created_at            AS event_time,
                            'DISASTER_REPORTED'      AS event_type,
                            'Disaster Reported'      AS title,
                            'Citizen'                AS actor,
                            'Citizen'                AS badge
                        FROM disaster_reports dr
                        WHERE dr.disaster_id = :did
                          AND dr.deleted_at IS NULL

                        UNION ALL

                        /* 2 — RESPONSE STARTED */
                        SELECT
                            d.response_time          AS event_time,
                            'RESPONSE_STARTED'       AS event_type,
                            'Response Started'       AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM disasters d
                        WHERE d.id = :did
                          AND d.deleted_at IS NULL
                          AND d.response_time IS NOT NULL

                        UNION ALL

                        /* 3 — UNITS DEPLOYED */
                        SELECT
                            dep.dispatched_at        AS event_time,
                            'UNITS_DEPLOYED'         AS event_type,
                            'Units Deployed'         AS title,
                            'Admin User'             AS actor,
                            'Admin'                  AS badge
                        FROM deployments dep
                        WHERE dep.disaster_id = :did
                          AND dep.deleted_at IS NULL
                          AND dep.dispatched_at IS NOT NULL

                        UNION ALL

                        /* 4 — UNITS ARRIVED ON SCENE
                               arrived_at preferred; on_scene_at as fallback */
                        SELECT
                            COALESCE(dep.arrived_at, dep.on_scene_at) AS event_time,
                            'UNITS_ARRIVED'                           AS event_type,
                            'Units Arrived on Scene'                  AS title,
                            'System'                                  AS actor,
                            'System'                                  AS badge
                        FROM deployments dep
                        WHERE dep.disaster_id = :did
                          AND dep.deleted_at IS NULL
                          AND COALESCE(dep.arrived_at, dep.on_scene_at) IS NOT NULL

                        UNION ALL

                        /* 5 — BACKUP REQUESTED
                               uses on_scene_at as the closest timestamp
                               since there is no dedicated backup_requested_at column */
                        SELECT
                            dep.on_scene_at          AS event_time,
                            'BACKUP_REQUESTED'       AS event_type,
                            'Backup Requested'       AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM deployments dep
                        WHERE dep.disaster_id = :did
                          AND dep.deleted_at IS NULL
                          AND dep.request_immediate_backup = true
                          AND dep.on_scene_at IS NOT NULL

                        UNION ALL

                        /* 6 — MISSION COMPLETED */
                        SELECT
                            dep.completed_at         AS event_time,
                            'MISSION_COMPLETED'      AS event_type,
                            'Mission Completed'      AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM deployments dep
                        WHERE dep.disaster_id = :did
                          AND dep.deleted_at IS NULL
                          AND dep.completed_at IS NOT NULL

                        UNION ALL

                        /* 7 — REROUTE TRIGGERED */
                        SELECT
                            al.created_at            AS event_time,
                            'REROUTE_TRIGGERED'      AS event_type,
                            'Reroute Triggered'      AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM audit_logs al
                        WHERE al.disaster_id = :did
                          AND al.event_type = 'traffic_rerouted'
                          AND al.deleted_at IS NULL

                        UNION ALL

                        /* 8 — OPERATOR OVERRIDE */
                        SELECT
                            al.created_at            AS event_time,
                            'OPERATOR_OVERRIDE'      AS event_type,
                            'Operator Override'      AS title,
                            'Admin User'             AS actor,
                            'Admin'                  AS badge
                        FROM audit_logs al
                        WHERE al.disaster_id = :did
                          AND al.event_type = 'operator_override'
                          AND al.deleted_at IS NULL

                        UNION ALL

                        /* 9 — TRAFFIC RESTORED */
                        SELECT
                            al.created_at            AS event_time,
                            'TRAFFIC_RESTORED'       AS event_type,
                            'Traffic Restored'       AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM audit_logs al
                        WHERE al.disaster_id = :did
                          AND al.event_type = 'restored'
                          AND al.deleted_at IS NULL

                        UNION ALL

                        /* 10 — EVACUATION CREATED */
                        SELECT
                            ep.created_at            AS event_time,
                            'EVACUATION_CREATED'     AS event_type,
                            'Evacuation Created'     AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM evacuation_plans ep
                        WHERE ep.disaster_id = :did
                          AND ep.deleted_at IS NULL

                        UNION ALL

                        /* 11 — EVACUATION APPROVED */
                        SELECT
                            ep.approved_at           AS event_time,
                            'EVACUATION_APPROVED'    AS event_type,
                            'Evacuation Approved'    AS title,
                            'Admin User'             AS actor,
                            'Admin'                  AS badge
                        FROM evacuation_plans ep
                        WHERE ep.disaster_id = :did
                          AND ep.deleted_at IS NULL
                          AND ep.approved_at IS NOT NULL

                        UNION ALL

                        /* 12 — EVACUATION ACTIVATED */
                        SELECT
                            ep.activated_at          AS event_time,
                            'EVACUATION_ACTIVATED'   AS event_type,
                            'Evacuation Activated'   AS title,
                            'System'                 AS actor,
                            'System'                 AS badge
                        FROM evacuation_plans ep
                        WHERE ep.disaster_id = :did
                          AND ep.deleted_at IS NULL
                          AND ep.activated_at IS NOT NULL

                        UNION ALL

                        /* 13 — DISASTER RESOLVED */
                        SELECT
                            d.resolved_time          AS event_time,
                            'DISASTER_RESOLVED'      AS event_type,
                            'Disaster Resolved'      AS title,
                            'Admin User'             AS actor,
                            'Admin'                  AS badge
                        FROM disasters d
                        WHERE d.id = :did
                          AND d.deleted_at IS NULL
                          AND d.resolved_time IS NOT NULL

                    )
                    SELECT DISTINCT ON (event_type) *
                    FROM timeline
                    WHERE event_time IS NOT NULL
                    ORDER BY event_type, event_time ASC
                """),
                {"did": disaster_id},
            )

            entries: List[Dict[str, Any]] = []
            for row in rows.mappings().all():
                ts = row["event_time"]
                entries.append({
                    "event_type": row["event_type"],
                    "title":      row["title"],
                    "actor":      row["actor"],
                    "badge":      row["badge"],
                    "time":       ts.strftime("%H:%M") if ts else None,
                    "timestamp":  ts.isoformat()       if ts else None,
                })

            # newest first
            entries.sort(key=lambda e: e["timestamp"] or "", reverse=True)

            return {
                "disaster_id":   disaster_id,
                "tracking_id":   str(disaster["tracking_id"]),
                "total_entries": len(entries),
                "entries":       entries,
            }

        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(f"Timeline error for {disaster_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch incident timeline: {exc}",
            )