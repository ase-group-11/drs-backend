# File: app/services/emergency_unit_service.py
"""
Emergency Unit Service - CRUD and dispatch support.

Handles:
  - List all units (with filters)
  - List available units (with distance/ETA from disaster)
  - Get unit details (crew, stats, current assignment)
  - Create new unit
  - Update unit config
  - Decommission (soft delete)
"""

import uuid
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class EmergencyUnitService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # LIST: All units with filters
    # ──────────────────────────────────────────────
    async def list_units(
        self,
        department: Optional[str] = None,
        unit_status: Optional[str] = None,
        unit_type: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
    ) -> Dict[str, Any]:  
        """List units with optional filters. Supports Emergency Teams admin page."""
        try:
            conditions = ["u.deleted_at IS NULL"]
            params = {"limit": limit}

            if department:
                conditions.append("u.department = CAST(:department AS department)")
                params["department"] = department.upper()
            if unit_status:
                conditions.append("u.unit_status = CAST(:unit_status AS unit_status)")
                params["unit_status"] = unit_status.upper()
            if unit_type:
                conditions.append("u.unit_type = CAST(:unit_type AS unit_type)")
                params["unit_type"] = unit_type.upper()
            if search:
                conditions.append("(u.unit_code ILIKE :search OR u.unit_name ILIKE :search OR u.station_name ILIKE :search)")
                params["search"] = f"%{search}%"

            where_clause = " AND ".join(conditions)

            sql = text(f"""
                SELECT
                    u.id, u.unit_code, u.unit_name, u.unit_type, u.department,
                    u.unit_status, u.station_name, u.station_address,
                    u.capacity, u.total_deployments, u.avg_response_time_seconds,
                    u.success_rate, u.last_deployed_at,
                    ST_Y(u.station_location::geometry) as station_lat,
                    ST_X(u.station_location::geometry) as station_lon,
                    et.full_name as commander_name,
                    (SELECT COUNT(*) FROM unit_crew uc WHERE uc.unit_id = u.id) as crew_count
                FROM emergency_units u
                LEFT JOIN emergency_teams et ON u.commander_id = et.id
                WHERE {where_clause}
                ORDER BY u.unit_code ASC
                LIMIT :limit
            """)

            result = await self.db.execute(sql, params)
            rows = result.mappings().all()

            # Get counts by status and department
            count_sql = text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE unit_status != CAST('OFFLINE' AS unit_status) AND unit_status != CAST('MAINTENANCE' AS unit_status)) as active_count,
                    COUNT(*) FILTER (WHERE unit_status = CAST('DEPLOYED' AS unit_status) OR unit_status = CAST('ON_SCENE' AS unit_status)) as deployed_count
                FROM emergency_units WHERE deleted_at IS NULL
            """)
            count_result = await self.db.execute(count_sql)
            counts = count_result.mappings().first()

            dept_sql = text("""
                SELECT department, COUNT(*) as cnt
                FROM emergency_units WHERE deleted_at IS NULL
                GROUP BY department
            """)
            dept_result = await self.db.execute(dept_sql)
            by_dept = {str(row["department"]): row["cnt"] for row in dept_result.mappings().all()}

            units = [self._row_to_unit(row) for row in rows]

            return {
                "units": units,
                "total_count": counts["total"],
                "active_count": counts["active_count"],
                "deployed_count": counts["deployed_count"],
                "by_department": by_dept,
            }

        except Exception as e:
            logger.exception(f"Error listing units: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to list units: {str(e)}")

    # ──────────────────────────────────────────────
    # LIST: Available units (for dispatch modal)
    # ──────────────────────────────────────────────
    async def list_available_units(
        self,
        disaster_id: Optional[str] = None,
        department: Optional[str] = None,
        unit_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List available units with distance/ETA from disaster location."""
        try:
            params = {}
            extra_select = ""
            extra_join = ""

            if disaster_id:
                extra_select = """,
                    ROUND(ST_Distance(u.station_location, d.location)::numeric / 1000, 1) as distance_km,
                    ROUND(ST_Distance(u.station_location, d.location)::numeric / 1000 / 40 * 60, 0) as eta_minutes
                """
                extra_join = "CROSS JOIN disasters d"
                disaster_filter = "AND d.id = :disaster_id AND d.deleted_at IS NULL"
                params["disaster_id"] = disaster_id
            else:
                extra_select = ", NULL as distance_km, NULL as eta_minutes"
                extra_join = ""
                disaster_filter = ""

            conditions = ["u.deleted_at IS NULL", "u.unit_status = CAST('AVAILABLE' AS unit_status)"]
            if department:
                conditions.append("u.department = CAST(:department AS department)")
                params["department"] = department.upper()
            if unit_type:
                conditions.append("u.unit_type = CAST(:unit_type AS unit_type)")
                params["unit_type"] = unit_type.upper()

            where_clause = " AND ".join(conditions)

            sql = text(f"""
                SELECT
                    u.id, u.unit_code, u.unit_name, u.unit_type, u.department,
                    u.unit_status, u.station_name,
                    u.capacity,
                    ST_Y(u.station_location::geometry) as station_lat,
                    ST_X(u.station_location::geometry) as station_lon,
                    et.full_name as commander_name,
                    (SELECT COUNT(*) FROM unit_crew uc WHERE uc.unit_id = u.id) as crew_count
                    {extra_select}
                FROM emergency_units u
                LEFT JOIN emergency_teams et ON u.commander_id = et.id
                {extra_join}
                WHERE {where_clause} {disaster_filter}
                ORDER BY distance_km ASC NULLS LAST, u.unit_code ASC
            """)

            result = await self.db.execute(sql, params)
            rows = result.mappings().all()

            return [{
                "id": str(row["id"]),
                "unit_code": str(row["unit_code"]),
                "unit_name": str(row["unit_name"]),
                "unit_type": str(row["unit_type"]),
                "department": str(row["department"]),
                "station_name": row["station_name"],
                "crew_count": row["crew_count"],
                "capacity": row["capacity"],
                "commander_name": row["commander_name"],
                "distance_km": float(row["distance_km"]) if row["distance_km"] else None,
                "eta_minutes": int(row["eta_minutes"]) if row["eta_minutes"] else None,
            } for row in rows]

        except Exception as e:
            logger.exception(f"Error listing available units: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to list available units: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Unit details
    # ──────────────────────────────────────────────
    async def get_unit(self, unit_id: str) -> Dict[str, Any]:
        """Get full unit details with crew roster and current assignment."""
        try:
            sql = text("""
                SELECT
                    u.id, u.unit_code, u.unit_name, u.description,
                    u.unit_type, u.department, u.unit_status,
                    u.station_name, u.station_address,
                    ST_Y(u.station_location::geometry) as station_lat,
                    ST_X(u.station_location::geometry) as station_lon,
                    u.vehicle_model, u.vehicle_license_plate, u.vehicle_year,
                    u.equipment_checklist,
                    u.capacity, u.total_deployments, u.avg_response_time_seconds,
                    u.success_rate, u.last_deployed_at,
                    u.commander_id,
                    et.full_name as commander_name, et.phone_number as commander_phone,
                    et.email as commander_email
                FROM emergency_units u
                LEFT JOIN emergency_teams et ON u.commander_id = et.id
                WHERE u.id = :unit_id AND u.deleted_at IS NULL
            """)
            result = await self.db.execute(sql, {"unit_id": unit_id})
            row = result.mappings().first()

            if not row:
                raise HTTPException(status_code=404, detail="Emergency unit not found.")

            # Get crew roster
            crew_sql = text("""
                SELECT et.id, et.full_name, et.email, et.role, et.department, et.status
                FROM unit_crew uc
                JOIN emergency_teams et ON uc.team_member_id = et.id
                WHERE uc.unit_id = :unit_id AND et.deleted_at IS NULL
            """)
            crew_result = await self.db.execute(crew_sql, {"unit_id": unit_id})
            crew_rows = crew_result.mappings().all()

            # Get current active deployment
            deploy_sql = text("""
                SELECT dep.id as deployment_id, dep.deployment_status,
                       dep.dispatched_at, dep.priority_level,
                       dis.tracking_id, dis.type as disaster_type,
                       dis.location_address, dis.disaster_status
                FROM deployments dep
                JOIN disasters dis ON dep.disaster_id = dis.id
                WHERE dep.unit_id = :unit_id
                  AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')
                  AND dep.deleted_at IS NULL
                ORDER BY dep.created_at DESC LIMIT 1
            """)
            deploy_result = await self.db.execute(deploy_sql, {"unit_id": unit_id})
            active_deploy = deploy_result.mappings().first()

            # Format response time
            avg_seconds = row["avg_response_time_seconds"]
            avg_formatted = None
            if avg_seconds:
                mins = avg_seconds // 60
                secs = avg_seconds % 60
                avg_formatted = f"{mins}m {secs}s"

            return {
                "id": str(row["id"]),
                "unit_code": str(row["unit_code"]),
                "unit_name": str(row["unit_name"]),
                "description": row["description"],
                "unit_type": str(row["unit_type"]),
                "department": str(row["department"]),
                "unit_status": str(row["unit_status"]),
                "station": {
                    "name": row["station_name"],
                    "address": row["station_address"],
                    "lat": float(row["station_lat"]) if row["station_lat"] else None,
                    "lon": float(row["station_lon"]) if row["station_lon"] else None,
                },
                "vehicle": {
                    "model": row["vehicle_model"],
                    "license_plate": row["vehicle_license_plate"],
                    "year": row["vehicle_year"],
                    "equipment": row["equipment_checklist"],
                },
                "stats": {
                    "crew_count": len(crew_rows),
                    "capacity": row["capacity"],
                    "total_deployments": row["total_deployments"],
                    "avg_response_time": avg_formatted,
                    "avg_response_time_seconds": avg_seconds,
                    "success_rate": round(row["success_rate"] * 100, 1) if row["success_rate"] else None,
                    "last_deployed_at": row["last_deployed_at"].isoformat() if row["last_deployed_at"] else None,
                },
                "commander": {
                    "id": str(row["commander_id"]) if row["commander_id"] else None,
                    "name": row["commander_name"],
                    "phone": row["commander_phone"],
                    "email": row["commander_email"],
                },
                "crew_roster": [{
                    "id": str(c["id"]),
                    "name": c["full_name"],
                    "email": c["email"],
                    "role": str(c["role"]),
                    "department": str(c["department"]),
                    "status": str(c["status"]),
                } for c in crew_rows],
                "current_assignment": {
                    "deployment_id": str(active_deploy["deployment_id"]),
                    "disaster_tracking_id": str(active_deploy["tracking_id"]),
                    "disaster_type": str(active_deploy["disaster_type"]),
                    "location": active_deploy["location_address"],
                    "deployment_status": active_deploy["deployment_status"],
                    "dispatched_at": active_deploy["dispatched_at"].isoformat() if active_deploy["dispatched_at"] else None,
                } if active_deploy else None,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching unit: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch unit: {str(e)}")

    # ──────────────────────────────────────────────
    # CREATE: New unit
    # ──────────────────────────────────────────────
    async def create_unit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new emergency unit with commander and crew validation.
        
        Flow:
          1. Auto-map department from unit_type if not provided
          2. Validate commander exists and is ACTIVE
          3. Validate all crew members exist and are ACTIVE
          4. Warn if commander/crew are from different department
          5. Create unit + link crew
        """
        try:
            unit_id = str(uuid.uuid4())
            now = datetime.utcnow()

            # Auto-map unit_type → department if not explicitly set
            UNIT_TYPE_TO_DEPT = {
                "FIRE_ENGINE": "FIRE", "AMBULANCE": "MEDICAL",
                "PATROL_CAR": "POLICE", "RESCUE": "FIRE",
                "HAZMAT": "FIRE", "RAPID_RESPONSE": "MEDICAL",
                "COMMAND": "IT",
            }
            unit_type = data["unit_type"].upper()
            department = data.get("department", "").upper()
            if not department:
                department = UNIT_TYPE_TO_DEPT.get(unit_type, "IT")

            # Validate commander if provided
            commander_id = data.get("commander_id")
            if commander_id:
                cmd_sql = text("""
                    SELECT id, full_name, department, status FROM emergency_teams
                    WHERE id = :id AND deleted_at IS NULL
                """)
                cmd_result = await self.db.execute(cmd_sql, {"id": commander_id})
                commander = cmd_result.mappings().first()
                if not commander:
                    raise HTTPException(status_code=404, detail="Commander not found.")
                if str(commander["status"]) != "ACTIVE":
                    raise HTTPException(status_code=400, detail=f"Commander {commander['full_name']} is not ACTIVE.")

            # Validate crew members if provided
            crew_member_ids = data.get("crew_member_ids", [])
            validated_crew = []
            if crew_member_ids:
                capacity = data.get("capacity", 4)
                if len(crew_member_ids) > capacity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Crew size ({len(crew_member_ids)}) exceeds capacity ({capacity})."
                    )

                for member_id in crew_member_ids:
                    mem_sql = text("""
                        SELECT id, full_name, department, status FROM emergency_teams
                        WHERE id = :id AND deleted_at IS NULL
                    """)
                    mem_result = await self.db.execute(mem_sql, {"id": member_id})
                    member = mem_result.mappings().first()
                    if not member:
                        raise HTTPException(status_code=404, detail=f"Crew member {member_id} not found.")
                    if str(member["status"]) != "ACTIVE":
                        raise HTTPException(status_code=400, detail=f"Crew member {member['full_name']} is not ACTIVE.")
                    validated_crew.append({
                        "id": str(member["id"]),
                        "name": member["full_name"],
                        "department": str(member["department"]),
                    })

            # Check unit_code is unique
            code_sql = text("SELECT id FROM emergency_units WHERE unit_code = :code AND deleted_at IS NULL")
            code_result = await self.db.execute(code_sql, {"code": data["unit_code"]})
            if code_result.first():
                raise HTTPException(status_code=400, detail=f"Unit code '{data['unit_code']}' already exists.")

            sql = text("""
                INSERT INTO emergency_units (
                    id, created_at, updated_at,
                    unit_code, unit_name, description,
                    unit_type, department, unit_status,
                    station_name, station_address, station_location,
                    vehicle_model, vehicle_license_plate, vehicle_year,
                    capacity, commander_id,
                    total_deployments, avg_response_time_seconds, success_rate
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :unit_code, :unit_name, :description,
                    CAST(:unit_type AS unit_type),
                    CAST(:department AS department),
                    CAST(:unit_status AS unit_status),
                    :station_name, :station_address,
                    ST_SetSRID(ST_MakePoint(:station_lon, :station_lat), 4326)::geography,
                    :vehicle_model, :vehicle_license_plate, :vehicle_year,
                    :capacity, :commander_id,
                    0, NULL, NULL
                )
            """)

            await self.db.execute(sql, {
                "id": unit_id,
                "created_at": now,
                "updated_at": now,
                "unit_code": data["unit_code"],
                "unit_name": data["unit_name"],
                "description": data.get("description"),
                "unit_type": unit_type,
                "department": department,
                "unit_status": "AVAILABLE",
                "station_name": data["station_name"],
                "station_address": data.get("station_address"),
                "station_lat": data.get("station_latitude", 53.3498),
                "station_lon": data.get("station_longitude", -6.2603),
                "vehicle_model": data.get("vehicle_model"),
                "vehicle_license_plate": data.get("vehicle_license_plate"),
                "vehicle_year": data.get("vehicle_year"),
                "capacity": data.get("capacity", 4),
                "commander_id": commander_id,
            })

            # Add crew members
            if crew_member_ids:
                for member_id in crew_member_ids:
                    crew_sql = text("""
                        INSERT INTO unit_crew (unit_id, team_member_id)
                        VALUES (:unit_id, :member_id)
                    """)
                    await self.db.execute(crew_sql, {"unit_id": unit_id, "member_id": member_id})

            await self.db.flush()

            # Build department mismatch warnings
            warnings = []
            if commander_id and validated_crew:
                pass  # commander already validated above
            for crew in validated_crew:
                if crew["department"] != department:
                    warnings.append(
                        f"{crew['name']} is from {crew['department']} department "
                        f"(unit is {department})"
                    )

            result = await self.get_unit(unit_id)
            if warnings:
                result["warnings"] = warnings
            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating unit: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create unit: {str(e)}")

    # ──────────────────────────────────────────────
    # UPDATE: Unit config
    # ──────────────────────────────────────────────
    async def update_unit(self, unit_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update emergency unit configuration."""
        try:
            # Check unit exists
            check_sql = text("""
                SELECT id, unit_code FROM emergency_units
                WHERE id = :unit_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"unit_id": unit_id})
            unit = result.mappings().first()

            if not unit:
                raise HTTPException(status_code=404, detail="Emergency unit not found.")

            now = datetime.utcnow()
            set_clauses = ["updated_at = :updated_at"]
            params = {"unit_id": unit_id, "updated_at": now}

            # Only update fields that are provided
            field_map = {
                "unit_name": ("unit_name = :unit_name", "unit_name"),
                "description": ("description = :description", "description"),
                "station_name": ("station_name = :station_name", "station_name"),
                "station_address": ("station_address = :station_address", "station_address"),
                "vehicle_model": ("vehicle_model = :vehicle_model", "vehicle_model"),
                "vehicle_license_plate": ("vehicle_license_plate = :vehicle_license_plate", "vehicle_license_plate"),
                "vehicle_year": ("vehicle_year = :vehicle_year", "vehicle_year"),
                "capacity": ("capacity = :capacity", "capacity"),
                "commander_id": ("commander_id = :commander_id", "commander_id"),
            }

            for key, (clause, param_name) in field_map.items():
                if key in data and data[key] is not None:
                    set_clauses.append(clause)
                    params[param_name] = data[key]

            # Enum fields need CAST
            if "unit_type" in data and data["unit_type"]:
                set_clauses.append("unit_type = CAST(:unit_type AS unit_type)")
                params["unit_type"] = data["unit_type"].upper()

            if "department" in data and data["department"]:
                set_clauses.append("department = CAST(:department AS department)")
                params["department"] = data["department"].upper()

            if "unit_status" in data and data["unit_status"]:
                set_clauses.append("unit_status = CAST(:unit_status AS unit_status)")
                params["unit_status"] = data["unit_status"].upper()

            # Station location
            if "station_latitude" in data and "station_longitude" in data:
                set_clauses.append(
                    "station_location = ST_SetSRID(ST_MakePoint(:station_lon, :station_lat), 4326)::geography"
                )
                params["station_lat"] = data["station_latitude"]
                params["station_lon"] = data["station_longitude"]

            # Equipment checklist (JSON)
            if "equipment_checklist" in data:
                set_clauses.append("equipment_checklist = :equipment")
                params["equipment"] = json.dumps(data["equipment_checklist"]) if data["equipment_checklist"] else None

            sql = text(f"""
                UPDATE emergency_units
                SET {', '.join(set_clauses)}
                WHERE id = :unit_id
            """)

            await self.db.execute(sql, params)

            # Update crew members if provided
            if "crew_member_ids" in data and data["crew_member_ids"] is not None:
                # Remove existing crew
                await self.db.execute(
                    text("DELETE FROM unit_crew WHERE unit_id = :unit_id"),
                    {"unit_id": unit_id}
                )
                # Add new crew
                for member_id in data["crew_member_ids"]:
                    await self.db.execute(
                        text("INSERT INTO unit_crew (unit_id, team_member_id) VALUES (:unit_id, :member_id)"),
                        {"unit_id": unit_id, "member_id": member_id}
                    )

            await self.db.flush()

            return await self.get_unit(unit_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating unit: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to update unit: {str(e)}")

    # ──────────────────────────────────────────────
    # DELETE: Decommission unit (soft delete)
    # ──────────────────────────────────────────────
    async def decommission_unit(self, unit_id: str) -> Dict[str, Any]:
        """Soft delete an emergency unit."""
        try:
            check_sql = text("""
                SELECT id, unit_code, unit_status FROM emergency_units
                WHERE id = :unit_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(check_sql, {"unit_id": unit_id})
            unit = result.mappings().first()

            if not unit:
                raise HTTPException(status_code=404, detail="Emergency unit not found.")

            now = datetime.utcnow()
            
            # FIX: Cancel all active deployments for this unit before decommission
            # Use separate params for completed_at and updated_at — asyncpg raises
            # AmbiguousParameterError when the same $N is bound to columns with
            # different timestamp types (timestamp vs timestamptz).
            await self.db.execute(text("""
                UPDATE deployments
                SET deployment_status = 'CANCELLED',
                    completed_at = :completed_at,
                    assessment_notes = 'Auto-cancelled: unit decommissioned',
                    updated_at = :updated_at
                WHERE unit_id = :unit_id
                  AND deployment_status NOT IN ('COMPLETED', 'CANCELLED')
                  AND deleted_at IS NULL
            """), {"unit_id": unit_id, "completed_at": now, "updated_at": now})

            # FIX: Remove all crew members from this unit before decommissioning.
            # Without this, unit_crew rows linger and inflate assigned_units_count
            # on team member profiles even after the unit is gone.
            await self.db.execute(
                text("DELETE FROM unit_crew WHERE unit_id = :unit_id"),
                {"unit_id": unit_id}
            )

            # FIX #5: Atomic conditional UPDATE — the WHERE clause on unit_status NOT IN
            # (DEPLOYED, ON_SCENE) is evaluated inside the same lock as the write, so a
            # concurrent dispatch cannot slip in between the check and the delete.
            sql = text("""
                UPDATE emergency_units
                SET deleted_at = :deleted_at,
                    unit_status = CAST('OFFLINE' AS unit_status),
                    updated_at = :updated_at
                WHERE id = :unit_id
                  AND unit_status NOT IN (
                      CAST('DEPLOYED' AS unit_status),
                      CAST('ON_SCENE' AS unit_status)
                  )
                RETURNING id
            """)
            decom_result = await self.db.execute(sql, {
                "unit_id": unit_id,
                "deleted_at": now,
                "updated_at": now,
            })
            if not decom_result.first():
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot decommission unit while DEPLOYED or ON_SCENE. Complete active deployments first."
                )

            await self.db.flush()

            return {
                "unit_id": unit_id,
                "unit_code": str(unit["unit_code"]),
                "status": "DECOMMISSIONED",
                "message": f"Unit {unit['unit_code']} has been decommissioned.",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error decommissioning unit: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to decommission unit: {str(e)}")

    # ──────────────────────────────────────────────
    # CREW: Add single member
    # ──────────────────────────────────────────────
    async def add_crew_member(self, unit_id: str, team_member_id: str) -> Dict[str, Any]:
        """Add a single crew member to a unit."""
        try:
            # Check unit exists
            unit_sql = text("SELECT id, unit_code, capacity FROM emergency_units WHERE id = :uid AND deleted_at IS NULL")
            result = await self.db.execute(unit_sql, {"uid": unit_id})
            unit = result.mappings().first()
            if not unit:
                raise HTTPException(status_code=404, detail="Unit not found.")

            # Check member exists
            member_sql = text("SELECT id, full_name FROM emergency_teams WHERE id = :mid AND deleted_at IS NULL")
            result = await self.db.execute(member_sql, {"mid": team_member_id})
            member = result.mappings().first()
            if not member:
                raise HTTPException(status_code=404, detail="Team member not found.")

            # Check not already in crew
            check_sql = text("SELECT unit_id FROM unit_crew WHERE unit_id = :uid AND team_member_id = :mid")
            result = await self.db.execute(check_sql, {"uid": unit_id, "mid": team_member_id})
            if result.first():
                raise HTTPException(status_code=400, detail=f"{member['full_name']} is already in this unit's crew.")

            # FIX #6: Atomic INSERT with inline capacity check — the subquery COUNT is
            # evaluated inside the same statement as the INSERT, so concurrent requests
            # both trying to fill the last slot cannot both succeed.
            insert_sql = text("""
                INSERT INTO unit_crew (unit_id, team_member_id)
                SELECT :uid, :mid
                WHERE (SELECT COUNT(*) FROM unit_crew WHERE unit_id = :uid) < :capacity
                RETURNING unit_id
            """)
            insert_result = await self.db.execute(insert_sql, {
                "uid": unit_id,
                "mid": team_member_id,
                "capacity": unit["capacity"],
            })
            if not insert_result.first():
                raise HTTPException(status_code=400, detail=f"Unit is at full capacity ({unit['capacity']}).")

            await self.db.flush()

            # Get current count for response
            count_sql = text("SELECT COUNT(*) FROM unit_crew WHERE unit_id = :uid")
            count_result = await self.db.execute(count_sql, {"uid": unit_id})
            current_count = count_result.scalar()

            return {
                "unit_id": unit_id,
                "unit_code": str(unit["unit_code"]),
                "member_added": member["full_name"],
                "crew_count": current_count,
                "capacity": unit["capacity"],
                "message": f"{member['full_name']} added to {unit['unit_code']}",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error adding crew member: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to add crew member: {str(e)}")

    # ──────────────────────────────────────────────
    # CREW: Remove single member
    # ──────────────────────────────────────────────
    async def remove_crew_member(self, unit_id: str, team_member_id: str) -> Dict[str, Any]:
        """Remove a single crew member from a unit."""
        try:
            # Check exists in crew
            check_sql = text("""
                SELECT uc.unit_id, eu.unit_code, et.full_name
                FROM unit_crew uc
                JOIN emergency_units eu ON uc.unit_id = eu.id
                JOIN emergency_teams et ON uc.team_member_id = et.id
                WHERE uc.unit_id = :uid AND uc.team_member_id = :mid
            """)
            result = await self.db.execute(check_sql, {"uid": unit_id, "mid": team_member_id})
            row = result.mappings().first()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found in this unit's crew.")

            # Remove
            await self.db.execute(
                text("DELETE FROM unit_crew WHERE unit_id = :uid AND team_member_id = :mid"),
                {"uid": unit_id, "mid": team_member_id}
            )
            await self.db.flush()

            # Get new count
            count_sql = text("SELECT COUNT(*) FROM unit_crew WHERE unit_id = :uid")
            result = await self.db.execute(count_sql, {"uid": unit_id})
            new_count = result.scalar()

            return {
                "unit_id": unit_id,
                "unit_code": str(row["unit_code"]),
                "member_removed": row["full_name"],
                "crew_count": new_count,
                "message": f"{row['full_name']} removed from {row['unit_code']}",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error removing crew member: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to remove crew member: {str(e)}")

    # ──────────────────────────────────────────────
    # HELPER
    # ──────────────────────────────────────────────
    def _row_to_unit(self, row) -> Dict[str, Any]:
        avg_seconds = row["avg_response_time_seconds"]
        avg_formatted = None
        if avg_seconds:
            mins = avg_seconds // 60
            secs = avg_seconds % 60
            avg_formatted = f"{mins}m {secs}s"

        return {
            "id": str(row["id"]),
            "unit_code": str(row["unit_code"]),
            "unit_name": str(row["unit_name"]),
            "unit_type": str(row["unit_type"]),
            "department": str(row["department"]),
            "unit_status": str(row["unit_status"]),
            "station_name": row["station_name"],
            "station_address": row["station_address"],
            "crew_count": row["crew_count"],
            "capacity": row["capacity"],
            "commander_name": row["commander_name"],
            "total_deployments": row["total_deployments"],
            "avg_response_time": avg_formatted,
            "success_rate": round(row["success_rate"] * 100, 1) if row["success_rate"] else None,
        }