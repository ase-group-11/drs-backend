# # File: app/services/deployment_service.py
# """
# Deployment Service - Manages unit deployments to disasters.

# Handles:
#   - Dispatch units to disasters
#   - Update deployment status (DISPATCHED → EN_ROUTE → ON_SCENE → IN_PROGRESS → COMPLETED)
#   - List active missions for a unit
#   - Get deployment details (mission progress)
#   - Request backup
# """

# import uuid
# import json
# import logging
# from datetime import datetime
# from typing import Dict, Any, List, Optional

# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import text
# from fastapi import HTTPException, status

# from app.services.rabbitmq_service import (
#     publish_disaster_reported,
#     publish_disaster_updated,
#     publish_disaster_resolved,
# )

# logger = logging.getLogger(__name__)

# # Valid status transitions
# VALID_TRANSITIONS = {
#     "DISPATCHED": ["EN_ROUTE", "CANCELLED"],
#     "EN_ROUTE": ["ON_SCENE", "CANCELLED"],
#     "ON_SCENE": ["IN_PROGRESS", "COMPLETED", "CANCELLED"],
#     "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
# }


# class DeploymentService:
#     def __init__(self, db: AsyncSession):
#         self.db = db

#     # ──────────────────────────────────────────────
#     # DISPATCH: Admin sends units to disaster
#     # ──────────────────────────────────────────────
#     async def dispatch_units(
#         self,
#         disaster_id: str,
#         unit_ids: List[str],
#         priority_level: str = "STANDARD",
#         special_instructions: str = None,
#     ) -> Dict[str, Any]:
#         """Dispatch one or more units to a disaster."""
#         logger.info(f"Dispatching {len(unit_ids)} units to disaster {disaster_id}")

#         try:
#             # Validate disaster
#             disaster_sql = text("""
#                 SELECT id, tracking_id, disaster_status, type, severity,
#                        ST_Y(location::geometry) as lat, ST_X(location::geometry) as lon,
#                        location_address
#                 FROM disasters
#                 WHERE id = :disaster_id AND deleted_at IS NULL
#             """)
#             result = await self.db.execute(disaster_sql, {"disaster_id": disaster_id})
#             disaster = result.mappings().first()

#             if not disaster:
#                 raise HTTPException(status_code=404, detail="Disaster not found.")

#             ds = str(disaster["disaster_status"])
#             if ds not in ("MONITORING", "ACTIVE"):
#                 raise HTTPException(
#                     status_code=400,
#                     detail=f"Can only dispatch to UNVERIFIED or ACTIVE disasters. Current: {ds}"
#                 )

#             now = datetime.utcnow()
#             dispatched_units = []

#             for uid in unit_ids:
#                 # Validate unit
#                 unit_sql = text("""
#                     SELECT id, unit_code, unit_name, unit_type, department, unit_status,
#                            station_name,
#                            ST_Y(station_location::geometry) as station_lat,
#                            ST_X(station_location::geometry) as station_lon
#                     FROM emergency_units
#                     WHERE id = :unit_id AND deleted_at IS NULL
#                 """)
#                 unit_result = await self.db.execute(unit_sql, {"unit_id": uid})
#                 unit = unit_result.mappings().first()

#                 if not unit:
#                     raise HTTPException(status_code=404, detail=f"Unit {uid} not found.")

#                 claim_unit_sql = text("""
#                     UPDATE emergency_units
#                     SET unit_status = CAST('DEPLOYED' AS unit_status),
#                         last_deployed_at = :now,
#                         total_deployments = total_deployments + 1,
#                         updated_at = :updated_at
#                     WHERE id = :unit_id
#                         AND unit_status = CAST('AVAILABLE' AS unit_status)
#                         AND deleted_at IS NULL
#                     RETURNING id
#                 """)

#                 claim_result = await self.db.execute(claim_unit_sql, {
#                     "unit_id" : uid, 
#                     "now" : now, 
#                     "updated_at" : now, 
#                 })

#                 if not claim_result.first():
#                     raise HTTPException(
#                         status_code = 409, 
#                         detail = f"Unit {unit['unit_code']} is no longer AVAILABLE, it may have been claimed by another request"
#                     )
                

#                 # Create deployment record
#                 deployment_id = str(uuid.uuid4())
#                 deploy_sql = text("""
#                     INSERT INTO deployments (
#                         id, disaster_id, unit_id,
#                         dispatched_at, assigned_at,
#                         priority_level, special_instructions,
#                         deployment_status,
#                         created_at, updated_at
#                     ) VALUES (
#                         :id, :disaster_id, :unit_id,
#                         :dispatched_at, :assigned_at,
#                         :priority_level, :special_instructions,
#                         'DISPATCHED',
#                         :created_at, :updated_at
#                     )
#                 """)
#                 await self.db.execute(deploy_sql, {
#                     "id": deployment_id,
#                     "disaster_id": disaster_id,
#                     "unit_id": uid,
#                     "dispatched_at": now,
#                     "assigned_at": now,
#                     "priority_level": priority_level.upper(),
#                     "special_instructions": special_instructions,
#                     "created_at": now,
#                     "updated_at": now,
#                 })

#                 # # Update unit status → DEPLOYED
#                 # update_unit_sql = text("""
#                 #     UPDATE emergency_units
#                 #     SET unit_status = CAST('DEPLOYED' AS unit_status),
#                 #         last_deployed_at = :now,
#                 #         total_deployments = total_deployments + 1,
#                 #         updated_at = :updated_at
#                 #     WHERE id = :unit_id
#                 # """)
#                 # await self.db.execute(update_unit_sql, {
#                 #     "unit_id": uid,
#                 #     "now": now,
#                 #     "updated_at": now,
#                 # })

#                 # Calculate ETA (rough: distance / 40 km/h)
#                 eta = None
#                 if unit["station_lat"] and disaster["lat"]:
#                     dist_sql = text("""
#                         SELECT ST_Distance(
#                             ST_SetSRID(ST_MakePoint(:lon1, :lat1), 4326)::geography,
#                             ST_SetSRID(ST_MakePoint(:lon2, :lat2), 4326)::geography
#                         ) / 1000 as distance_km
#                     """)
#                     dist_result = await self.db.execute(dist_sql, {
#                         "lat1": float(unit["station_lat"]),
#                         "lon1": float(unit["station_lon"]),
#                         "lat2": float(disaster["lat"]),
#                         "lon2": float(disaster["lon"]),
#                     })
#                     dist_row = dist_result.mappings().first()
#                     if dist_row and dist_row["distance_km"]:
#                         eta = round(float(dist_row["distance_km"]) / 40 * 60)

#                 dispatched_units.append({
#                     "deployment_id": deployment_id,
#                     "unit_id": str(unit["id"]),
#                     "unit_code": str(unit["unit_code"]),
#                     "unit_name": str(unit["unit_name"]),
#                     "unit_type": str(unit["unit_type"]),
#                     "department": str(unit["department"]),
#                     "station": unit["station_name"],
#                     "eta_minutes": eta,
#                 })

#             # Update disaster assigned_department (use first unit's department)
#             if dispatched_units:
#                 update_disaster_sql = text("""
#                     UPDATE disasters
#                     SET assigned_department = CAST(:dept AS department),
#                         updated_at = :updated_at
#                     WHERE id = :disaster_id
#                 """)
#                 await self.db.execute(update_disaster_sql, {
#                     "disaster_id": disaster_id,
#                     "dept": dispatched_units[0]["department"].upper(),
#                     "updated_at": now,
#                 })

#             await self.db.flush()

#             pending_event = {
#                 "topic" : "disaster.dispatched",
#                 "payload" : {
#                     "disaster_id" : disaster_id,
#                     "tracking_id" : str(disaster["tracking_id"]),
#                     "units_dispatched" : len(dispatched_units),
#                     "priority_level" : priority_level,
#                 },
#             }

#             # Publish RabbitMQ event
#             # from app.services.rabbitmq_service import get_rabbitmq_service
#             # service = get_rabbitmq_service()
#             # service.publish("disaster.dispatched", {
#             #     "disaster_id": disaster_id,
#             #     "tracking_id": str(disaster["tracking_id"]),
#             #     "units_dispatched": len(dispatched_units),
#             #     "priority_level": priority_level,
#             # })

#             return {
#                 "disaster_id": disaster_id,
#                 "tracking_id": str(disaster["tracking_id"]),
#                 "units_dispatched": dispatched_units,
#                 "priority_level": priority_level,
#                 "message": f"{len(dispatched_units)} unit(s) dispatched to {disaster['tracking_id']}",
#                 "pending_event" : pending_event,
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error dispatching units: {e}")
#             raise HTTPException(status_code=500, detail=f"Failed to dispatch: {str(e)}")

#     # ──────────────────────────────────────────────
#     # UPDATE STATUS: Responder updates deployment
#     # ──────────────────────────────────────────────
#     async def update_status(
#         self,
#         deployment_id: str,
#         new_status: str,
#         situation_report: str = None,
#         tags: List[str] = None,
#         minor_injuries: int = 0,
#         serious_injuries: int = 0,
#         additional_resources: List[str] = None,
#         location_verified: bool = False,
#         request_immediate_backup: bool = False,
#         assessment_notes: str = None,
#     ) -> Dict[str, Any]:
#         """Update deployment status — the main responder endpoint."""
#         logger.info(f"Updating deployment {deployment_id} to {new_status}")

#         try:
#             # Fetch deployment
#             dep_sql = text("""
#                 SELECT dep.id, dep.disaster_id, dep.unit_id, dep.deployment_status,
#                        dep.dispatched_at,
#                        dis.tracking_id, dis.disaster_status, dis.type as disaster_type
#                 FROM deployments dep
#                 JOIN disasters dis ON dep.disaster_id = dis.id
#                 WHERE dep.id = :deployment_id AND dep.deleted_at IS NULL
#             """)
#             result = await self.db.execute(dep_sql, {"deployment_id": deployment_id})
#             dep = result.mappings().first()

#             if not dep:
#                 raise HTTPException(status_code=404, detail="Deployment not found.")

#             current = dep["deployment_status"]
#             new_status = new_status.upper()

#             # Validate transition
#             valid_next = VALID_TRANSITIONS.get(current, [])
#             if new_status not in valid_next:
#                 raise HTTPException(
#                     status_code=400,
#                     detail=f"Invalid transition: {current} → {new_status}. Valid: {valid_next}"
#                 )

#             now = datetime.utcnow()
#             unit_id = str(dep["unit_id"])
#             disaster_id = str(dep["disaster_id"])

#             # Build dynamic UPDATE
#             set_clauses = [
#                 "deployment_status = :new_status",
#                 "updated_at = :updated_at",
#             ]
#             params = {
#                 "deployment_id": deployment_id,
#                 "new_status": new_status,
#                 "updated_at": now,
#             }

#             # Set timeline timestamp for this status
#             timestamp_map = {
#                 "EN_ROUTE": "en_route_at",
#                 "ON_SCENE": "on_scene_at",
#                 "IN_PROGRESS": "in_progress_at",
#                 "COMPLETED": "completed_at",
#             }
#             if new_status in timestamp_map:
#                 col = timestamp_map[new_status]
#                 set_clauses.append(f"{col} = :{col}")
#                 params[col] = now

#             if new_status == "ON_SCENE":
#                 set_clauses.append("arrived_at = :arrived_at")
#                 params["arrived_at"] = now

#             if situation_report:
#                 set_clauses.append("situation_report = :situation_report")
#                 params["situation_report"] = situation_report

#             if tags is not None:
#                 set_clauses.append("status_tags = :status_tags")
#                 params["status_tags"] = json.dumps(tags)

#             if minor_injuries > 0:
#                 set_clauses.append("minor_injuries = :minor_injuries")
#                 params["minor_injuries"] = minor_injuries

#             if serious_injuries > 0:
#                 set_clauses.append("serious_injuries = :serious_injuries")
#                 params["serious_injuries"] = serious_injuries

#             if additional_resources is not None:
#                 set_clauses.append("additional_resources = :additional_resources")
#                 params["additional_resources"] = json.dumps(additional_resources)

#             if location_verified:
#                 set_clauses.append("location_verified = :location_verified")
#                 params["location_verified"] = True

#             if request_immediate_backup:
#                 set_clauses.append("request_immediate_backup = :request_backup")
#                 params["request_backup"] = True

#             if assessment_notes:
#                 set_clauses.append("assessment_notes = :assessment_notes")
#                 params["assessment_notes"] = assessment_notes

#             update_sql = text(f"""
#                 UPDATE deployments
#                 SET {', '.join(set_clauses)}
#                 WHERE id = :deployment_id
#             """)
#             await self.db.execute(update_sql, params)

#             # Update unit status to match
#             unit_status_map = {
#                 "EN_ROUTE": "DEPLOYED",
#                 "ON_SCENE": "ON_SCENE",
#                 "IN_PROGRESS": "ON_SCENE",
#                 "COMPLETED": "RETURNING",
#                 "CANCELLED": "AVAILABLE",
#             }
#             if new_status in unit_status_map:
#                 unit_update_sql = text("""
#                     UPDATE emergency_units
#                     SET unit_status = CAST(:unit_status AS unit_status),
#                         updated_at = :updated_at
#                     WHERE id = :unit_id
#                 """)
#                 await self.db.execute(unit_update_sql, {
#                     "unit_id": unit_id,
#                     "unit_status": unit_status_map[new_status],
#                     "updated_at": now,
#                 })

#             # If ON_SCENE and disaster is UNVERIFIED → make it ACTIVE (verified by field team)
#             if new_status == "ON_SCENE" and str(dep["disaster_status"]) == "UNVERIFIED":
#                 activate_sql = text("""
#                     UPDATE disasters
#                     SET disaster_status = CAST('ACTIVE' AS disaster_status),
#                         response_time = :response_time,
#                         updated_at = :updated_at
#                     WHERE id = :disaster_id
#                 """)
#                 await self.db.execute(activate_sql, {
#                     "disaster_id": disaster_id,
#                     "response_time": now,
#                     "updated_at": now,
#                 })

#                 # Publish verified event
#                 from app.services.rabbitmq_service import get_rabbitmq_service
#                 svc = get_rabbitmq_service()
#                 svc.publish("disaster.verified", {
#                     "disaster_id": disaster_id,
#                     "tracking_id": str(dep["tracking_id"]),
#                     "verified_by_unit": unit_id,
#                     "situation_report": situation_report,
#                 })

#             # If COMPLETED → publish
#             if new_status == "COMPLETED":
#                 from app.services.rabbitmq_service import get_rabbitmq_service
#                 svc = get_rabbitmq_service()
#                 svc.publish("disaster.unit_completed", {
#                     "disaster_id": disaster_id,
#                     "tracking_id": str(dep["tracking_id"]),
#                     "unit_id": unit_id,
#                 })

#             # If backup requested → publish
#             if request_immediate_backup:
#                 from app.services.rabbitmq_service import get_rabbitmq_service
#                 svc = get_rabbitmq_service()
#                 svc.publish("disaster.backup_requested", {
#                     "disaster_id": disaster_id,
#                     "tracking_id": str(dep["tracking_id"]),
#                     "requesting_unit": unit_id,
#                     "resources_needed": additional_resources,
#                 })

#             await self.db.flush()

#             return {
#                 "deployment_id": deployment_id,
#                 "disaster_id": disaster_id,
#                 "tracking_id": str(dep["tracking_id"]),
#                 "previous_status": current,
#                 "new_status": new_status,
#                 "updated_at": now.isoformat(),
#                 "disaster_activated": new_status == "ON_SCENE" and str(dep["disaster_status"]) == "UNVERIFIED",
#                 "backup_requested": request_immediate_backup,
#                 "message": f"Deployment updated: {current} → {new_status}",
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error updating deployment: {e}")
#             raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

#     # ──────────────────────────────────────────────
#     # GET: Active missions for a unit
#     # ──────────────────────────────────────────────
#     async def get_active_missions(self, unit_id: str) -> List[Dict[str, Any]]:
#         """Get all active deployments for a unit (Active Missions page)."""
#         try:
#             sql = text("""
#                 SELECT
#                     dep.id as deployment_id, dep.deployment_status,
#                     dep.dispatched_at, dep.assigned_at, dep.en_route_at,
#                     dep.on_scene_at, dep.in_progress_at,
#                     dep.priority_level, dep.special_instructions,
#                     dep.situation_report,
#                     dis.id as disaster_id, dis.tracking_id,
#                     dis.type as disaster_type, dis.severity, dis.disaster_status,
#                     dis.description, dis.location_address, dis.people_affected,
#                     ST_Y(dis.location::geometry) as lat,
#                     ST_X(dis.location::geometry) as lon,
#                     ST_Distance(
#                         eu.station_location,
#                         dis.location
#                     ) / 1000 as distance_km
#                 FROM deployments dep
#                 JOIN disasters dis ON dep.disaster_id = dis.id
#                 JOIN emergency_units eu ON dep.unit_id = eu.id
#                 WHERE dep.unit_id = :unit_id
#                   AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')
#                   AND dep.deleted_at IS NULL
#                 ORDER BY
#                     CASE dep.priority_level
#                         WHEN 'CRITICAL' THEN 1
#                         WHEN 'HIGH' THEN 2
#                         WHEN 'STANDARD' THEN 3
#                         ELSE 4
#                     END,
#                     dep.dispatched_at ASC
#             """)

#             result = await self.db.execute(sql, {"unit_id": unit_id})
#             rows = result.mappings().all()

#             return [{
#                 "deployment_id": str(row["deployment_id"]),
#                 "deployment_status": row["deployment_status"],
#                 "priority_level": row["priority_level"],
#                 "disaster": {
#                     "id": str(row["disaster_id"]),
#                     "tracking_id": str(row["tracking_id"]),
#                     "type": str(row["disaster_type"]),
#                     "severity": str(row["severity"]),
#                     "status": str(row["disaster_status"]),
#                     "description": row["description"],
#                     "location_address": row["location_address"],
#                     "people_affected": row["people_affected"],
#                     "location": {"lat": float(row["lat"]), "lon": float(row["lon"])},
#                 },
#                 "distance_km": round(float(row["distance_km"]), 1) if row["distance_km"] else None,
#                 "eta_minutes": round(float(row["distance_km"]) / 40 * 60) if row["distance_km"] else None,
#                 "timeline": {
#                     "assigned_at": row["assigned_at"].isoformat() if row["assigned_at"] else None,
#                     "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
#                     "en_route_at": row["en_route_at"].isoformat() if row["en_route_at"] else None,
#                     "on_scene_at": row["on_scene_at"].isoformat() if row["on_scene_at"] else None,
#                     "in_progress_at": row["in_progress_at"].isoformat() if row["in_progress_at"] else None,
#                 },
#                 "special_instructions": row["special_instructions"],
#             } for row in rows]

#         except Exception as e:
#             logger.exception(f"Error fetching active missions: {e}")
#             raise HTTPException(status_code=500, detail=f"Failed to fetch missions: {str(e)}")

#     # ──────────────────────────────────────────────
#     # GET: Single deployment details (Mission Progress)
#     # ──────────────────────────────────────────────
#     async def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
#         """Get full deployment details including timeline and situation report."""
#         try:
#             sql = text("""
#                 SELECT
#                     dep.*,
#                     dis.tracking_id, dis.type as disaster_type, dis.severity,
#                     dis.disaster_status, dis.description as disaster_description,
#                     dis.location_address, dis.people_affected,
#                     ST_Y(dis.location::geometry) as lat,
#                     ST_X(dis.location::geometry) as lon,
#                     eu.unit_code, eu.unit_name, eu.unit_type as eu_type,
#                     eu.department as eu_department,
#                     (SELECT COUNT(*) FROM unit_crew uc WHERE uc.unit_id = eu.id) as crew_count
#                 FROM deployments dep
#                 JOIN disasters dis ON dep.disaster_id = dis.id
#                 JOIN emergency_units eu ON dep.unit_id = eu.id
#                 WHERE dep.id = :deployment_id AND dep.deleted_at IS NULL
#             """)

#             result = await self.db.execute(sql, {"deployment_id": deployment_id})
#             row = result.mappings().first()

#             if not row:
#                 raise HTTPException(status_code=404, detail="Deployment not found.")

#             return {
#                 "deployment_id": str(row["id"]),
#                 "deployment_status": row["deployment_status"],
#                 "priority_level": row["priority_level"],
#                 "special_instructions": row["special_instructions"],
#                 "disaster": {
#                     "id": str(row["disaster_id"]),
#                     "tracking_id": str(row["tracking_id"]),
#                     "type": str(row["disaster_type"]),
#                     "severity": str(row["severity"]),
#                     "status": str(row["disaster_status"]),
#                     "description": row["disaster_description"],
#                     "location_address": row["location_address"],
#                     "people_affected": row["people_affected"],
#                     "location": {"lat": float(row["lat"]), "lon": float(row["lon"])},
#                 },
#                 "unit": {
#                     "id": str(row["unit_id"]),
#                     "unit_code": str(row["unit_code"]),
#                     "unit_name": str(row["unit_name"]),
#                     "unit_type": str(row["eu_type"]),
#                     "department": str(row["eu_department"]),
#                     "crew_count": row["crew_count"],
#                 },
#                 "timeline": {
#                     "assigned_at": row["assigned_at"].isoformat() if row["assigned_at"] else None,
#                     "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
#                     "en_route_at": row["en_route_at"].isoformat() if row["en_route_at"] else None,
#                     "on_scene_at": row["on_scene_at"].isoformat() if row["on_scene_at"] else None,
#                     "in_progress_at": row["in_progress_at"].isoformat() if row["in_progress_at"] else None,
#                     "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
#                 },
#                 "situation_report": row["situation_report"],
#                 "status_tags": row["status_tags"] if row["status_tags"] else [],
#                 "casualties": {
#                     "minor_injuries": row["minor_injuries"] or 0,
#                     "serious_injuries": row["serious_injuries"] or 0,
#                 },
#                 "additional_resources": row["additional_resources"] if row["additional_resources"] else [],
#                 "location_verified": row["location_verified"],
#                 "request_immediate_backup": row["request_immediate_backup"],
#                 "assessment_notes": row["assessment_notes"],
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error fetching deployment: {e}")
#             raise HTTPException(status_code=500, detail=f"Failed to fetch deployment: {str(e)}")

#     # ──────────────────────────────────────────────
#     # GET: Completed missions for a unit
#     # ──────────────────────────────────────────────
#     async def get_completed_missions(self, unit_id: str, limit: int = 20) -> List[Dict[str, Any]]:
#         """Get completed deployments for a unit (Completed tab)."""
#         try:
#             sql = text("""
#                 SELECT
#                     dep.id as deployment_id, dep.deployment_status,
#                     dep.dispatched_at, dep.completed_at,
#                     dep.priority_level, dep.situation_report,
#                     dep.minor_injuries, dep.serious_injuries,
#                     dis.id as disaster_id, dis.tracking_id,
#                     dis.type as disaster_type, dis.severity, dis.disaster_status,
#                     dis.location_address
#                 FROM deployments dep
#                 JOIN disasters dis ON dep.disaster_id = dis.id
#                 WHERE dep.unit_id = :unit_id
#                   AND dep.deployment_status IN ('COMPLETED', 'CANCELLED')
#                   AND dep.deleted_at IS NULL
#                 ORDER BY dep.completed_at DESC
#                 LIMIT :limit
#             """)

#             result = await self.db.execute(sql, {"unit_id": unit_id, "limit": limit})
#             rows = result.mappings().all()

#             return [{
#                 "deployment_id": str(row["deployment_id"]),
#                 "deployment_status": row["deployment_status"],
#                 "priority_level": row["priority_level"],
#                 "disaster": {
#                     "id": str(row["disaster_id"]),
#                     "tracking_id": str(row["tracking_id"]),
#                     "type": str(row["disaster_type"]),
#                     "severity": str(row["severity"]),
#                     "location_address": row["location_address"],
#                 },
#                 "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
#                 "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
#                 "situation_report": row["situation_report"],
#                 "casualties": {
#                     "minor_injuries": row["minor_injuries"] or 0,
#                     "serious_injuries": row["serious_injuries"] or 0,
#                 },
#             } for row in rows]

#         except Exception as e:
#             logger.exception(f"Error fetching completed missions: {e}")
#             raise HTTPException(status_code=500, detail=f"Failed to fetch completed missions: {str(e)}")



















# File: app/services/deployment_service.py
"""
Deployment Service - Manages unit deployments to disasters.

Handles:
  - Dispatch units to disasters
  - Update deployment status (DISPATCHED → EN_ROUTE → ON_SCENE → IN_PROGRESS → COMPLETED)
  - List active missions for a unit
  - Get deployment details (mission progress)
  - Request backup
"""

import uuid
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException, status

from app.services.rabbitmq_service import (
    publish_disaster_reported,
    publish_disaster_updated,
    publish_disaster_resolved,
)

logger = logging.getLogger(__name__)

# Valid status transitions
VALID_TRANSITIONS = {
    "DISPATCHED": ["EN_ROUTE", "CANCELLED"],
    "EN_ROUTE": ["ON_SCENE", "CANCELLED"],
    "ON_SCENE": ["IN_PROGRESS", "COMPLETED", "CANCELLED"],
    "IN_PROGRESS": ["COMPLETED", "CANCELLED"],
}


class DeploymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # DISPATCH: Admin sends units to disaster
    # ──────────────────────────────────────────────
    async def dispatch_units(
        self,
        disaster_id: str,
        unit_ids: List[str],
        priority_level: str = "STANDARD",
        special_instructions: str = None,
    ) -> Dict[str, Any]:
        """Dispatch one or more units to a disaster."""
        logger.info(f"Dispatching {len(unit_ids)} units to disaster {disaster_id}")

        try:
            # Validate disaster
            disaster_sql = text("""
                SELECT id, tracking_id, disaster_status, type, severity,
                       ST_Y(location::geometry) as lat, ST_X(location::geometry) as lon,
                       location_address
                FROM disasters
                WHERE id = :disaster_id AND deleted_at IS NULL
            """)
            result = await self.db.execute(disaster_sql, {"disaster_id": disaster_id})
            disaster = result.mappings().first()

            if not disaster:
                raise HTTPException(status_code=404, detail="Disaster not found.")

            ds = str(disaster["disaster_status"])
            if ds not in ("MONITORING", "ACTIVE"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Can only dispatch to ACTIVE disasters. Current: {ds}"
                )

            now = datetime.utcnow()
            dispatched_units = []

            for uid in unit_ids:
                # Validate unit
                unit_sql = text("""
                    SELECT id, unit_code, unit_name, unit_type, department, unit_status,
                           station_name,
                           ST_Y(station_location::geometry) as station_lat,
                           ST_X(station_location::geometry) as station_lon
                    FROM emergency_units
                    WHERE id = :unit_id AND deleted_at IS NULL
                """)
                unit_result = await self.db.execute(unit_sql, {"unit_id": uid})
                unit = unit_result.mappings().first()

                if not unit:
                    raise HTTPException(status_code=404, detail=f"Unit {uid} not found.")

                # FIX #1: Atomic conditional UPDATE replaces the read-then-write pattern.
                # The WHERE clause on unit_status = AVAILABLE means only one concurrent
                # request can succeed — the rest get 0 rows back and receive a 409.
                claim_unit_sql = text("""
                    UPDATE emergency_units
                    SET unit_status = CAST('DEPLOYED' AS unit_status),
                        last_deployed_at = :now,
                        total_deployments = total_deployments + 1,
                        updated_at = :updated_at
                    WHERE id = :unit_id
                      AND unit_status = CAST('AVAILABLE' AS unit_status)
                      AND deleted_at IS NULL
                    RETURNING id
                """)
                claim_result = await self.db.execute(claim_unit_sql, {
                    "unit_id": uid,
                    "now": now,
                    "updated_at": now,
                })
                if not claim_result.first():
                    raise HTTPException(
                        status_code=409,
                        detail=f"Unit {unit['unit_code']} is no longer AVAILABLE — it may have just been claimed by another request."
                    )

                # Create deployment record (unit is now atomically locked to DEPLOYED)
                deployment_id = str(uuid.uuid4())
                deploy_sql = text("""
                    INSERT INTO deployments (
                        id, disaster_id, unit_id,
                        dispatched_at, assigned_at,
                        priority_level, special_instructions,
                        deployment_status,
                        created_at, updated_at
                    ) VALUES (
                        :id, :disaster_id, :unit_id,
                        :dispatched_at, :assigned_at,
                        :priority_level, :special_instructions,
                        'DISPATCHED',
                        :created_at, :updated_at
                    )
                """)
                await self.db.execute(deploy_sql, {
                    "id": deployment_id,
                    "disaster_id": disaster_id,
                    "unit_id": uid,
                    "dispatched_at": now,
                    "assigned_at": now,
                    "priority_level": priority_level.upper(),
                    "special_instructions": special_instructions,
                    "created_at": now,
                    "updated_at": now,
                })

                # Calculate ETA (rough: distance / 40 km/h)
                eta = None
                if unit["station_lat"] and disaster["lat"]:
                    dist_sql = text("""
                        SELECT ST_Distance(
                            ST_SetSRID(ST_MakePoint(:lon1, :lat1), 4326)::geography,
                            ST_SetSRID(ST_MakePoint(:lon2, :lat2), 4326)::geography
                        ) / 1000 as distance_km
                    """)
                    dist_result = await self.db.execute(dist_sql, {
                        "lat1": float(unit["station_lat"]),
                        "lon1": float(unit["station_lon"]),
                        "lat2": float(disaster["lat"]),
                        "lon2": float(disaster["lon"]),
                    })
                    dist_row = dist_result.mappings().first()
                    if dist_row and dist_row["distance_km"]:
                        eta = round(float(dist_row["distance_km"]) / 40 * 60)

                dispatched_units.append({
                    "deployment_id": deployment_id,
                    "unit_id": str(unit["id"]),
                    "unit_code": str(unit["unit_code"]),
                    "unit_name": str(unit["unit_name"]),
                    "unit_type": str(unit["unit_type"]),
                    "department": str(unit["department"]),
                    "station": unit["station_name"],
                    "eta_minutes": eta,
                })

            # Update disaster assigned_department (use first unit's department)
            if dispatched_units:
                update_disaster_sql = text("""
                    UPDATE disasters
                    SET assigned_department = CAST(:dept AS department),
                        updated_at = :updated_at
                    WHERE id = :disaster_id
                """)
                await self.db.execute(update_disaster_sql, {
                    "disaster_id": disaster_id,
                    "dept": dispatched_units[0]["department"].upper(),
                    "updated_at": now,
                })

            await self.db.flush()
            
            pending_event = {
                "topic" : "disaster.dispatched",
                "payload" : {
                    "disaster_id" : disaster_id,
                    "tracking_id" : str(disaster["tracking_id"]),
                    "units_dispatched" : len(dispatched_units),
                    "priority_level" : priority_level,
                },
            }

            # Publish RabbitMQ event
            from app.services.rabbitmq_service import get_rabbitmq_service
            service = get_rabbitmq_service()
            # service.publish("disaster.dispatched", {
            #     "disaster_id": disaster_id,
            #     "tracking_id": str(disaster["tracking_id"]),
            #     "units_dispatched": len(dispatched_units),
            #     "priority_level": priority_level,
            # })
            
            service.publish("disaster.dispatched", {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "units_dispatched": len(dispatched_units),
                "priority_level": priority_level,
                "location": {
                    "lat": float(disaster["lat"]) if disaster["lat"] else None,
                    "lon": float(disaster["lon"]) if disaster["lon"] else None,
                },
                "location_address": disaster["location_address"],
            })

            return {
                "disaster_id": disaster_id,
                "tracking_id": str(disaster["tracking_id"]),
                "units_dispatched": dispatched_units,
                "priority_level": priority_level,
                "message": f"{len(dispatched_units)} unit(s) dispatched to {disaster['tracking_id']}",
                "_pending_event": pending_event,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error dispatching units: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to dispatch: {str(e)}")

    # ──────────────────────────────────────────────
    # UPDATE STATUS: Responder updates deployment
    # ──────────────────────────────────────────────
    async def update_status(
        self,
        deployment_id: str,
        new_status: str,
        situation_report: str = None,
        tags: List[str] = None,
        minor_injuries: int = 0,
        serious_injuries: int = 0,
        additional_resources: List[str] = None,
        location_verified: bool = False,
        request_immediate_backup: bool = False,
        assessment_notes: str = None,
    ) -> Dict[str, Any]:
        """Update deployment status — the main responder endpoint."""
        logger.info(f"Updating deployment {deployment_id} to {new_status}")

        try:
            # Fetch deployment
            # dep_sql = text("""
            #     SELECT dep.id, dep.disaster_id, dep.unit_id, dep.deployment_status,
            #            dep.dispatched_at,
            #            dis.tracking_id, dis.disaster_status, dis.type as disaster_type
            #     FROM deployments dep
            #     JOIN disasters dis ON dep.disaster_id = dis.id
            #     WHERE dep.id = :deployment_id AND dep.deleted_at IS NULL
            # """)
            
            dep_sql = text("""
                SELECT dep.id, dep.disaster_id, dep.unit_id, dep.deployment_status,
                    dep.dispatched_at,
                    dis.tracking_id, dis.disaster_status, dis.type as disaster_type,
                    dis.location_address,
                    ST_Y(dis.location::geometry) as lat,
                    ST_X(dis.location::geometry) as lon
                FROM deployments dep
                JOIN disasters dis ON dep.disaster_id = dis.id
                WHERE dep.id = :deployment_id AND dep.deleted_at IS NULL
            """)
            
            result = await self.db.execute(dep_sql, {"deployment_id": deployment_id})
            dep = result.mappings().first()

            if not dep:
                raise HTTPException(status_code=404, detail="Deployment not found.")

            current = dep["deployment_status"]
            new_status = new_status.upper()

            # Validate transition
            valid_next = VALID_TRANSITIONS.get(current, [])
            if new_status not in valid_next:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition: {current} → {new_status}. Valid: {valid_next}"
                )

            now = datetime.utcnow()
            unit_id = str(dep["unit_id"])
            disaster_id = str(dep["disaster_id"])

            # Build dynamic UPDATE
            set_clauses = [
                "deployment_status = :new_status",
                "updated_at = :updated_at",
            ]
            params = {
                "deployment_id": deployment_id,
                "new_status": new_status,
                "updated_at": now,
            }

            # Set timeline timestamp for this status
            timestamp_map = {
                "EN_ROUTE": "en_route_at",
                "ON_SCENE": "on_scene_at",
                "IN_PROGRESS": "in_progress_at",
                "COMPLETED": "completed_at",
            }
            if new_status in timestamp_map:
                col = timestamp_map[new_status]
                set_clauses.append(f"{col} = :{col}")
                params[col] = now

            if new_status == "ON_SCENE":
                set_clauses.append("arrived_at = :arrived_at")
                params["arrived_at"] = now

            if situation_report:
                set_clauses.append("situation_report = :situation_report")
                params["situation_report"] = situation_report

            if tags is not None:
                set_clauses.append("status_tags = :status_tags")
                params["status_tags"] = json.dumps(tags)

            if minor_injuries > 0:
                set_clauses.append("minor_injuries = :minor_injuries")
                params["minor_injuries"] = minor_injuries

            if serious_injuries > 0:
                set_clauses.append("serious_injuries = :serious_injuries")
                params["serious_injuries"] = serious_injuries

            if additional_resources is not None:
                set_clauses.append("additional_resources = :additional_resources")
                params["additional_resources"] = json.dumps(additional_resources)

            if location_verified:
                set_clauses.append("location_verified = :location_verified")
                params["location_verified"] = True

            if request_immediate_backup:
                set_clauses.append("request_immediate_backup = :request_backup")
                params["request_backup"] = True

            if assessment_notes:
                set_clauses.append("assessment_notes = :assessment_notes")
                params["assessment_notes"] = assessment_notes

            # FIX #2: Add current_status to the WHERE clause so the UPDATE itself
            # validates the transition atomically. If another request already advanced
            # the status, rowcount = 0 and we raise 409 instead of silently overwriting.
            params["current_status"] = current
            update_sql = text(f"""
                UPDATE deployments
                SET {', '.join(set_clauses)}
                WHERE id = :deployment_id
                  AND deployment_status = :current_status
                RETURNING id
            """)
            update_result = await self.db.execute(update_sql, params)
            if not update_result.first():
                raise HTTPException(
                    status_code=409,
                    detail=f"Deployment status was changed by a concurrent request. Fetch the latest status and retry."
                )

            # Update unit status to match
            unit_status_map = {
                "EN_ROUTE": "DEPLOYED",
                "ON_SCENE": "ON_SCENE",
                "IN_PROGRESS": "ON_SCENE",
                "COMPLETED": "RETURNING",
                "CANCELLED": "AVAILABLE",
            }
            if new_status in unit_status_map:
                unit_update_sql = text("""
                    UPDATE emergency_units
                    SET unit_status = CAST(:unit_status AS unit_status),
                        updated_at = :updated_at
                    WHERE id = :unit_id
                """)
                await self.db.execute(unit_update_sql, {
                    "unit_id": unit_id,
                    "unit_status": unit_status_map[new_status],
                    "updated_at": now,
                })


            # If ON_SCENE and disaster is UNVERIFIED → make it ACTIVE (verified by field team)
            if new_status == "ON_SCENE" and str(dep["disaster_status"]) == "UNVERIFIED":
                activate_sql = text("""
                    UPDATE disasters
                    SET disaster_status = CAST('ACTIVE' AS disaster_status),
                        response_time = :response_time,
                        updated_at = :updated_at
                    WHERE id = :disaster_id
                """)
                await self.db.execute(activate_sql, {
                    "disaster_id": disaster_id,
                    "response_time": now,
                    "updated_at": now,
                })

                # Publish verified event
                from app.services.rabbitmq_service import get_rabbitmq_service
                svc = get_rabbitmq_service()
                # svc.publish("disaster.verified", {
                #     "disaster_id": disaster_id,
                #     "tracking_id": str(dep["tracking_id"]),
                #     "verified_by_unit": unit_id,
                #     "situation_report": situation_report,
                # })
                
                svc.publish("disaster.verified", {
                    "disaster_id": disaster_id,
                    "tracking_id": str(dep["tracking_id"]),
                    "verified_by_unit": unit_id,
                    "situation_report": situation_report,
                    "location": {
                        "lat": float(dep["lat"]) if dep["lat"] else None,
                        "lon": float(dep["lon"]) if dep["lon"] else None,
                    },
                    "location_address": dep["location_address"],
                })

            # If COMPLETED → publish
            if new_status == "COMPLETED":
                try:
                    from app.services.rabbitmq_service import get_rabbitmq_service
                    svc = get_rabbitmq_service()
                    svc.publish("disaster.unit_completed", {
                        "disaster_id": disaster_id,
                        "tracking_id": str(dep["tracking_id"]),
                        "unit_id": unit_id,
                    })
                except Exception:
                    pass

            # If backup requested → publish
            if request_immediate_backup:
                from app.services.rabbitmq_service import get_rabbitmq_service
                svc = get_rabbitmq_service()
                # svc.publish("disaster.backup_requested", {
                #     "disaster_id": disaster_id,
                #     "tracking_id": str(dep["tracking_id"]),
                #     "requesting_unit": unit_id,
                #     "resources_needed": additional_resources,
                # })
                
                svc.publish("disaster.backup_requested", {
                    "disaster_id": disaster_id,
                    "tracking_id": str(dep["tracking_id"]),
                    "requesting_unit": unit_id,
                    "resources_needed": additional_resources,
                    "location": {
                        "lat": float(dep["lat"]) if dep["lat"] else None,
                        "lon": float(dep["lon"]) if dep["lon"] else None,
                    },
                    "location_address": dep["location_address"],
                })
                
                

            await self.db.flush()


            return {
                "deployment_id": deployment_id,
                "disaster_id": disaster_id,
                "tracking_id": str(dep["tracking_id"]),
                "previous_status": current,
                "new_status": new_status,
                "updated_at": now.isoformat(),

                "backup_requested": request_immediate_backup,
                "message": f"Deployment updated: {current} → {new_status}",
                "_pending_events": pending_events,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error updating deployment: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to update: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Active missions for a unit
    # ──────────────────────────────────────────────
    async def get_active_missions(self, unit_id: str) -> List[Dict[str, Any]]:
        """Get all active deployments for a unit (Active Missions page)."""
        try:
            sql = text("""
                SELECT
                    dep.id as deployment_id, dep.deployment_status,
                    dep.dispatched_at, dep.assigned_at, dep.en_route_at,
                    dep.on_scene_at, dep.in_progress_at,
                    dep.priority_level, dep.special_instructions,
                    dep.situation_report,
                    dis.id as disaster_id, dis.tracking_id,
                    dis.type as disaster_type, dis.severity, dis.disaster_status,
                    dis.description, dis.location_address, dis.people_affected,
                    ST_Y(dis.location::geometry) as lat,
                    ST_X(dis.location::geometry) as lon,
                    ST_Distance(
                        eu.station_location,
                        dis.location
                    ) / 1000 as distance_km
                FROM deployments dep
                JOIN disasters dis ON dep.disaster_id = dis.id
                JOIN emergency_units eu ON dep.unit_id = eu.id
                WHERE dep.unit_id = :unit_id
                  AND dep.deployment_status NOT IN ('COMPLETED', 'CANCELLED')
                  AND dep.deleted_at IS NULL
                ORDER BY
                    CASE dep.priority_level
                        WHEN 'CRITICAL' THEN 1
                        WHEN 'HIGH' THEN 2
                        WHEN 'STANDARD' THEN 3
                        ELSE 4
                    END,
                    dep.dispatched_at ASC
            """)

            result = await self.db.execute(sql, {"unit_id": unit_id})
            rows = result.mappings().all()

            return [{
                "deployment_id": str(row["deployment_id"]),
                "deployment_status": row["deployment_status"],
                "priority_level": row["priority_level"],
                "disaster": {
                    "id": str(row["disaster_id"]),
                    "tracking_id": str(row["tracking_id"]),
                    "type": str(row["disaster_type"]),
                    "severity": str(row["severity"]),
                    "status": str(row["disaster_status"]),
                    "description": row["description"],
                    "location_address": row["location_address"],
                    "people_affected": row["people_affected"],
                    "location": {"lat": float(row["lat"]), "lon": float(row["lon"])},
                },
                "distance_km": round(float(row["distance_km"]), 1) if row["distance_km"] else None,
                "eta_minutes": round(float(row["distance_km"]) / 40 * 60) if row["distance_km"] else None,
                "timeline": {
                    "assigned_at": row["assigned_at"].isoformat() if row["assigned_at"] else None,
                    "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
                    "en_route_at": row["en_route_at"].isoformat() if row["en_route_at"] else None,
                    "on_scene_at": row["on_scene_at"].isoformat() if row["on_scene_at"] else None,
                    "in_progress_at": row["in_progress_at"].isoformat() if row["in_progress_at"] else None,
                },
                "special_instructions": row["special_instructions"],
            } for row in rows]

        except Exception as e:
            logger.exception(f"Error fetching active missions: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch missions: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Single deployment details (Mission Progress)
    # ──────────────────────────────────────────────
    async def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
        """Get full deployment details including timeline and situation report."""
        try:
            sql = text("""
                SELECT
                    dep.*,
                    dis.tracking_id, dis.type as disaster_type, dis.severity,
                    dis.disaster_status, dis.description as disaster_description,
                    dis.location_address, dis.people_affected,
                    ST_Y(dis.location::geometry) as lat,
                    ST_X(dis.location::geometry) as lon,
                    eu.unit_code, eu.unit_name, eu.unit_type as eu_type,
                    eu.department as eu_department,
                    (SELECT COUNT(*) FROM unit_crew uc WHERE uc.unit_id = eu.id) as crew_count
                FROM deployments dep
                JOIN disasters dis ON dep.disaster_id = dis.id
                JOIN emergency_units eu ON dep.unit_id = eu.id
                WHERE dep.id = :deployment_id AND dep.deleted_at IS NULL
            """)

            result = await self.db.execute(sql, {"deployment_id": deployment_id})
            row = result.mappings().first()

            if not row:
                raise HTTPException(status_code=404, detail="Deployment not found.")

            return {
                "deployment_id": str(row["id"]),
                "deployment_status": row["deployment_status"],
                "priority_level": row["priority_level"],
                "special_instructions": row["special_instructions"],
                "disaster": {
                    "id": str(row["disaster_id"]),
                    "tracking_id": str(row["tracking_id"]),
                    "type": str(row["disaster_type"]),
                    "severity": str(row["severity"]),
                    "status": str(row["disaster_status"]),
                    "description": row["disaster_description"],
                    "location_address": row["location_address"],
                    "people_affected": row["people_affected"],
                    "location": {"lat": float(row["lat"]), "lon": float(row["lon"])},
                },
                "unit": {
                    "id": str(row["unit_id"]),
                    "unit_code": str(row["unit_code"]),
                    "unit_name": str(row["unit_name"]),
                    "unit_type": str(row["eu_type"]),
                    "department": str(row["eu_department"]),
                    "crew_count": row["crew_count"],
                },
                "timeline": {
                    "assigned_at": row["assigned_at"].isoformat() if row["assigned_at"] else None,
                    "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
                    "en_route_at": row["en_route_at"].isoformat() if row["en_route_at"] else None,
                    "on_scene_at": row["on_scene_at"].isoformat() if row["on_scene_at"] else None,
                    "in_progress_at": row["in_progress_at"].isoformat() if row["in_progress_at"] else None,
                    "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                },
                "situation_report": row["situation_report"],
                "status_tags": row["status_tags"] if row["status_tags"] else [],
                "casualties": {
                    "minor_injuries": row["minor_injuries"] or 0,
                    "serious_injuries": row["serious_injuries"] or 0,
                },
                "additional_resources": row["additional_resources"] if row["additional_resources"] else [],
                "location_verified": row["location_verified"],
                "request_immediate_backup": row["request_immediate_backup"],
                "assessment_notes": row["assessment_notes"],
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error fetching deployment: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch deployment: {str(e)}")

    # ──────────────────────────────────────────────
    # GET: Completed missions for a unit
    # ──────────────────────────────────────────────
    async def get_completed_missions(self, unit_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get completed deployments for a unit (Completed tab)."""
        try:
            sql = text("""
                SELECT
                    dep.id as deployment_id, dep.deployment_status,
                    dep.dispatched_at, dep.completed_at,
                    dep.priority_level, dep.situation_report,
                    dep.minor_injuries, dep.serious_injuries,
                    dis.id as disaster_id, dis.tracking_id,
                    dis.type as disaster_type, dis.severity, dis.disaster_status,
                    dis.location_address
                FROM deployments dep
                JOIN disasters dis ON dep.disaster_id = dis.id
                WHERE dep.unit_id = :unit_id
                  AND dep.deployment_status IN ('COMPLETED', 'CANCELLED')
                  AND dep.deleted_at IS NULL
                ORDER BY dep.completed_at DESC
                LIMIT :limit
            """)

            result = await self.db.execute(sql, {"unit_id": unit_id, "limit": limit})
            rows = result.mappings().all()

            return [{
                "deployment_id": str(row["deployment_id"]),
                "deployment_status": row["deployment_status"],
                "priority_level": row["priority_level"],
                "disaster": {
                    "id": str(row["disaster_id"]),
                    "tracking_id": str(row["tracking_id"]),
                    "type": str(row["disaster_type"]),
                    "severity": str(row["severity"]),
                    "location_address": row["location_address"],
                },
                "dispatched_at": row["dispatched_at"].isoformat() if row["dispatched_at"] else None,
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                "situation_report": row["situation_report"],
                "casualties": {
                    "minor_injuries": row["minor_injuries"] or 0,
                    "serious_injuries": row["serious_injuries"] or 0,
                },
            } for row in rows]

        except Exception as e:
            logger.exception(f"Error fetching completed missions: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch completed missions: {str(e)}")