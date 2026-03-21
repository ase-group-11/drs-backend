# # File: app/services/disaster_report_service.py
# """
# Disaster Report Service - Business logic for report submission and review.

# Uses RAW SQL for database operations to avoid ORM relationship issues.
# All enum values are UPPERCASE to match PostgreSQL enum definitions.
# Uses datetime.utcnow() for timestamps (no timezone info).
# Uses separate parameters for each timestamp to avoid type conflicts.

# Workflow:
#   STEP 2: Save disaster report    → disaster_reports (status=PENDING)
#   STEP 3: Save photos             → disaster_photos  (all share same reference_id)
#   STEP 4: Admin review            → if approved → create entry in disasters table
# """

# import uuid
# import logging
# from datetime import datetime
# from typing import Dict, Any, List

# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import text
# from fastapi import HTTPException, status

# from app.schemas.disaster_report import (
#     DisasterReportCreateRequest,
#     AdminReviewRequest,
# )
# from app.services.rabbitmq_service import publish_disaster_reported

# logger = logging.getLogger(__name__)


# class DisasterReportService:
#     """
#     Service layer for disaster report operations.
#     All DB operations use raw SQL to avoid ORM relationship loading issues.
#     """

#     def __init__(self, db: AsyncSession):
#         self.db = db

#     # ──────────────────────────────────────────────
#     # STEP 2 + 3: Create report + save photos
#     # ──────────────────────────────────────────────
#     async def create_report(self, data: DisasterReportCreateRequest) -> Dict[str, Any]:
#         """
#         Create a new disaster report with photos using raw SQL.

#         - Inserts 1 row in disaster_reports (status='PENDING', disaster_id=NULL)
#         - Inserts N rows in disaster_photos (all share same reference_id)
#         """
#         logger.info(f"Creating disaster report for user {data.user_id}")

#         try:
#             report_id = str(uuid.uuid4())
#             now = datetime.utcnow()

#             insert_report_sql = text("""
#                 INSERT INTO disaster_reports (
#                     id, created_at, updated_at,
#                     user_id, location_address, disaster_type, severity,
#                     description, location, people_affected,
#                     multiple_casualties, structural_damage, road_blocked,
#                     report_status, disaster_id, reviewed_by_id, reviewed_at, rejection_reason
#                 ) VALUES (
#                     :id, :created_at, :updated_at,
#                     :user_id, :location_address,
#                     CAST(:disaster_type AS disaster_type),
#                     CAST(:severity AS disaster_severity),
#                     :description,
#                     ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
#                     :people_affected,
#                     :multiple_casualties, :structural_damage, :road_blocked,
#                     CAST(:report_status AS disaster_report_status),
#                     NULL, NULL, NULL, NULL
#                 )
#             """)

#             await self.db.execute(insert_report_sql, {
#                 "id": report_id,
#                 "created_at": now,
#                 "updated_at": now,
#                 "user_id": data.user_id,
#                 "location_address": data.location_address,
#                 "disaster_type": data.disaster_type.upper(),
#                 "severity": data.severity.upper(),
#                 "description": data.description,
#                 "latitude": data.latitude,
#                 "longitude": data.longitude,
#                 "people_affected": data.people_affected or 0,
#                 "multiple_casualties": data.multiple_casualties or False,
#                 "structural_damage": data.structural_damage or False,
#                 "road_blocked": data.road_blocked or False,
#                 "report_status": "PENDING",
#             })

#             logger.info(f"Created disaster report: {report_id}")

#             # ── STEP 3: Save photos ──
#             photo_count = 0
#             if data.photos:
#                 reference_id = data.reference_id or str(uuid.uuid4())

#                 insert_photo_sql = text("""
#                     INSERT INTO disaster_photos (
#                         id, created_at, updated_at,
#                         image_url, caption, file_size, mime_type,
#                         disaster_report_id, reference_id
#                     ) VALUES (
#                         :id, :created_at, :updated_at,
#                         :image_url, :caption, :file_size, :mime_type,
#                         :disaster_report_id, :reference_id
#                     )
#                 """)

#                 for photo_data in data.photos:
#                     await self.db.execute(insert_photo_sql, {
#                         "id": str(uuid.uuid4()),
#                         "created_at": now,
#                         "updated_at": now,
#                         "image_url": photo_data.image_url,
#                         "caption": photo_data.caption,
#                         "file_size": photo_data.file_size,
#                         "mime_type": photo_data.mime_type,
#                         "disaster_report_id": report_id,
#                         "reference_id": reference_id,
#                     })
#                     photo_count += 1

#                 logger.info(f"Saved {photo_count} photos with reference_id={reference_id}")

#             await self.db.flush()

#             return await self._get_report_dict(report_id)

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error creating disaster report: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to create disaster report: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # COMBINED: Upload photos + Create report (All-in-one)
#     # ──────────────────────────────────────────────
#     async def submit_report(
#         self,
#         user_id: str,
#         location_address: str,
#         disaster_type: str,
#         severity: str,
#         description: str,
#         latitude: float,
#         longitude: float,
#         people_affected: int,
#         multiple_casualties: bool,
#         structural_damage: bool,
#         road_blocked: bool,
#         uploaded_files: List[Dict[str, Any]],
#     ) -> Dict[str, Any]:
#         """
#         All-in-one: Creates report + saves photos in one transaction.

#         uploaded_files = list of dicts from blob_service.upload_multiple_files():
#           [{"image_url": "...", "file_size": 123, "mime_type": "image/jpeg", "original_filename": "..."}]
#         """
#         logger.info(f"Submitting disaster report for user {user_id} with {len(uploaded_files)} photos")

#         try:
#             report_id = str(uuid.uuid4())
#             reference_id = str(uuid.uuid4())
#             now = datetime.utcnow()

#             insert_report_sql = text("""
#                 INSERT INTO disaster_reports (
#                     id, created_at, updated_at,
#                     user_id, location_address, disaster_type, severity,
#                     description, location, people_affected,
#                     multiple_casualties, structural_damage, road_blocked,
#                     report_status, disaster_id, reviewed_by_id, reviewed_at, rejection_reason
#                 ) VALUES (
#                     :id, :created_at, :updated_at,
#                     :user_id, :location_address,
#                     CAST(:disaster_type AS disaster_type),
#                     CAST(:severity AS disaster_severity),
#                     :description,
#                     ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
#                     :people_affected,
#                     :multiple_casualties, :structural_damage, :road_blocked,
#                     CAST(:report_status AS disaster_report_status),
#                     NULL, NULL, NULL, NULL
#                 )
#             """)

#             await self.db.execute(insert_report_sql, {
#                 "id": report_id,
#                 "created_at": now,
#                 "updated_at": now,
#                 "user_id": user_id,
#                 "location_address": location_address,
#                 "disaster_type": disaster_type.upper(),
#                 "severity": severity.upper(),
#                 "description": description,
#                 "latitude": latitude,
#                 "longitude": longitude,
#                 "people_affected": people_affected,
#                 "multiple_casualties": multiple_casualties,
#                 "structural_damage": structural_damage,
#                 "road_blocked": road_blocked,
#                 "report_status": "PENDING",
#             })

#             logger.info(f"Created disaster report: {report_id}")

#             # ── Save photos ──
#             photo_count = 0
#             if uploaded_files:
#                 insert_photo_sql = text("""
#                     INSERT INTO disaster_photos (
#                         id, created_at, updated_at,
#                         image_url, caption, file_size, mime_type,
#                         disaster_report_id, reference_id
#                     ) VALUES (
#                         :id, :created_at, :updated_at,
#                         :image_url, :caption, :file_size, :mime_type,
#                         :disaster_report_id, :reference_id
#                     )
#                 """)

#                 for file_data in uploaded_files:
#                     await self.db.execute(insert_photo_sql, {
#                         "id": str(uuid.uuid4()),
#                         "created_at": now,
#                         "updated_at": now,
#                         "image_url": file_data["image_url"],
#                         "caption": file_data.get("original_filename", ""),
#                         "file_size": file_data.get("file_size", 0),
#                         "mime_type": file_data.get("mime_type", ""),
#                         "disaster_report_id": report_id,
#                         "reference_id": reference_id,
#                     })
#                     photo_count += 1

#                 logger.info(f"Saved {photo_count} photos with reference_id={reference_id}")

#             await self.db.flush()

#             return await self._get_report_dict(report_id)

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error submitting disaster report: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to submit disaster report: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # GET: Single report
#     # ──────────────────────────────────────────────
#     async def get_report(self, report_id: str) -> Dict[str, Any]:
#         """Get a single disaster report by ID."""
#         report = await self._get_report_dict(report_id)
#         if not report:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND,
#                 detail="Disaster report not found."
#             )
#         return report

#     # ──────────────────────────────────────────────
#     # GET: All pending reports (raw list)
#     # ──────────────────────────────────────────────
#     async def get_pending_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
#         """Get all pending reports awaiting admin review."""
#         try:
#             sql = text("""
#                 SELECT
#                     r.id, r.user_id, r.disaster_type, r.severity,
#                     r.description, r.location_address,
#                     ST_Y(r.location::geometry) as latitude,
#                     ST_X(r.location::geometry) as longitude,
#                     r.people_affected, r.multiple_casualties,
#                     r.structural_damage, r.road_blocked,
#                     r.report_status, r.disaster_id, r.reviewed_by_id,
#                     r.reviewed_at, r.rejection_reason, r.created_at,
#                     (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
#                 FROM disaster_reports r
#                 WHERE r.report_status = CAST('PENDING' AS disaster_report_status)
#                   AND r.deleted_at IS NULL
#                 ORDER BY r.created_at ASC
#                 LIMIT :limit
#             """)

#             result = await self.db.execute(sql, {"limit": limit})
#             rows = result.mappings().all()

#             return [self._row_to_report_dict(row) for row in rows]

#         except Exception as e:
#             logger.exception(f"Error fetching pending reports: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to fetch pending reports: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # GET: Clustered pending reports (Smart admin view)
#     # ──────────────────────────────────────────────
#     async def get_clustered_pending_reports(
#         self,
#         radius_meters: int = 500,
#         time_window_hours: int = 1,
#     ) -> List[Dict[str, Any]]:
#         """
#         Get pending reports grouped by proximity + disaster type + time window.

#         Uses PostGIS ST_ClusterDBSCAN to group reports that are:
#           - Within `radius_meters` of each other (default 500m)
#           - AND have the same disaster_type
#           - AND reported within `time_window_hours` of each other (default 1h)
#         """
#         logger.info(f"Fetching clustered pending reports (radius={radius_meters}m, time_window={time_window_hours}h)")

#         try:
#             cluster_sql = text("""
#                 WITH clustered AS (
#                     SELECT
#                         r.id,
#                         r.user_id,
#                         r.disaster_type,
#                         r.severity,
#                         r.description,
#                         r.location_address,
#                         ST_Y(r.location::geometry) as latitude,
#                         ST_X(r.location::geometry) as longitude,
#                         r.people_affected,
#                         r.multiple_casualties,
#                         r.structural_damage,
#                         r.road_blocked,
#                         r.created_at,
#                         (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count,
#                         ST_ClusterDBSCAN(r.location::geometry, eps := :radius, minpoints := 1)
#                             OVER (PARTITION BY r.disaster_type) as cluster_id
#                     FROM disaster_reports r
#                     WHERE r.report_status = CAST('PENDING' AS disaster_report_status)
#                       AND r.deleted_at IS NULL
#                       AND r.created_at >= NOW() - CAST(:time_window || ' hours' AS INTERVAL)
#                 ),
#                 cluster_summary AS (
#                     SELECT
#                         disaster_type,
#                         cluster_id,
#                         COUNT(*) as report_count,
#                         SUM(photo_count) as total_photos,
#                         COUNT(DISTINCT user_id) as unique_reporters,
#                         MAX(people_affected) as max_people_affected,
#                         BOOL_OR(multiple_casualties) as any_casualties,
#                         BOOL_OR(structural_damage) as any_structural_damage,
#                         BOOL_OR(road_blocked) as any_road_blocked,
#                         MIN(created_at) as earliest_report_at,
#                         MAX(created_at) as latest_report_at,
#                         MAX(
#                             CASE severity
#                                 WHEN CAST('CRITICAL' AS disaster_severity) THEN 4
#                                 WHEN CAST('HIGH' AS disaster_severity) THEN 3
#                                 WHEN CAST('MEDIUM' AS disaster_severity) THEN 2
#                                 WHEN CAST('LOW' AS disaster_severity) THEN 1
#                                 ELSE 0
#                             END
#                         ) as max_severity_rank,
#                         ST_Y(ST_Centroid(ST_Collect(ST_MakePoint(longitude, latitude)))) as center_lat,
#                         ST_X(ST_Centroid(ST_Collect(ST_MakePoint(longitude, latitude)))) as center_lon,
#                         ARRAY_AGG(id ORDER BY created_at ASC) as report_ids,
#                         ARRAY_AGG(DISTINCT user_id) as reporter_ids
#                     FROM clustered
#                     GROUP BY disaster_type, cluster_id
#                 )
#                 SELECT
#                     cs.*,
#                     CASE cs.max_severity_rank
#                         WHEN 4 THEN 'CRITICAL'
#                         WHEN 3 THEN 'HIGH'
#                         WHEN 2 THEN 'MEDIUM'
#                         WHEN 1 THEN 'LOW'
#                         ELSE 'LOW'
#                     END as max_severity,
#                     c.id as primary_report_id,
#                     c.description as primary_description,
#                     c.location_address as primary_address
#                 FROM cluster_summary cs
#                 JOIN clustered c ON c.id = cs.report_ids[1]
#                 ORDER BY cs.max_severity_rank DESC, cs.report_count DESC
#             """)

#             result = await self.db.execute(cluster_sql, {"radius": radius_meters, "time_window": str(time_window_hours)})
#             rows = result.mappings().all()

#             clusters = []
#             for row in rows:
#                 clusters.append({
#                     "cluster_id": f"{row['disaster_type']}_{row['cluster_id']}",
#                     "disaster_type": str(row["disaster_type"]),
#                     "max_severity": row["max_severity"],
#                     "report_count": row["report_count"],
#                     "total_photos": int(row["total_photos"] or 0),
#                     "unique_reporters": row["unique_reporters"],
#                     "location": {
#                         "lat": float(row["center_lat"]) if row["center_lat"] else None,
#                         "lon": float(row["center_lon"]) if row["center_lon"] else None,
#                     },
#                     "primary_report": {
#                         "id": str(row["primary_report_id"]),
#                         "description": row["primary_description"],
#                         "location_address": row["primary_address"],
#                     },
#                     "impact": {
#                         "max_people_affected": row["max_people_affected"],
#                         "any_casualties": row["any_casualties"],
#                         "any_structural_damage": row["any_structural_damage"],
#                         "any_road_blocked": row["any_road_blocked"],
#                     },
#                     "timeline": {
#                         "earliest_report": row["earliest_report_at"].isoformat() if row["earliest_report_at"] else None,
#                         "latest_report": row["latest_report_at"].isoformat() if row["latest_report_at"] else None,
#                     },
#                     "report_ids": [str(rid) for rid in row["report_ids"]],
#                     "reporter_ids": [str(uid) for uid in row["reporter_ids"]],
#                 })

#             logger.info(f"Found {len(clusters)} disaster clusters from pending reports")
#             return clusters

#         except Exception as e:
#             logger.exception(f"Error fetching clustered reports: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to fetch clustered reports: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # GET: Reports by user
#     # ──────────────────────────────────────────────
#     async def get_user_reports(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
#         """Get all reports submitted by a specific user."""
#         try:
#             sql = text("""
#                 SELECT
#                     r.id, r.user_id, r.disaster_type, r.severity,
#                     r.description, r.location_address,
#                     ST_Y(r.location::geometry) as latitude,
#                     ST_X(r.location::geometry) as longitude,
#                     r.people_affected, r.multiple_casualties,
#                     r.structural_damage, r.road_blocked,
#                     r.report_status, r.disaster_id, r.reviewed_by_id,
#                     r.reviewed_at, r.rejection_reason, r.created_at,
#                     (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
#                 FROM disaster_reports r
#                 WHERE r.user_id = :user_id
#                   AND r.deleted_at IS NULL
#                 ORDER BY r.created_at DESC
#                 LIMIT :limit
#             """)

#             result = await self.db.execute(sql, {"user_id": user_id, "limit": limit})
#             rows = result.mappings().all()

#             return [self._row_to_report_dict(row) for row in rows]

#         except Exception as e:
#             logger.exception(f"Error fetching user reports: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to fetch user reports: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # STEP 4: Admin Review → Approve or Reject
#     # ──────────────────────────────────────────────
#     async def review_report(
#         self,
#         report_id: str,
#         review: AdminReviewRequest,
#     ) -> Dict[str, Any]:
#         """
#         Admin reviews a pending report using raw SQL.

#         If VERIFIED → inserts into disasters table, links back to report.
#         If REJECTED → updates report status + rejection reason.
#         """
#         logger.info(f"Reviewing report {report_id} — action: {review.action}")

#         try:
#             # ── Fetch the report ──
#             fetch_sql = text("""
#                 SELECT
#                     id, user_id, disaster_type, severity, description,
#                     location_address, location,
#                     ST_Y(location::geometry) as latitude,
#                     ST_X(location::geometry) as longitude,
#                     people_affected, multiple_casualties,
#                     structural_damage, road_blocked, report_status
#                 FROM disaster_reports
#                 WHERE id = :report_id AND deleted_at IS NULL
#             """)

#             result = await self.db.execute(fetch_sql, {"report_id": report_id})
#             report = result.mappings().first()

#             if not report:
#                 raise HTTPException(
#                     status_code=status.HTTP_404_NOT_FOUND,
#                     detail="Disaster report not found."
#                 )

#             if str(report["report_status"]) != "PENDING":
#                 raise HTTPException(
#                     status_code=status.HTTP_400_BAD_REQUEST,
#                     detail=f"Report already reviewed. Current status: {report['report_status']}"
#                 )

#             now = datetime.utcnow()

#             # ── REJECTED ──
#             if review.action == "rejected":
#                 if not review.rejection_reason:
#                     raise HTTPException(
#                         status_code=status.HTTP_400_BAD_REQUEST,
#                         detail="rejection_reason is required when rejecting a report."
#                     )

#                 reject_sql = text("""
#                     UPDATE disaster_reports
#                     SET report_status = CAST(:status AS disaster_report_status),
#                         reviewed_by_id = :reviewed_by_id,
#                         reviewed_at = :reviewed_at,
#                         rejection_reason = :rejection_reason,
#                         updated_at = :updated_at
#                     WHERE id = :report_id
#                 """)

#                 await self.db.execute(reject_sql, {
#                     "report_id": report_id,
#                     "status": "REJECTED",
#                     "reviewed_by_id": review.reviewed_by_id,
#                     "reviewed_at": now,
#                     "rejection_reason": review.rejection_reason,
#                     "updated_at": now,
#                 })

#                 await self.db.flush()
#                 logger.info(f"Report {report_id} REJECTED by {review.reviewed_by_id}")

#                 return {
#                     "report_id": report_id,
#                     "report_status": "REJECTED",
#                     "disaster_id": None,
#                     "tracking_id": None,
#                     "reviewed_by_id": review.reviewed_by_id,
#                     "reviewed_at": now.isoformat(),
#                     "message": "Report has been rejected.",
#                 }

#             # ── VERIFIED → Create Disaster via raw SQL ──
#             disaster_id = str(uuid.uuid4())
#             tracking_id = await self._generate_tracking_id()

#             # Auto-assign: look up reviewer's department
#             team_info = await self._get_team_info(review.reviewed_by_id)

#             insert_disaster_sql = text("""
#                 INSERT INTO disasters (
#                     id, created_at, updated_at,
#                     tracking_id, type, severity, disaster_status,
#                     location, location_address, affected_area,
#                     description, people_affected,
#                     multiple_casualties, structural_damage, road_blocked,
#                     assigned_to_id, assigned_department,
#                     response_time, resolved_time, resolution_notes,
#                     created_by_id, disaster_metadata
#                 ) VALUES (
#                     :id, :created_at, :updated_at,
#                     :tracking_id,
#                     CAST(:type AS disaster_type),
#                     CAST(:severity AS disaster_severity),
#                     CAST(:disaster_status AS disaster_status),
#                     ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
#                     :location_address, NULL,
#                     :description, :people_affected,
#                     :multiple_casualties, :structural_damage, :road_blocked,
#                     :assigned_to_id, CAST(:assigned_department AS department),
#                     NULL, NULL, NULL,
#                     :created_by_id, NULL
#                 )
#             """)

#             await self.db.execute(insert_disaster_sql, {
#                 "id": disaster_id,
#                 "created_at": now,
#                 "updated_at": now,
#                 "tracking_id": tracking_id,
#                 "type": str(report["disaster_type"]).upper(),
#                 "severity": str(report["severity"]).upper(),
#                 "disaster_status": "ACTIVE",
#                 "longitude": float(report["longitude"]),
#                 "latitude": float(report["latitude"]),
#                 "location_address": report["location_address"],
#                 "description": report["description"],
#                 "people_affected": report["people_affected"],
#                 "multiple_casualties": report["multiple_casualties"],
#                 "structural_damage": report["structural_damage"],
#                 "road_blocked": report["road_blocked"],
#                 "assigned_to_id": review.reviewed_by_id,
#                 "assigned_department": team_info["department"] if team_info else "IT",
#                 "created_by_id": review.reviewed_by_id,
#             })

#             # ── Link report back to disaster ──
#             update_report_sql = text("""
#                 UPDATE disaster_reports
#                 SET report_status = CAST(:status AS disaster_report_status),
#                     disaster_id = :disaster_id,
#                     reviewed_by_id = :reviewed_by_id,
#                     reviewed_at = :reviewed_at,
#                     updated_at = :updated_at
#                 WHERE id = :report_id
#             """)

#             await self.db.execute(update_report_sql, {
#                 "report_id": report_id,
#                 "status": "VERIFIED",
#                 "disaster_id": disaster_id,
#                 "reviewed_by_id": review.reviewed_by_id,
#                 "reviewed_at": now,
#                 "updated_at": now,
#             })

#             await self.db.flush()

#             logger.info(
#                 f"Report {report_id} VERIFIED → Disaster {disaster_id} "
#                 f"(tracking_id={tracking_id}) created by {review.reviewed_by_id}"
#             )

#             # Publish to RabbitMQ → triggers evaluation, coordination, notification
#             publish_disaster_reported({
#                 "disaster_id": disaster_id,
#                 "tracking_id": tracking_id,
#                 "type": str(report["disaster_type"]).upper(),
#                 "severity": str(report["severity"]).upper(),
#                 "location": {"lat": float(report["latitude"]), "lon": float(report["longitude"])},
#                 "location_address": report["location_address"],
#                 "description": report["description"],
#                 "people_affected": report["people_affected"],
#                 "multiple_casualties": report["multiple_casualties"],
#                 "structural_damage": report["structural_damage"],
#                 "road_blocked": report["road_blocked"],
#                 "assigned_to_id": review.reviewed_by_id,
#                 "assigned_department": team_info["department"] if team_info else None,
#                 "created_by_id": review.reviewed_by_id,
#             })

#             return {
#                 "report_id": report_id,
#                 "report_status": "VERIFIED",
#                 "disaster_id": disaster_id,
#                 "tracking_id": tracking_id,
#                 "reviewed_by_id": review.reviewed_by_id,
#                 "reviewed_at": now.isoformat(),
#                 "assigned_to": team_info["full_name"] if team_info else None,
#                 "assigned_department": team_info["department"] if team_info else None,
#                 "message": f"Report verified. Disaster {tracking_id} created and assigned to {team_info['full_name']} ({team_info['department']})" if team_info else f"Report verified. Disaster {tracking_id} created.",
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error reviewing report: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to review disaster report: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # STEP 4b: Bulk approve entire cluster
#     # ──────────────────────────────────────────────
#     async def review_cluster(
#         self,
#         report_ids: List[str],
#         review: AdminReviewRequest,
#     ) -> Dict[str, Any]:
#         """
#         Admin approves/rejects an entire cluster of reports at once.

#         If VERIFIED → Creates ONE disaster, links ALL reports.
#         If REJECTED → Marks ALL reports as rejected.
#         """
#         logger.info(f"Reviewing cluster of {len(report_ids)} reports — action: {review.action}")

#         try:
#             now = datetime.utcnow()

#             # ── REJECTED ──
#             if review.action == "rejected":
#                 if not review.rejection_reason:
#                     raise HTTPException(
#                         status_code=status.HTTP_400_BAD_REQUEST,
#                         detail="rejection_reason is required when rejecting."
#                     )

#                 for rid in report_ids:
#                     reject_sql = text("""
#                         UPDATE disaster_reports
#                         SET report_status = CAST(:status AS disaster_report_status),
#                             reviewed_by_id = :reviewed_by_id,
#                             reviewed_at = :reviewed_at,
#                             rejection_reason = :rejection_reason,
#                             updated_at = :updated_at
#                         WHERE id = :report_id
#                           AND report_status = CAST('PENDING' AS disaster_report_status)
#                     """)
#                     await self.db.execute(reject_sql, {
#                         "report_id": rid,
#                         "status": "REJECTED",
#                         "reviewed_by_id": review.reviewed_by_id,
#                         "reviewed_at": now,
#                         "rejection_reason": review.rejection_reason,
#                         "updated_at": now,
#                     })

#                 await self.db.flush()

#                 return {
#                     "action": "rejected",
#                     "reports_updated": len(report_ids),
#                     "disaster_id": None,
#                     "tracking_id": None,
#                     "reviewed_by_id": review.reviewed_by_id,
#                     "reviewed_at": now.isoformat(),
#                     "message": f"Rejected {len(report_ids)} reports in cluster.",
#                 }

#             # ── VERIFIED ──
#             primary_id = report_ids[0]
#             fetch_sql = text("""
#                 SELECT
#                     id, disaster_type, severity, description,
#                     location_address,
#                     ST_Y(location::geometry) as latitude,
#                     ST_X(location::geometry) as longitude,
#                     people_affected, multiple_casualties,
#                     structural_damage, road_blocked
#                 FROM disaster_reports
#                 WHERE id = :report_id AND deleted_at IS NULL
#             """)

#             result = await self.db.execute(fetch_sql, {"report_id": primary_id})
#             primary = result.mappings().first()

#             if not primary:
#                 raise HTTPException(
#                     status_code=status.HTTP_404_NOT_FOUND,
#                     detail="Primary report not found."
#                 )

#             disaster_id = str(uuid.uuid4())
#             tracking_id = await self._generate_tracking_id()

#             # Auto-assign: look up reviewer's department
#             team_info = await self._get_team_info(review.reviewed_by_id)

#             agg_sql = text("""
#                 SELECT
#                     MAX(people_affected) as max_people,
#                     BOOL_OR(multiple_casualties) as any_casualties,
#                     BOOL_OR(structural_damage) as any_damage,
#                     BOOL_OR(road_blocked) as any_blocked
#                 FROM disaster_reports
#                 WHERE id = ANY(:ids)
#             """)
#             agg_result = await self.db.execute(agg_sql, {"ids": report_ids})
#             agg = agg_result.mappings().first()

#             metadata = None

#             insert_disaster_sql = text("""
#                 INSERT INTO disasters (
#                     id, created_at, updated_at,
#                     tracking_id, type, severity, disaster_status,
#                     location, location_address, affected_area,
#                     description, people_affected,
#                     multiple_casualties, structural_damage, road_blocked,
#                     assigned_to_id, assigned_department,
#                     response_time, resolved_time, resolution_notes,
#                     created_by_id, disaster_metadata
#                 ) VALUES (
#                     :id, :created_at, :updated_at,
#                     :tracking_id,
#                     CAST(:type AS disaster_type),
#                     CAST(:severity AS disaster_severity),
#                     CAST(:disaster_status AS disaster_status),
#                     ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
#                     :location_address, NULL,
#                     :description, :people_affected,
#                     :multiple_casualties, :structural_damage, :road_blocked,
#                     :assigned_to_id, CAST(:assigned_department AS department),
#                     NULL, NULL, NULL,
#                     :created_by_id,
#                     :metadata
#                 )
#             """)

#             await self.db.execute(insert_disaster_sql, {
#                 "id": disaster_id,
#                 "created_at": now,
#                 "updated_at": now,
#                 "tracking_id": tracking_id,
#                 "type": str(primary["disaster_type"]).upper(),
#                 "severity": str(primary["severity"]).upper(),
#                 "disaster_status": "ACTIVE",
#                 "longitude": float(primary["longitude"]),
#                 "latitude": float(primary["latitude"]),
#                 "location_address": primary["location_address"],
#                 "description": primary["description"],
#                 "people_affected": agg["max_people"] or 0,
#                 "multiple_casualties": agg["any_casualties"] or False,
#                 "structural_damage": agg["any_damage"] or False,
#                 "road_blocked": agg["any_blocked"] or False,
#                 "assigned_to_id": review.reviewed_by_id,
#                 "assigned_department": team_info["department"] if team_info else "IT",
#                 "created_by_id": review.reviewed_by_id,
#                 "metadata": metadata,
#             })

#             for rid in report_ids:
#                 update_sql = text("""
#                     UPDATE disaster_reports
#                     SET report_status = CAST(:status AS disaster_report_status),
#                         disaster_id = :disaster_id,
#                         reviewed_by_id = :reviewed_by_id,
#                         reviewed_at = :reviewed_at,
#                         updated_at = :updated_at
#                     WHERE id = :report_id
#                       AND report_status = CAST('PENDING' AS disaster_report_status)
#                 """)
#                 await self.db.execute(update_sql, {
#                     "report_id": rid,
#                     "status": "VERIFIED",
#                     "disaster_id": disaster_id,
#                     "reviewed_by_id": review.reviewed_by_id,
#                     "reviewed_at": now,
#                     "updated_at": now,
#                 })

#             await self.db.flush()

#             logger.info(
#                 f"Cluster of {len(report_ids)} reports VERIFIED → Disaster {disaster_id} "
#                 f"(tracking_id={tracking_id})"
#             )

#             # Publish to RabbitMQ → triggers evaluation, coordination, notification
#             publish_disaster_reported({
#                 "disaster_id": disaster_id,
#                 "tracking_id": tracking_id,
#                 "type": str(primary["disaster_type"]).upper(),
#                 "severity": str(primary["severity"]).upper(),
#                 "location": {"lat": float(primary["latitude"]), "lon": float(primary["longitude"])},
#                 "location_address": primary["location_address"],
#                 "description": primary["description"],
#                 "people_affected": agg["max_people"] or 0,
#                 "multiple_casualties": agg["any_casualties"] or False,
#                 "structural_damage": agg["any_damage"] or False,
#                 "road_blocked": agg["any_blocked"] or False,
#                 "assigned_to_id": review.reviewed_by_id,
#                 "assigned_department": team_info["department"] if team_info else None,
#                 "created_by_id": review.reviewed_by_id,
#             })

#             return {
#                 "action": "verified",
#                 "reports_updated": len(report_ids),
#                 "disaster_id": disaster_id,
#                 "tracking_id": tracking_id,
#                 "reviewed_by_id": review.reviewed_by_id,
#                 "reviewed_at": now.isoformat(),
#                 "assigned_to": team_info["full_name"] if team_info else None,
#                 "assigned_department": team_info["department"] if team_info else None,
#                 "message": f"Cluster verified. {len(report_ids)} reports linked to disaster {tracking_id}, assigned to {team_info['full_name']} ({team_info['department']})" if team_info else f"Cluster verified. {len(report_ids)} reports linked to disaster {tracking_id}",
#             }

#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.exception(f"Error reviewing cluster: {e}")
#             raise HTTPException(
#                 status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#                 detail=f"Failed to review cluster: {str(e)}"
#             )

#     # ──────────────────────────────────────────────
#     # HELPERS
#     # ──────────────────────────────────────────────
#     async def _generate_tracking_id(self) -> str:
#         """Generate sequential tracking ID like DIS-2026-00001."""
#         year = datetime.utcnow().year
#         pattern = f"DIS-{year}-%"

#         count_sql = text("""
#             SELECT COUNT(*) FROM disasters WHERE tracking_id LIKE :pattern
#         """)
#         result = await self.db.execute(count_sql, {"pattern": pattern})
#         count = result.scalar() or 0

#         return f"DIS-{year}-{count + 1:05d}"

#     async def _get_team_info(self, team_id: str) -> Dict[str, Any]:
#         """Fetch emergency team member's name and department."""
#         sql = text("""
#             SELECT id, full_name, department FROM emergency_teams
#             WHERE id = :team_id AND deleted_at IS NULL
#         """)
#         result = await self.db.execute(sql, {"team_id": team_id})
#         row = result.mappings().first()
#         if not row:
#             return None
#         return {"id": str(row["id"]), "full_name": row["full_name"], "department": str(row["department"])}

#     async def _get_report_dict(self, report_id: str) -> Dict[str, Any]:
#         """Fetch a single report as dict using raw SQL."""
#         sql = text("""
#             SELECT
#                 r.id, r.user_id, r.disaster_type, r.severity,
#                 r.description, r.location_address,
#                 ST_Y(r.location::geometry) as latitude,
#                 ST_X(r.location::geometry) as longitude,
#                 r.people_affected, r.multiple_casualties,
#                 r.structural_damage, r.road_blocked,
#                 r.report_status, r.disaster_id, r.reviewed_by_id,
#                 r.reviewed_at, r.rejection_reason, r.created_at,
#                 (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
#             FROM disaster_reports r
#             WHERE r.id = :report_id AND r.deleted_at IS NULL
#         """)

#         result = await self.db.execute(sql, {"report_id": report_id})
#         row = result.mappings().first()

#         if not row:
#             return None

#         return self._row_to_report_dict(row)

#     def _row_to_report_dict(self, row) -> Dict[str, Any]:
#         """Convert a raw SQL row to a report dictionary."""
#         return {
#             "id": str(row["id"]),
#             "user_id": str(row["user_id"]),
#             "disaster_type": str(row["disaster_type"]),
#             "severity": str(row["severity"]),
#             "description": row["description"],
#             "location": {
#                 "lat": float(row["latitude"]) if row["latitude"] else None,
#                 "lon": float(row["longitude"]) if row["longitude"] else None,
#             },
#             "location_address": row["location_address"],
#             "people_affected": row["people_affected"],
#             "multiple_casualties": row["multiple_casualties"],
#             "structural_damage": row["structural_damage"],
#             "road_blocked": row["road_blocked"],
#             "report_status": str(row["report_status"]),
#             "disaster_id": str(row["disaster_id"]) if row["disaster_id"] else None,
#             "reviewed_by_id": str(row["reviewed_by_id"]) if row["reviewed_by_id"] else None,
#             "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
#             "rejection_reason": row["rejection_reason"],
#             "created_at": row["created_at"].isoformat() if row["created_at"] else None,
#             "photo_count": row["photo_count"] or 0,
#         }








# File: app/services/disaster_report_service.py
"""
Disaster Report Service - Business logic for report submission and review.

Uses RAW SQL for database operations to avoid ORM relationship issues.
All enum values are UPPERCASE to match PostgreSQL enum definitions.
Uses datetime.utcnow() for timestamps (no timezone info).
Uses separate parameters for each timestamp to avoid type conflicts.

Workflow:
  STEP 2: Save disaster report    → disaster_reports (status=PENDING)
  STEP 3: Save photos             → disaster_photos  (all share same reference_id)
  STEP 4: Admin review            → if approved → create entry in disasters table
"""

import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException, status

from app.schemas.disaster_report import (
    DisasterReportCreateRequest,
    AdminReviewRequest,
)
from app.services.rabbitmq_service import publish_disaster_reported

logger = logging.getLogger(__name__)


class DisasterReportService:
    """
    Service layer for disaster report operations.
    All DB operations use raw SQL to avoid ORM relationship loading issues.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ──────────────────────────────────────────────
    # STEP 2 + 3: Create report + save photos
    # ──────────────────────────────────────────────
    async def create_report(self, data: DisasterReportCreateRequest) -> Dict[str, Any]:
        """
        Create a new disaster report with photos using raw SQL.

        - Inserts 1 row in disaster_reports (status='PENDING', disaster_id=NULL)
        - Inserts N rows in disaster_photos (all share same reference_id)
        """
        logger.info(f"Creating disaster report for user {data.user_id}")

        try:
            report_id = str(uuid.uuid4())
            now = datetime.utcnow()

            insert_report_sql = text("""
                INSERT INTO disaster_reports (
                    id, created_at, updated_at,
                    user_id, location_address, disaster_type, severity,
                    description, location, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    report_status, disaster_id, reviewed_by_id, reviewed_at, rejection_reason
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :user_id, :location_address,
                    CAST(:disaster_type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    :description,
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    CAST(:report_status AS disaster_report_status),
                    NULL, NULL, NULL, NULL
                )
            """)

            await self.db.execute(insert_report_sql, {
                "id": report_id,
                "created_at": now,
                "updated_at": now,
                "user_id": data.user_id,
                "location_address": data.location_address,
                "disaster_type": data.disaster_type.upper(),
                "severity": data.severity.upper(),
                "description": data.description,
                "latitude": data.latitude,
                "longitude": data.longitude,
                "people_affected": data.people_affected or 0,
                "multiple_casualties": data.multiple_casualties or False,
                "structural_damage": data.structural_damage or False,
                "road_blocked": data.road_blocked or False,
                "report_status": "PENDING",
            })

            logger.info(f"Created disaster report: {report_id}")

            # ── STEP 3: Save photos ──
            photo_count = 0
            if data.photos:
                reference_id = data.reference_id or str(uuid.uuid4())

                insert_photo_sql = text("""
                    INSERT INTO disaster_photos (
                        id, created_at, updated_at,
                        image_url, caption, file_size, mime_type,
                        disaster_report_id, reference_id
                    ) VALUES (
                        :id, :created_at, :updated_at,
                        :image_url, :caption, :file_size, :mime_type,
                        :disaster_report_id, :reference_id
                    )
                """)

                for photo_data in data.photos:
                    await self.db.execute(insert_photo_sql, {
                        "id": str(uuid.uuid4()),
                        "created_at": now,
                        "updated_at": now,
                        "image_url": photo_data.image_url,
                        "caption": photo_data.caption,
                        "file_size": photo_data.file_size,
                        "mime_type": photo_data.mime_type,
                        "disaster_report_id": report_id,
                        "reference_id": reference_id,
                    })
                    photo_count += 1

                logger.info(f"Saved {photo_count} photos with reference_id={reference_id}")

            await self.db.flush()

            return await self._get_report_dict(report_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error creating disaster report: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create disaster report: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # COMBINED: Upload photos + Create report (All-in-one)
    # ──────────────────────────────────────────────
    async def submit_report(
        self,
        user_id: str,
        location_address: str,
        disaster_type: str,
        severity: str,
        description: str,
        latitude: float,
        longitude: float,
        people_affected: int,
        multiple_casualties: bool,
        structural_damage: bool,
        road_blocked: bool,
        uploaded_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        All-in-one: Creates report + saves photos in one transaction.

        uploaded_files = list of dicts from blob_service.upload_multiple_files():
          [{"image_url": "...", "file_size": 123, "mime_type": "image/jpeg", "original_filename": "..."}]
        """
        logger.info(f"Submitting disaster report for user {user_id} with {len(uploaded_files)} photos")

        try:
            report_id = str(uuid.uuid4())
            reference_id = str(uuid.uuid4())
            now = datetime.utcnow()

            insert_report_sql = text("""
                INSERT INTO disaster_reports (
                    id, created_at, updated_at,
                    user_id, location_address, disaster_type, severity,
                    description, location, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    report_status, disaster_id, reviewed_by_id, reviewed_at, rejection_reason
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :user_id, :location_address,
                    CAST(:disaster_type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    :description,
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    CAST(:report_status AS disaster_report_status),
                    NULL, NULL, NULL, NULL
                )
            """)

            await self.db.execute(insert_report_sql, {
                "id": report_id,
                "created_at": now,
                "updated_at": now,
                "user_id": user_id,
                "location_address": location_address,
                "disaster_type": disaster_type.upper(),
                "severity": severity.upper(),
                "description": description,
                "latitude": latitude,
                "longitude": longitude,
                "people_affected": people_affected,
                "multiple_casualties": multiple_casualties,
                "structural_damage": structural_damage,
                "road_blocked": road_blocked,
                "report_status": "PENDING",
            })

            logger.info(f"Created disaster report: {report_id}")

            # ── Save photos ──
            photo_count = 0
            if uploaded_files:
                insert_photo_sql = text("""
                    INSERT INTO disaster_photos (
                        id, created_at, updated_at,
                        image_url, caption, file_size, mime_type,
                        disaster_report_id, reference_id
                    ) VALUES (
                        :id, :created_at, :updated_at,
                        :image_url, :caption, :file_size, :mime_type,
                        :disaster_report_id, :reference_id
                    )
                """)

                for file_data in uploaded_files:
                    await self.db.execute(insert_photo_sql, {
                        "id": str(uuid.uuid4()),
                        "created_at": now,
                        "updated_at": now,
                        "image_url": file_data["image_url"],
                        "caption": file_data.get("original_filename", ""),
                        "file_size": file_data.get("file_size", 0),
                        "mime_type": file_data.get("mime_type", ""),
                        "disaster_report_id": report_id,
                        "reference_id": reference_id,
                    })
                    photo_count += 1

                logger.info(f"Saved {photo_count} photos with reference_id={reference_id}")

            await self.db.flush()

            return await self._get_report_dict(report_id)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error submitting disaster report: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to submit disaster report: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # GET: Single report
    # ──────────────────────────────────────────────
    async def get_report(self, report_id: str) -> Dict[str, Any]:
        """Get a single disaster report by ID."""
        report = await self._get_report_dict(report_id)
        if not report:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Disaster report not found."
            )
        return report

    # ──────────────────────────────────────────────
    # GET: All pending reports (raw list)
    # ──────────────────────────────────────────────
    async def get_pending_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get all pending reports awaiting admin review."""
        try:
            sql = text("""
                SELECT
                    r.id, r.user_id, r.disaster_type, r.severity,
                    r.description, r.location_address,
                    ST_Y(r.location::geometry) as latitude,
                    ST_X(r.location::geometry) as longitude,
                    r.people_affected, r.multiple_casualties,
                    r.structural_damage, r.road_blocked,
                    r.report_status, r.disaster_id, r.reviewed_by_id,
                    r.reviewed_at, r.rejection_reason, r.created_at,
                    (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
                FROM disaster_reports r
                WHERE r.report_status = CAST('PENDING' AS disaster_report_status)
                  AND r.deleted_at IS NULL
                ORDER BY r.created_at ASC
                LIMIT :limit
            """)

            result = await self.db.execute(sql, {"limit": limit})
            rows = result.mappings().all()

            return [self._row_to_report_dict(row) for row in rows]

        except Exception as e:
            logger.exception(f"Error fetching pending reports: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch pending reports: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # GET: Clustered pending reports (Smart admin view)
    # ──────────────────────────────────────────────
    async def get_clustered_pending_reports(
        self,
        radius_meters: int = 500,
        time_window_hours: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Get pending reports grouped by proximity + disaster type + time window.

        Uses PostGIS ST_ClusterDBSCAN to group reports that are:
          - Within `radius_meters` of each other (default 500m)
          - AND have the same disaster_type
          - AND reported within `time_window_hours` of each other (default 1h)
        """
        logger.info(f"Fetching clustered pending reports (radius={radius_meters}m, time_window={time_window_hours}h)")

        try:
            cluster_sql = text("""
                WITH clustered AS (
                    SELECT
                        r.id,
                        r.user_id,
                        r.disaster_type,
                        r.severity,
                        r.description,
                        r.location_address,
                        ST_Y(r.location::geometry) as latitude,
                        ST_X(r.location::geometry) as longitude,
                        r.people_affected,
                        r.multiple_casualties,
                        r.structural_damage,
                        r.road_blocked,
                        r.created_at,
                        (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count,
                        ST_ClusterDBSCAN(r.location::geometry, eps := :radius, minpoints := 1)
                            OVER (PARTITION BY r.disaster_type) as cluster_id
                    FROM disaster_reports r
                    WHERE r.report_status = CAST('PENDING' AS disaster_report_status)
                      AND r.deleted_at IS NULL
                      AND r.created_at >= NOW() - CAST(:time_window || ' hours' AS INTERVAL)
                ),
                cluster_summary AS (
                    SELECT
                        disaster_type,
                        cluster_id,
                        COUNT(*) as report_count,
                        SUM(photo_count) as total_photos,
                        COUNT(DISTINCT user_id) as unique_reporters,
                        MAX(people_affected) as max_people_affected,
                        BOOL_OR(multiple_casualties) as any_casualties,
                        BOOL_OR(structural_damage) as any_structural_damage,
                        BOOL_OR(road_blocked) as any_road_blocked,
                        MIN(created_at) as earliest_report_at,
                        MAX(created_at) as latest_report_at,
                        MAX(
                            CASE severity
                                WHEN CAST('CRITICAL' AS disaster_severity) THEN 4
                                WHEN CAST('HIGH' AS disaster_severity) THEN 3
                                WHEN CAST('MEDIUM' AS disaster_severity) THEN 2
                                WHEN CAST('LOW' AS disaster_severity) THEN 1
                                ELSE 0
                            END
                        ) as max_severity_rank,
                        ST_Y(ST_Centroid(ST_Collect(ST_MakePoint(longitude, latitude)))) as center_lat,
                        ST_X(ST_Centroid(ST_Collect(ST_MakePoint(longitude, latitude)))) as center_lon,
                        ARRAY_AGG(id ORDER BY created_at ASC) as report_ids,
                        ARRAY_AGG(DISTINCT user_id) as reporter_ids
                    FROM clustered
                    GROUP BY disaster_type, cluster_id
                )
                SELECT
                    cs.*,
                    CASE cs.max_severity_rank
                        WHEN 4 THEN 'CRITICAL'
                        WHEN 3 THEN 'HIGH'
                        WHEN 2 THEN 'MEDIUM'
                        WHEN 1 THEN 'LOW'
                        ELSE 'LOW'
                    END as max_severity,
                    c.id as primary_report_id,
                    c.description as primary_description,
                    c.location_address as primary_address
                FROM cluster_summary cs
                JOIN clustered c ON c.id = cs.report_ids[1]
                ORDER BY cs.max_severity_rank DESC, cs.report_count DESC
            """)

            result = await self.db.execute(cluster_sql, {"radius": radius_meters, "time_window": str(time_window_hours)})
            rows = result.mappings().all()

            clusters = []
            for row in rows:
                clusters.append({
                    "cluster_id": f"{row['disaster_type']}_{row['cluster_id']}",
                    "disaster_type": str(row["disaster_type"]),
                    "max_severity": row["max_severity"],
                    "report_count": row["report_count"],
                    "total_photos": int(row["total_photos"] or 0),
                    "unique_reporters": row["unique_reporters"],
                    "location": {
                        "lat": float(row["center_lat"]) if row["center_lat"] else None,
                        "lon": float(row["center_lon"]) if row["center_lon"] else None,
                    },
                    "primary_report": {
                        "id": str(row["primary_report_id"]),
                        "description": row["primary_description"],
                        "location_address": row["primary_address"],
                    },
                    "impact": {
                        "max_people_affected": row["max_people_affected"],
                        "any_casualties": row["any_casualties"],
                        "any_structural_damage": row["any_structural_damage"],
                        "any_road_blocked": row["any_road_blocked"],
                    },
                    "timeline": {
                        "earliest_report": row["earliest_report_at"].isoformat() if row["earliest_report_at"] else None,
                        "latest_report": row["latest_report_at"].isoformat() if row["latest_report_at"] else None,
                    },
                    "report_ids": [str(rid) for rid in row["report_ids"]],
                    "reporter_ids": [str(uid) for uid in row["reporter_ids"]],
                })

            logger.info(f"Found {len(clusters)} disaster clusters from pending reports")
            return clusters

        except Exception as e:
            logger.exception(f"Error fetching clustered reports: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch clustered reports: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # GET: Reports by user
    # ──────────────────────────────────────────────
    async def get_user_reports(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get all reports submitted by a specific user."""
        try:
            sql = text("""
                SELECT
                    r.id, r.user_id, r.disaster_type, r.severity,
                    r.description, r.location_address,
                    ST_Y(r.location::geometry) as latitude,
                    ST_X(r.location::geometry) as longitude,
                    r.people_affected, r.multiple_casualties,
                    r.structural_damage, r.road_blocked,
                    r.report_status, r.disaster_id, r.reviewed_by_id,
                    r.reviewed_at, r.rejection_reason, r.created_at,
                    (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
                FROM disaster_reports r
                WHERE r.user_id = :user_id
                  AND r.deleted_at IS NULL
                ORDER BY r.created_at DESC
                LIMIT :limit
            """)

            result = await self.db.execute(sql, {"user_id": user_id, "limit": limit})
            rows = result.mappings().all()

            return [self._row_to_report_dict(row) for row in rows]

        except Exception as e:
            logger.exception(f"Error fetching user reports: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch user reports: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # STEP 4: Admin Review → Approve or Reject
    # ──────────────────────────────────────────────
    async def review_report(
        self,
        report_id: str,
        review: AdminReviewRequest,
    ) -> Dict[str, Any]:
        """
        Admin reviews a pending report using raw SQL.

        If VERIFIED → inserts into disasters table, links back to report.
        If REJECTED → updates report status + rejection reason.
        """
        logger.info(f"Reviewing report {report_id} — action: {review.action}")

        try:
            # ── Fetch the report ──
            fetch_sql = text("""
                SELECT
                    id, user_id, disaster_type, severity, description,
                    location_address, location,
                    ST_Y(location::geometry) as latitude,
                    ST_X(location::geometry) as longitude,
                    people_affected, multiple_casualties,
                    structural_damage, road_blocked, report_status
                FROM disaster_reports
                WHERE id = :report_id AND deleted_at IS NULL
            """)

            result = await self.db.execute(fetch_sql, {"report_id": report_id})
            report = result.mappings().first()

            if not report:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Disaster report not found."
                )

            if str(report["report_status"]).upper() not in ("PENDING",):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Report already reviewed. Current status: {report['report_status']}"
                )

            now = datetime.utcnow()

            # ── REJECTED ──
            if review.action == "rejected":
                if not review.rejection_reason:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="rejection_reason is required when rejecting a report."
                    )

                # FIX #3 (reject path): Atomic conditional UPDATE — WHERE PENDING ensures
                # a concurrent rejection can't double-process the same report.
                reject_sql = text("""
                    UPDATE disaster_reports
                    SET report_status = CAST(:status AS disaster_report_status),
                        reviewed_by_id = :reviewed_by_id,
                        reviewed_at = :reviewed_at,
                        rejection_reason = :rejection_reason,
                        updated_at = :updated_at
                    WHERE id = :report_id
                      AND report_status = CAST('PENDING' AS disaster_report_status)
                    RETURNING id
                """)

                reject_result = await self.db.execute(reject_sql, {
                    "report_id": report_id,
                    "status": "REJECTED",
                    "reviewed_by_id": review.reviewed_by_id,
                    "reviewed_at": now,
                    "rejection_reason": review.rejection_reason,
                    "updated_at": now,
                })
                if not reject_result.first():
                    raise HTTPException(
                        status_code=409,
                        detail="Report was already reviewed by a concurrent request."
                    )

                await self.db.flush()
                logger.info(f"Report {report_id} REJECTED by {review.reviewed_by_id}")

                return {
                    "report_id": report_id,
                    "report_status": "REJECTED",
                    "disaster_id": None,
                    "tracking_id": None,
                    "reviewed_by_id": review.reviewed_by_id,
                    "reviewed_at": now.isoformat(),
                    "message": "Report has been rejected.",
                    "_pending_event": None,
                }

            # ── VERIFIED → Create Disaster via raw SQL ──
            disaster_id = str(uuid.uuid4())

            # FIX #7: Use a DB sequence instead of COUNT(*)+1 to prevent duplicate
            # tracking IDs when two reports are verified concurrently.
            tracking_id = await self._generate_tracking_id()

            # Auto-assign: look up reviewer's department
            team_info = await self._get_team_info(review.reviewed_by_id)

            insert_disaster_sql = text("""
                INSERT INTO disasters (
                    id, created_at, updated_at,
                    tracking_id, type, severity, disaster_status,
                    location, location_address, affected_area,
                    description, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    assigned_to_id, assigned_department,
                    response_time, resolved_time, resolution_notes,
                    created_by_id, disaster_metadata
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :tracking_id,
                    CAST(:type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    CAST(:disaster_status AS disaster_status),
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :location_address, NULL,
                    :description, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    :assigned_to_id, CAST(:assigned_department AS department),
                    NULL, NULL, NULL,
                    :created_by_id, NULL
                )
            """)

            await self.db.execute(insert_disaster_sql, {
                "id": disaster_id,
                "created_at": now,
                "updated_at": now,
                "tracking_id": tracking_id,
                "type": str(report["disaster_type"]).upper(),
                "severity": str(report["severity"]).upper(),
                "disaster_status": "ACTIVE",
                "longitude": float(report["longitude"]),
                "latitude": float(report["latitude"]),
                "location_address": report["location_address"],
                "description": report["description"],
                "people_affected": report["people_affected"],
                "multiple_casualties": report["multiple_casualties"],
                "structural_damage": report["structural_damage"],
                "road_blocked": report["road_blocked"],
                "assigned_to_id": review.reviewed_by_id,
                "assigned_department": team_info["department"] if team_info else "IT",
                "created_by_id": review.reviewed_by_id,
            })

            # ── FIX #3 (verified path): Atomic gate — update the report FIRST with
            # AND PENDING guard. If a concurrent request already claimed it, we get
            # 0 rows back and raise 409 before the disaster INSERT ever happens.
            gate_sql = text("""
                UPDATE disaster_reports
                SET report_status = CAST(:status AS disaster_report_status),
                    disaster_id = :disaster_id,
                    reviewed_by_id = :reviewed_by_id,
                    reviewed_at = :reviewed_at,
                    updated_at = :updated_at
                WHERE id = :report_id
                  AND report_status = CAST('PENDING' AS disaster_report_status)
                RETURNING id
            """)
            gate_result = await self.db.execute(gate_sql, {
                "report_id": report_id,
                "status": "VERIFIED",
                "disaster_id": disaster_id,
                "reviewed_by_id": review.reviewed_by_id,
                "reviewed_at": now,
                "updated_at": now,
            })
            if not gate_result.first():
                raise HTTPException(
                    status_code=409,
                    detail="Report was already reviewed by a concurrent request."
                )

            await self.db.flush()

            logger.info(
                f"Report {report_id} VERIFIED → Disaster {disaster_id} "
                f"(tracking_id={tracking_id}) created by {review.reviewed_by_id}"
            )

            # FIX #8: Return event payload — do NOT publish here (transaction not committed yet).
            pending_event = {
                "disaster_id": disaster_id,
                "tracking_id": tracking_id,
                "type": str(report["disaster_type"]).upper(),
                "severity": str(report["severity"]).upper(),
                "location": {"lat": float(report["latitude"]), "lon": float(report["longitude"])},
                "location_address": report["location_address"],
                "description": report["description"],
                "people_affected": report["people_affected"],
                "multiple_casualties": report["multiple_casualties"],
                "structural_damage": report["structural_damage"],
                "road_blocked": report["road_blocked"],
                "assigned_to_id": review.reviewed_by_id,
                "assigned_department": team_info["department"] if team_info else None,
                "created_by_id": review.reviewed_by_id,
            }

            return {
                "report_id": report_id,
                "report_status": "VERIFIED",
                "disaster_id": disaster_id,
                "tracking_id": tracking_id,
                "reviewed_by_id": review.reviewed_by_id,
                "reviewed_at": now.isoformat(),
                "assigned_to": team_info["full_name"] if team_info else None,
                "assigned_department": team_info["department"] if team_info else None,
                "message": f"Report verified. Disaster {tracking_id} created and assigned to {team_info['full_name']} ({team_info['department']})" if team_info else f"Report verified. Disaster {tracking_id} created.",
                "_pending_event": pending_event,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error reviewing report: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to review disaster report: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # STEP 4b: Bulk approve entire cluster
    # ──────────────────────────────────────────────
    async def review_cluster(
        self,
        report_ids: List[str],
        review: AdminReviewRequest,
    ) -> Dict[str, Any]:
        """
        Admin approves/rejects an entire cluster of reports at once.

        If VERIFIED → Creates ONE disaster, links ALL reports.
        If REJECTED → Marks ALL reports as rejected.
        """
        logger.info(f"Reviewing cluster of {len(report_ids)} reports — action: {review.action}")

        try:
            now = datetime.utcnow()

            # ── REJECTED ──
            if review.action == "rejected":
                if not review.rejection_reason:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="rejection_reason is required when rejecting."
                    )

                for rid in report_ids:
                    reject_sql = text("""
                        UPDATE disaster_reports
                        SET report_status = CAST(:status AS disaster_report_status),
                            reviewed_by_id = :reviewed_by_id,
                            reviewed_at = :reviewed_at,
                            rejection_reason = :rejection_reason,
                            updated_at = :updated_at
                        WHERE id = :report_id
                          AND report_status = CAST('PENDING' AS disaster_report_status)
                    """)
                    await self.db.execute(reject_sql, {
                        "report_id": rid,
                        "status": "REJECTED",
                        "reviewed_by_id": review.reviewed_by_id,
                        "reviewed_at": now,
                        "rejection_reason": review.rejection_reason,
                        "updated_at": now,
                    })

                await self.db.flush()

                return {
                    "action": "rejected",
                    "reports_updated": len(report_ids),
                    "disaster_id": None,
                    "tracking_id": None,
                    "reviewed_by_id": review.reviewed_by_id,
                    "reviewed_at": now.isoformat(),
                    "message": f"Rejected {len(report_ids)} reports in cluster.",
                }

            # ── VERIFIED ──
            primary_id = report_ids[0]
            fetch_sql = text("""
                SELECT
                    id, disaster_type, severity, description,
                    location_address,
                    ST_Y(location::geometry) as latitude,
                    ST_X(location::geometry) as longitude,
                    people_affected, multiple_casualties,
                    structural_damage, road_blocked
                FROM disaster_reports
                WHERE id = :report_id AND deleted_at IS NULL
            """)

            result = await self.db.execute(fetch_sql, {"report_id": primary_id})
            primary = result.mappings().first()

            if not primary:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Primary report not found."
                )

            disaster_id = str(uuid.uuid4())
            # FIX #7: sequence-based tracking ID (see _generate_tracking_id)
            tracking_id = await self._generate_tracking_id()

            # Auto-assign: look up reviewer's department
            team_info = await self._get_team_info(review.reviewed_by_id)

            agg_sql = text("""
                SELECT
                    MAX(people_affected) as max_people,
                    BOOL_OR(multiple_casualties) as any_casualties,
                    BOOL_OR(structural_damage) as any_damage,
                    BOOL_OR(road_blocked) as any_blocked
                FROM disaster_reports
                WHERE id = ANY(:ids)
            """)
            agg_result = await self.db.execute(agg_sql, {"ids": report_ids})
            agg = agg_result.mappings().first()

            metadata = None

            insert_disaster_sql = text("""
                INSERT INTO disasters (
                    id, created_at, updated_at,
                    tracking_id, type, severity, disaster_status,
                    location, location_address, affected_area,
                    description, people_affected,
                    multiple_casualties, structural_damage, road_blocked,
                    assigned_to_id, assigned_department,
                    response_time, resolved_time, resolution_notes,
                    created_by_id, disaster_metadata
                ) VALUES (
                    :id, :created_at, :updated_at,
                    :tracking_id,
                    CAST(:type AS disaster_type),
                    CAST(:severity AS disaster_severity),
                    CAST(:disaster_status AS disaster_status),
                    ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
                    :location_address, NULL,
                    :description, :people_affected,
                    :multiple_casualties, :structural_damage, :road_blocked,
                    :assigned_to_id, CAST(:assigned_department AS department),
                    NULL, NULL, NULL,
                    :created_by_id,
                    :metadata
                )
            """)

            await self.db.execute(insert_disaster_sql, {
                "id": disaster_id,
                "created_at": now,
                "updated_at": now,
                "tracking_id": tracking_id,
                "type": str(primary["disaster_type"]).upper(),
                "severity": str(primary["severity"]).upper(),
                "disaster_status": "ACTIVE",
                "longitude": float(primary["longitude"]),
                "latitude": float(primary["latitude"]),
                "location_address": primary["location_address"],
                "description": primary["description"],
                "people_affected": agg["max_people"] or 0,
                "multiple_casualties": agg["any_casualties"] or False,
                "structural_damage": agg["any_damage"] or False,
                "road_blocked": agg["any_blocked"] or False,
                "assigned_to_id": review.reviewed_by_id,
                "assigned_department": team_info["department"] if team_info else "IT",
                "created_by_id": review.reviewed_by_id,
                "metadata": metadata,
            })

            # FIX #4: Atomic gate — claim the primary report first with AND PENDING guard.
            # If a concurrent cluster review already claimed it, raise 409 before inserting
            # a second disaster record for the same cluster.
            gate_sql = text("""
                UPDATE disaster_reports
                SET report_status = CAST(:status AS disaster_report_status),
                    disaster_id = :disaster_id,
                    reviewed_by_id = :reviewed_by_id,
                    reviewed_at = :reviewed_at,
                    updated_at = :updated_at
                WHERE id = :report_id
                  AND report_status = CAST('PENDING' AS disaster_report_status)
                RETURNING id
            """)
            gate_result = await self.db.execute(gate_sql, {
                "report_id": primary_id,
                "status": "VERIFIED",
                "disaster_id": disaster_id,
                "reviewed_by_id": review.reviewed_by_id,
                "reviewed_at": now,
                "updated_at": now,
            })
            if not gate_result.first():
                raise HTTPException(
                    status_code=409,
                    detail="This cluster was already reviewed by a concurrent request."
                )

            # Link remaining reports in the cluster (skip primary — already updated above)
            for rid in report_ids[1:]:
                update_sql = text("""
                    UPDATE disaster_reports
                    SET report_status = CAST(:status AS disaster_report_status),
                        disaster_id = :disaster_id,
                        reviewed_by_id = :reviewed_by_id,
                        reviewed_at = :reviewed_at,
                        updated_at = :updated_at
                    WHERE id = :report_id
                      AND report_status = CAST('PENDING' AS disaster_report_status)
                """)
                await self.db.execute(update_sql, {
                    "report_id": rid,
                    "status": "VERIFIED",
                    "disaster_id": disaster_id,
                    "reviewed_by_id": review.reviewed_by_id,
                    "reviewed_at": now,
                    "updated_at": now,
                })

            await self.db.flush()

            logger.info(
                f"Cluster of {len(report_ids)} reports VERIFIED → Disaster {disaster_id} "
                f"(tracking_id={tracking_id})"
            )

            # FIX #8: Return event payload — publish AFTER commit in the API layer.
            pending_event = {
                "disaster_id": disaster_id,
                "tracking_id": tracking_id,
                "type": str(primary["disaster_type"]).upper(),
                "severity": str(primary["severity"]).upper(),
                "location": {"lat": float(primary["latitude"]), "lon": float(primary["longitude"])},
                "location_address": primary["location_address"],
                "description": primary["description"],
                "people_affected": agg["max_people"] or 0,
                "multiple_casualties": agg["any_casualties"] or False,
                "structural_damage": agg["any_damage"] or False,
                "road_blocked": agg["any_blocked"] or False,
                "assigned_to_id": review.reviewed_by_id,
                "assigned_department": team_info["department"] if team_info else None,
                "created_by_id": review.reviewed_by_id,
            }

            return {
                "action": "verified",
                "reports_updated": len(report_ids),
                "disaster_id": disaster_id,
                "tracking_id": tracking_id,
                "reviewed_by_id": review.reviewed_by_id,
                "reviewed_at": now.isoformat(),
                "assigned_to": team_info["full_name"] if team_info else None,
                "assigned_department": team_info["department"] if team_info else None,
                "message": f"Cluster verified. {len(report_ids)} reports linked to disaster {tracking_id}, assigned to {team_info['full_name']} ({team_info['department']})" if team_info else f"Cluster verified. {len(report_ids)} reports linked to disaster {tracking_id}",
                "_pending_event": pending_event,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Error reviewing cluster: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to review cluster: {str(e)}"
            )

    # ──────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────
    async def _generate_tracking_id(self) -> str:
        """
        Generate a unique tracking ID using a DB sequence — e.g. DIS-2026-00001.

        FIX #7: The old COUNT(*)+1 approach produced duplicate IDs when two reports
        were verified concurrently (both read the same count). A sequence is atomic
        by definition — each caller gets a strictly unique, monotonically increasing
        number with no gaps under concurrent load.

        The sequence is created on first use via CREATE SEQUENCE IF NOT EXISTS,
        so no migration is required as long as the DB user has CREATE privilege.
        """
        year = datetime.utcnow().year
        seq_name = f"disaster_tracking_seq_{year}"

        # Ensure the sequence exists (idempotent)
        await self.db.execute(text(f"""
            CREATE SEQUENCE IF NOT EXISTS {seq_name} START 1
        """))

        result = await self.db.execute(text(f"SELECT nextval('{seq_name}')"))
        seq = result.scalar()
        return f"DIS-{year}-{seq:05d}"

    async def _get_team_info(self, team_id: str) -> Dict[str, Any]:
        """Fetch emergency team member's name and department."""
        sql = text("""
            SELECT id, full_name, department FROM emergency_teams
            WHERE id = :team_id AND deleted_at IS NULL
        """)
        result = await self.db.execute(sql, {"team_id": team_id})
        row = result.mappings().first()
        if not row:
            return None
        return {"id": str(row["id"]), "full_name": row["full_name"], "department": str(row["department"])}

    async def _get_report_dict(self, report_id: str) -> Dict[str, Any]:
        """Fetch a single report as dict using raw SQL."""
        sql = text("""
            SELECT
                r.id, r.user_id, r.disaster_type, r.severity,
                r.description, r.location_address,
                ST_Y(r.location::geometry) as latitude,
                ST_X(r.location::geometry) as longitude,
                r.people_affected, r.multiple_casualties,
                r.structural_damage, r.road_blocked,
                r.report_status, r.disaster_id, r.reviewed_by_id,
                r.reviewed_at, r.rejection_reason, r.created_at,
                (SELECT COUNT(*) FROM disaster_photos p WHERE p.disaster_report_id = r.id) as photo_count
            FROM disaster_reports r
            WHERE r.id = :report_id AND r.deleted_at IS NULL
        """)

        result = await self.db.execute(sql, {"report_id": report_id})
        row = result.mappings().first()

        if not row:
            return None

        return self._row_to_report_dict(row)

    def _row_to_report_dict(self, row) -> Dict[str, Any]:
        """Convert a raw SQL row to a report dictionary."""
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "disaster_type": str(row["disaster_type"]),
            "severity": str(row["severity"]),
            "description": row["description"],
            "location": {
                "lat": float(row["latitude"]) if row["latitude"] else None,
                "lon": float(row["longitude"]) if row["longitude"] else None,
            },
            "location_address": row["location_address"],
            "people_affected": row["people_affected"],
            "multiple_casualties": row["multiple_casualties"],
            "structural_damage": row["structural_damage"],
            "road_blocked": row["road_blocked"],
            "report_status": str(row["report_status"]),
            "disaster_id": str(row["disaster_id"]) if row["disaster_id"] else None,
            "reviewed_by_id": str(row["reviewed_by_id"]) if row["reviewed_by_id"] else None,
            "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
            "rejection_reason": row["rejection_reason"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "photo_count": row["photo_count"] or 0,
        }