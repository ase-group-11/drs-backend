# File: app/services/disaster_service.py
"""
Disaster Service - Business logic for managing disasters after approval.

Handles:
  - Assign emergency team + department
  - Record response time (team arrived)
  - Resolve disaster with notes
  - Get disaster details
"""

import logging
from datetime import datetime
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException, status
from app.services.rabbitmq_service import publish_disaster_updated, publish_disaster_resolved

logger = logging.getLogger(__name__)

VALID_DEPARTMENTS = ["FIRE", "IT", "MEDICAL", "POLICE"]


class DisasterService:
    """Service layer for disaster management operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # LIST: Disasters by status (admin panel)
    # ──────────────────────────────────────────────
    async def list_disasters(
        self,
        disaster_status: str = None,
        severity: str = None,
        disaster_type: str = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """List disasters with filters. Supports admin Disaster Reports page."""
        try:
            conditions = ["d.deleted_at IS NULL"]
            params = {"limit": limit}

            if disaster_status:
                conditions.append("d.disaster_status = CAST(:disaster_status AS disaster_status)")
                params["disaster_status"] = disaster_status.upper()
            if severity:
                conditions.append("d.severity = CAST(:severity AS disaster_severity)")
                params["severity"] = severity.upper()
            if disaster_type:
                conditions.append("d.type = CAST(:disaster_type AS disaster_type)")
                params["disaster_type"] = disaster_type.upper()

            where_clause = " AND ".join(conditions)

            sql = text(f"""
                SELECT
                    d.id, d.tracking_id, d.type, d.severity, d.disaster_status,
                    d.description, d.location_address,
                    ST_Y(d.location::geometry) as latitude,
                    ST_X(d.location::geometry) as longitude,
                    d.people_affected, d.multiple_casualties,
                    d.structural_damage, d.road_blocked,
                    d.assigned_department, d.created_at,
                    d.response_time, d.resolved_time,
                    (SELECT COUNT(*) FROM disaster_reports r WHERE r.disaster_id = d.id) as report_count,
                    (SELECT COUNT(*) FROM deployments dep WHERE dep.disaster_id = d.id AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')) as units_assigned
                FROM disasters d
                WHERE {where_clause}
                ORDER BY
                    CASE d.severity
                        WHEN CAST('CRITICAL' AS disaster_severity) THEN 1
                        WHEN CAST('HIGH' AS disaster_severity) THEN 2
                        WHEN CAST('MEDIUM' AS disaster_severity) THEN 3
                        WHEN CAST('LOW' AS disaster_severity) THEN 4
                        ELSE 5
                    END,
                    d.created_at DESC
                LIMIT :limit
            """)

            result = await self.db.execute(sql, params)
            rows = result.mappings().all()

            # Get summary counts
            count_sql = text("""
                SELECT
                    COUNT(*) FILTER (WHERE severity = CAST('CRITICAL' AS disaster_severity) AND disaster_status = CAST('ACTIVE' AS disaster_status)) as critical_count,
                    COUNT(*) FILTER (WHERE disaster_status = CAST('ACTIVE' AS disaster_status)) as active_count,
                    COUNT(*) FILTER (WHERE disaster_status = CAST('RESOLVED' AS disaster_status)) as resolved_count,
                    COUNT(*) FILTER (WHERE disaster_status = CAST('UNVERIFIED' AS disaster_status)) as unverified_count
                FROM disasters WHERE deleted_at IS NULL
            """)
            count_result = await self.db.execute(count_sql)
            counts = count_result.mappings().first()

            disasters = []
            for row in rows:
                disasters.append({
                    "id": str(row["id"]),
                    "tracking_id": str(row["tracking_id"]),
                    "type": str(row["type"]),
                    "severity": str(row["severity"]),
                    "disaster_status": str(row["disaster_status"]),
                    "description": row["description"],
                    "location": {
                        "lat": float(row["latitude"]) if row["latitude"] else None,
                        "lon": float(row["longitude"]) if row["longitude"] else None,
                    },
                    "location_address": row["location_address"],
                    "people_affected": row["people_affected"],
                    "units_assigned": row["units_assigned"],
                    "report_count": row["report_count"],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "time_ago": self._time_ago(row["created_at"]) if row["created_at"] else None,
                })

            return {
                "disasters": disasters,
                "count": len(disasters),
                "summary": {
                    "critical": counts["critical_count"],
                    "active": counts["active_count"],
                    "resolved": counts["resolved_count"],
                    "unverified": counts["unverified_count"],
                },
            }

        except Exception as e:
            logger.exception(f"Error listing disasters: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to list disasters: {str(e)}")

    def _time_ago(self, dt: datetime) -> str:
        """Convert datetime to '15 mins ago' format."""
        if not dt:
            return None
        now = datetime.utcnow()
        diff = now - dt.replace(tzinfo=None) if dt.tzinfo else now - dt
        minutes = int(diff.total_seconds() / 60)
        if minutes < 1:
            return "Just now"
        elif minutes < 60:
            return f"{minutes} mins ago"
        elif minutes < 1440:
            return f"{minutes // 60} hours ago"
        else:
            return f"{minutes // 1440} days ago"

    # ──────────────────────────────────────────────
    # ASSIGN: Assign team + department to disaster
    # ──────────────────────────────────────────────
    async def assign_disaster(
        self,
        disaster_id: str,
        assigned_to_id: str,
        assigned_department: str,
    ) -> Dict[str, Any]:
        """Assign emergency team and department to an active disaster."""
        logger.info(f"Assigning disaster {disaster_id} to {assigned_to_id} ({assigned_department})")

        try:
            # Validate department
            if assigned_department.upper() not in VALID_DEPARTMENTS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid department. Must be one of: {', '.join(VALID_DEPARTMENTS)}"
                )

            # Check disaster exists and is active
            check_sql = text("""
                SELECT id, disaster_status, tracking_id
                FROM disasters
                WHERE id = :disaster_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"disaster_id": disaster_id})
            disaster = result.mappings().first()

            if not disaster:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster not found."
                )

            if str(disaster["disaster_status"]) != "ACTIVE":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Can only assign to ACTIVE disasters. Current status: {disaster['disaster_status']}"
                )

            # Validate assigned_to_id exists in emergency_teams
            team_sql = text("""
                SELECT id, full_name, department FROM emergency_teams
                WHERE id = :team_id AND deleted_at IS NULL
            """)
            team_result = await self.db.execute(team_sql, {"team_id": assigned_to_id})
            team = team_result.mappings().first()

            if not team:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Emergency team member not found."
                )

            now = datetime.utcnow()

            update_sql = text("""
                UPDATE disasters
                SET assigned_to_id = :assigned_to_id,
                    assigned_department = CAST(:assigned_department AS department),
                    updated_at = :updated_at
                WHERE id = :disaster_id
            """)

            await self.db.execute(update_sql, {
                "disaster_id": disaster_id,
                "assigned_to_id": assigned_to_id,
                "assigned_department": assigned_department.upper(),
                "updated_at": now,
            })

            await self.db.flush()

            logger.info(f"Disaster {disaster_id} assigned to {team['full_name']} ({assigned_department})")

            return {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "assigned_to_id": assigned_to_id,
                "assigned_to_name": team["full_name"],
                "assigned_department": assigned_department.upper(),
                "assigned_at": now.isoformat(),
                "message": f"Disaster assigned to {team['full_name']} ({assigned_department.upper()})",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error assigning disaster: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to assign disaster: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # RESPOND: Record response time (team arrived)
    # ──────────────────────────────────────────────
    async def respond_disaster(
        self,
        disaster_id: str,
        response_notes: str = None,
    ) -> Dict[str, Any]:
        """Record when first responder arrives on scene."""
        logger.info(f"Recording response time for disaster {disaster_id}")

        try:
            check_sql = text("""
                SELECT id, disaster_status, tracking_id, assigned_to_id, assigned_department, response_time
                FROM disasters
                WHERE id = :disaster_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"disaster_id": disaster_id})
            disaster = result.mappings().first()

            if not disaster:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster not found."
                )

            if str(disaster["disaster_status"]) != "ACTIVE":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Can only respond to ACTIVE disasters. Current status: {disaster['disaster_status']}"
                )

            if not disaster["assigned_to_id"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Disaster must be assigned before recording response time. Use /assign first."
                )

            if disaster["response_time"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Response time already recorded: {disaster['response_time']}"
                )

            now = datetime.utcnow()

            update_sql = text("""
                UPDATE disasters
                SET response_time = :response_time,
                    updated_at = :updated_at
                WHERE id = :disaster_id
            """)

            await self.db.execute(update_sql, {
                "disaster_id": disaster_id,
                "response_time": now,
                "updated_at": now,
            })

            await self.db.flush()

            logger.info(f"Response time recorded for disaster {disaster_id}")

            # Publish to RabbitMQ → triggers notification
            publish_disaster_updated({
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "update_type": "response_recorded",
                "details": f"Emergency team arrived on scene at {now.isoformat()}",
            })

            return {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "response_time": now.isoformat(),
                "assigned_department": str(disaster["assigned_department"]),
                "message": f"Response time recorded for disaster {disaster['tracking_id']}",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error recording response: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to record response time: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # RESOLVE: Mark disaster as resolved
    # ──────────────────────────────────────────────
    async def resolve_disaster(
        self,
        disaster_id: str,
        resolution_notes: str,
    ) -> Dict[str, Any]:
        """Mark disaster as resolved with notes."""
        logger.info(f"Resolving disaster {disaster_id}")

        try:
            check_sql = text("""
                SELECT id, disaster_status, tracking_id, assigned_to_id
                FROM disasters
                WHERE id = :disaster_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"disaster_id": disaster_id})
            disaster = result.mappings().first()

            if not disaster:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster not found."
                )

            if str(disaster["disaster_status"]) == "RESOLVED":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Disaster is already resolved."
                )

            now = datetime.utcnow()

            update_sql = text("""
                UPDATE disasters
                SET disaster_status = CAST(:disaster_status AS disaster_status),
                    resolved_time = :resolved_time,
                    resolution_notes = :resolution_notes,
                    updated_at = :updated_at
                WHERE id = :disaster_id
            """)

            await self.db.execute(update_sql, {
                "disaster_id": disaster_id,
                "disaster_status": "RESOLVED",
                "resolved_time": now,
                "resolution_notes": resolution_notes,
                "updated_at": now,
            })

            await self.db.flush()

            logger.info(f"Disaster {disaster_id} RESOLVED")

            # Publish to RabbitMQ → triggers notification + reroute restore
            publish_disaster_resolved({
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "resolution_notes": resolution_notes,
                "resolved_time": now.isoformat(),
            })

            return {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "disaster_status": "RESOLVED",
                "resolved_time": now.isoformat(),
                "resolution_notes": resolution_notes,
                "message": f"Disaster {disaster['tracking_id']} has been resolved.",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error resolving disaster: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to resolve disaster: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # ESCALATE: Update disaster severity
    # ──────────────────────────────────────────────
    async def escalate_disaster(
        self,
        disaster_id: str,
        new_severity: str,
        reason: str = None,
    ) -> Dict[str, Any]:
        """Escalate disaster severity level."""
        try:
            check_sql = text("""
                SELECT id, tracking_id, severity, disaster_status
                FROM disasters WHERE id = :disaster_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"disaster_id": disaster_id})
            disaster = result.mappings().first()

            if not disaster:
                raise HTTPException(status_code=404, detail="Disaster not found.")

            if str(disaster["disaster_status"]) == "RESOLVED":
                raise HTTPException(status_code=400, detail="Cannot escalate a resolved disaster.")

            now = datetime.utcnow()
            update_sql = text("""
                UPDATE disasters
                SET severity = CAST(:severity AS disaster_severity),
                    updated_at = :updated_at
                WHERE id = :disaster_id
            """)
            await self.db.execute(update_sql, {
                "disaster_id": disaster_id,
                "severity": new_severity.upper(),
                "updated_at": now,
            })

            await self.db.flush()

            # Publish update event
            publish_disaster_updated({
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "update_type": "severity_escalated",
                "details": f"Severity changed from {disaster['severity']} to {new_severity.upper()}. Reason: {reason or 'N/A'}",
            })

            return {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "previous_severity": str(disaster["severity"]),
                "new_severity": new_severity.upper(),
                "reason": reason,
                "message": f"Disaster {disaster['tracking_id']} escalated to {new_severity.upper()}",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error escalating disaster: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to escalate: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Photos for a disaster (from linked reports)
    # ──────────────────────────────────────────────
    async def get_disaster_photos(self, disaster_id: str) -> List[Dict[str, Any]]:
        """Get all photos linked to a disaster via its reports."""
        try:
            sql = text("""
                SELECT
                    p.id, p.image_url, p.caption, p.file_size, p.mime_type,
                    p.disaster_report_id, p.reference_id,
                    p.created_at,
                    r.user_id, r.location_address as report_address
                FROM disaster_photos p
                JOIN disaster_reports r ON p.disaster_report_id = r.id
                WHERE r.disaster_id = :disaster_id
                  AND p.deleted_at IS NULL
                ORDER BY p.created_at ASC
            """)

            result = await self.db.execute(sql, {"disaster_id": disaster_id})
            rows = result.mappings().all()

            return [{
                "id": str(row["id"]),
                "image_url": row["image_url"],
                "caption": row["caption"],
                "file_size": row["file_size"],
                "mime_type": row["mime_type"],
                "report_id": str(row["disaster_report_id"]),
                "uploaded_by": str(row["user_id"]),
                "report_address": row["report_address"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            } for row in rows]

        except Exception as e:
            logger.exception(f"Error fetching disaster photos: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch photos: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Deployment summary for a disaster
    # ──────────────────────────────────────────────
    async def get_disaster_deployments(self, disaster_id: str) -> Dict[str, Any]:
        """Get all deployments for a disaster with unit info."""
        try:
            sql = text("""
                SELECT
                    dep.id as deployment_id, dep.deployment_status,
                    dep.dispatched_at, dep.arrived_at, dep.completed_at,
                    dep.priority_level, dep.situation_report,
                    dep.minor_injuries, dep.serious_injuries,
                    eu.id as unit_id, eu.unit_code, eu.unit_name,
                    eu.unit_type, eu.department, eu.unit_status
                FROM deployments dep
                JOIN emergency_units eu ON dep.unit_id = eu.id
                WHERE dep.disaster_id = :disaster_id
                  AND dep.deleted_at IS NULL
                ORDER BY dep.dispatched_at ASC
            """)

            result = await self.db.execute(sql, {"disaster_id": disaster_id})
            rows = result.mappings().all()

            deployments = [{
                "deployment_id": str(row["deployment_id"]),
                "deployment_status": row["deployment_status"],
                "priority_level": row["priority_level"],
                "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
                "arrived_at": row["arrived_at"].isoformat() if row["arrived_at"] else None,
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                "situation_report": row["situation_report"],
                "casualties": {
                    "minor": row["minor_injuries"] or 0,
                    "serious": row["serious_injuries"] or 0,
                },
                "unit": {
                    "id": str(row["unit_id"]),
                    "unit_code": str(row["unit_code"]),
                    "unit_name": str(row["unit_name"]),
                    "unit_type": str(row["unit_type"]),
                    "department": str(row["department"]),
                    "current_status": str(row["unit_status"]),
                },
            } for row in rows]

            # Summary
            total = len(deployments)
            dispatched = sum(1 for d in deployments if d["deployment_status"] == "DISPATCHED")
            en_route = sum(1 for d in deployments if d["deployment_status"] == "EN_ROUTE")
            on_scene = sum(1 for d in deployments if d["deployment_status"] in ("ON_SCENE", "IN_PROGRESS"))
            completed = sum(1 for d in deployments if d["deployment_status"] == "COMPLETED")

            return {
                "disaster_id": disaster_id,
                "deployments": deployments,
                "total_units": total,
                "summary": {
                    "dispatched": dispatched,
                    "en_route": en_route,
                    "on_scene": on_scene,
                    "completed": completed,
                },
            }

        except Exception as e:
            logger.exception(f"Error fetching deployments: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch deployments: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Single disaster by ID
    # ──────────────────────────────────────────────
    async def get_disaster(self, disaster_id: str) -> Dict[str, Any]:
        """Get full disaster details."""
        try:
            sql = text("""
                SELECT
                    d.id, d.tracking_id, d.type, d.severity, d.disaster_status,
                    ST_Y(d.location::geometry) as latitude,
                    ST_X(d.location::geometry) as longitude,
                    d.location_address, d.affected_area,
                    d.description, d.people_affected,
                    d.multiple_casualties, d.structural_damage, d.road_blocked,
                    d.assigned_to_id, d.assigned_department,
                    d.response_time, d.resolved_time, d.resolution_notes,
                    d.created_by_id, d.disaster_metadata,
                    d.created_at, d.updated_at,
                    et.full_name as assigned_to_name,
                    et.phone_number as assigned_to_phone,
                    (SELECT COUNT(*) FROM disaster_reports r WHERE r.disaster_id = d.id) as report_count
                FROM disasters d
                LEFT JOIN emergency_teams et ON d.assigned_to_id = et.id
                WHERE d.id = :disaster_id AND d.deleted_at IS NULL
            """)

            result = await self.db.execute(sql, {"disaster_id": disaster_id})
            row = result.mappings().first()

            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster not found."
                )

            return {
                "id": str(row["id"]),
                "tracking_id": str(row["tracking_id"]),
                "type": str(row["type"]),
                "severity": str(row["severity"]),
                "disaster_status": str(row["disaster_status"]),
                "location": {
                    "lat": float(row["latitude"]) if row["latitude"] else None,
                    "lon": float(row["longitude"]) if row["longitude"] else None,
                },
                "location_address": row["location_address"],
                "affected_area": row["affected_area"],
                "description": row["description"],
                "people_affected": row["people_affected"],
                "multiple_casualties": row["multiple_casualties"],
                "structural_damage": row["structural_damage"],
                "road_blocked": row["road_blocked"],
                "assignment": {
                    "assigned_to_id": str(row["assigned_to_id"]) if row["assigned_to_id"] else None,
                    "assigned_to_name": row["assigned_to_name"],
                    "assigned_to_phone": row["assigned_to_phone"],
                    "assigned_department": str(row["assigned_department"]) if row["assigned_department"] else None,
                },
                "timeline": {
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    "response_time": row["response_time"].isoformat() if row["response_time"] else None,
                    "resolved_time": row["resolved_time"].isoformat() if row["resolved_time"] else None,
                },
                "resolution_notes": row["resolution_notes"],
                "created_by_id": str(row["created_by_id"]) if row["created_by_id"] else None,
                "report_count": row["report_count"] or 0,
                "disaster_metadata": row["disaster_metadata"],
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching disaster: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch disaster: {str(e)}"
            )