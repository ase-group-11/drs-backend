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
from typing import Dict, Any

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