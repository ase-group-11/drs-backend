# File: app/api/v1/user_management.py
"""
User Management API — Admin CRUD for Citizens + Emergency Team

Combined view of both user types (citizens from `users` table,
ERT members from `emergency_teams` table) in a single router.

Also serves as the team member picker for unit creation:
  - Filter by department or unit_type (auto-mapped)
  - Show assignment status (which units they're already on)
  - exclude_assigned=true hides members already in a unit

Auth: all endpoints require emergency team Bearer token (admin role).

Unit type → department mapping:
  FIRE_ENGINE / RESCUE / HAZMAT → FIRE
  AMBULANCE / RAPID_RESPONSE    → MEDICAL
  PATROL_CAR                    → POLICE
  COMMAND                       → IT
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_team_member
from app.db.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["User Management"])


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

UNIT_TYPE_TO_DEPARTMENT: Dict[str, str] = {
    "FIRE_ENGINE":    "FIRE",
    "RESCUE":         "FIRE",
    "HAZMAT":         "FIRE",
    "AMBULANCE":      "MEDICAL",
    "RAPID_RESPONSE": "MEDICAL",
    "PATROL_CAR":     "POLICE",
    "COMMAND":        "IT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Request schemas
# ─────────────────────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    """Body for POST /users/ — create a citizen or team member"""
    user_type:    str           = Field(..., description="citizen | team")
    full_name:    str           = Field(..., min_length=2, max_length=255)
    email:        str           = Field(...)
    phone_number: str           = Field(..., description="E.164 format e.g. +353861234567")
    # Team member fields (required when user_type=team)
    password:     Optional[str] = Field(None, description="Required for team members")
    role:         Optional[str] = Field(None, description="ADMIN | MANAGER | STAFF — team only")
    department:   Optional[str] = Field(None, description="FIRE | MEDICAL | POLICE | IT — team only")
    employee_id:  Optional[str] = Field(None, description="Employee ID — team only")


class UpdateUserRequest(BaseModel):
    """Body for PUT /users/{user_id} — update profile fields and/or status"""
    full_name:    Optional[str] = Field(None, min_length=2, max_length=255)
    email:        Optional[str] = Field(None)
    phone_number: Optional[str] = Field(None, description="E.164 format")
    status:       Optional[str] = Field(None, description="ACTIVE | INACTIVE | SUSPENDED | PENDING | DELETED")
    reason:       Optional[str] = Field(None, description="Optional note recorded in audit log")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import re
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/",
    summary="List all users — citizens and ERT members combined",
)
async def list_all_users(
    user_type:        Optional[str] = Query(None, description="Filter: citizen | team"),
    department:       Optional[str] = Query(None, description="Filter team by department: FIRE | MEDICAL | POLICE | IT"),
    unit_type:        Optional[str] = Query(None, description="Auto-maps to department (for unit creation picker)"),
    role:             Optional[str] = Query(None, description="Filter team by role: ADMIN | MANAGER | STAFF"),
    status_filter:    Optional[str] = Query(None, alias="status", description="ACTIVE | INACTIVE | SUSPENDED"),
    search:           Optional[str] = Query(None, description="Search name, email, phone, or employee ID"),
    exclude_assigned: bool          = Query(False, description="Exclude team members already assigned to a unit"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Combined list of citizens and ERT members.

    Primary use cases:
      1. Admin user list — filter by user_type, status, or search term
      2. Unit creation crew picker — use unit_type to auto-filter by department,
         and exclude_assigned=true to hide members already in units

    Returns a summary breakdown (citizens / team / active / inactive) and
    a by-department count for team members.
    Requires: emergency team Bearer token.
    """
    try:
        # Auto-map unit_type → department for the crew picker flow
        effective_department = department
        if unit_type and not department:
            effective_department = UNIT_TYPE_TO_DEPARTMENT.get(unit_type.upper())

        results: List[Dict] = []

        # ── Citizens ──────────────────────────────────────────────────────────
        if user_type is None or user_type == "citizen":
            if not effective_department and not role and not exclude_assigned:
                citizen_where  = ["u.email IS NOT NULL"]
                citizen_params: Dict[str, Any] = {"limit": limit}

                if status_filter:
                    citizen_where.append("u.status = CAST(:status AS user_status)")
                    citizen_params["status"] = status_filter.upper()
                if search:
                    citizen_where.append(
                        "(u.full_name ILIKE :search OR u.email ILIKE :search OR u.phone_number ILIKE :search)"
                    )
                    citizen_params["search"] = f"%{search}%"

                citizen_sql = text(f"""
                    SELECT u.id, u.full_name, u.email, u.phone_number,
                           u.role, u.status, u.created_at,
                           'citizen' AS user_type,
                           NULL AS department, NULL AS employee_id,
                           0 AS reviews_count,
                           FALSE AS is_assigned,
                           0 AS assigned_units_count,
                           0 AS commanding_units_count,
                           ARRAY[]::text[] AS current_unit_codes
                    FROM users u
                    WHERE {' AND '.join(citizen_where)}
                    ORDER BY u.created_at DESC
                    LIMIT :limit
                """)
                citizen_rows = await db.execute(citizen_sql, citizen_params)
                for row in citizen_rows.mappings().all():
                    results.append({
                        "id":           str(row["id"]),
                        "full_name":    row["full_name"],
                        "email":        row["email"],
                        "phone_number": row["phone_number"],
                        "role":         str(row["role"]) if row["role"] else None,
                        "status":       str(row["status"]),
                        "user_type":    "citizen",
                        "department":   None,
                        "employee_id":  None,
                        "stats": {
                            "reviews_count":         0,
                            "assigned_units_count":  0,
                            "commanding_units_count":0,
                        },
                        "current_unit_codes": [],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    })

        # ── Team members ──────────────────────────────────────────────────────
        if user_type is None or user_type == "team":
            team_where  = ["et.deleted_at IS NULL"]
            team_params: Dict[str, Any] = {"limit": limit}

            if effective_department:
                team_where.append("et.department = CAST(:dept AS department)")
                team_params["dept"] = effective_department.upper()
            if role:
                team_where.append("et.role = CAST(:role AS emergency_team_role)")
                team_params["role"] = role.upper()
            if status_filter:
                team_where.append("et.status = CAST(:status AS user_status)")
                team_params["status"] = status_filter.upper()
            if search:
                team_where.append(
                    "(et.full_name ILIKE :search OR et.email ILIKE :search "
                    "OR et.phone_number ILIKE :search OR et.employee_id ILIKE :search)"
                )
                team_params["search"] = f"%{search}%"
            if exclude_assigned:
                team_where.append(
                    "NOT EXISTS (SELECT 1 FROM unit_crew uc "
                    "JOIN emergency_units eu ON uc.unit_id = eu.id "
                    "WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL)"
                )

            team_sql = text(f"""
                SELECT
                    et.id, et.full_name, et.email, et.phone_number,
                    et.role, et.status, et.department, et.employee_id,
                    et.created_at,
                    'team' as user_type,
                    (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.reviewed_by_id = et.id) as reviews_count,
                    (SELECT COUNT(*) FROM unit_crew uc JOIN emergency_units eu ON uc.unit_id = eu.id WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL) as assigned_units_count,
                    (SELECT COUNT(*) FROM emergency_units eu WHERE eu.commander_id = et.id AND eu.deleted_at IS NULL) as commanding_units_count,
                    (SELECT ARRAY_AGG(eu.unit_code) FROM unit_crew uc
                     JOIN emergency_units eu ON uc.unit_id = eu.id
                     WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL) as current_unit_codes
                FROM emergency_teams et
                LEFT JOIN disaster_reports dr ON dr.reviewed_by_id = et.id
                LEFT JOIN unit_crew uc ON uc.team_member_id = et.id
                LEFT JOIN emergency_units eu ON uc.unit_id = eu.id AND eu.deleted_at IS NULL
                LEFT JOIN emergency_units eu_cmd ON eu_cmd.commander_id = et.id AND eu_cmd.deleted_at IS NULL
                WHERE {' AND '.join(team_where)}
                GROUP BY et.id, et.full_name, et.email, et.phone_number,
                         et.role, et.status, et.department, et.employee_id, et.created_at
                ORDER BY et.full_name ASC
                LIMIT :limit
            """)
            team_rows = await db.execute(team_sql, team_params)
            for row in team_rows.mappings().all():
                is_assigned = int(row["assigned_units_count"] or 0) > 0
                results.append({
                    "id":           str(row["id"]),
                    "full_name":    row["full_name"],
                    "email":        row["email"],
                    "phone_number": row["phone_number"],
                    "role":         str(row["role"]),
                    "status":       str(row["status"]),
                    "user_type":    "team",
                    "department":   str(row["department"]),
                    "employee_id":  row["employee_id"],
                    "stats": {
                        "reviews_count":         int(row["reviews_count"] or 0),
                        "assigned_units_count":  int(row["assigned_units_count"] or 0),
                        "commanding_units_count":int(row["commanding_units_count"] or 0),
                    },
                    "is_assigned":       is_assigned,
                    "current_unit_codes":row["current_unit_codes"] or [],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

        citizens = [u for u in results if u["user_type"] == "citizen"]
        team     = [u for u in results if u["user_type"] == "team"]

        return {
            "users":       results,
            "total_count": len(results),
            "summary": {
                "citizens":  len(citizens),
                "team":      len(team),
                "active":    sum(1 for u in results if u["status"] == "ACTIVE"),
                "inactive":  sum(1 for u in results if u["status"] != "ACTIVE"),
            },
            "by_department": {
                dept: sum(1 for u in team if u["department"] == dept)
                for dept in set(u["department"] for u in team if u["department"])
            },
            "filters_applied": {
                "user_type":        user_type,
                "department":       effective_department,
                "unit_type":        unit_type,
                "role":             role,
                "search":           search,
                "exclude_assigned": exclude_assigned,
            },
        }

    except Exception as exc:
        logger.exception(f"list_all_users failed")
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(exc)[:200]}")


@router.get(
    "/{user_id}",
    summary="Get user details — auto-detects citizen or team member",
)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Returns full profile for a single user.
    Checks `users` table first, then `emergency_teams` — caller doesn't
    need to know which table the user belongs to.
    Includes stats (reviews, unit assignments) for team members.
    Requires: emergency team Bearer token.
    """
    try:
        # ── Try citizens first ────────────────────────────────────────────────
        citizen_result = await db.execute(
            text("""
                SELECT id, full_name, email, phone_number, role, status, created_at, updated_at
                FROM users WHERE id = :uid AND deleted_at IS NULL
            """),
            {"uid": user_id},
        )
        citizen = citizen_result.mappings().first()
        if citizen:
            return {
                "id":           str(citizen["id"]),
                "full_name":    citizen["full_name"],
                "email":        citizen["email"],
                "phone_number": citizen["phone_number"],
                "role":         str(citizen["role"]) if citizen["role"] else None,
                "status":       str(citizen["status"]),
                "user_type":    "citizen",
                "created_at":   citizen["created_at"].isoformat() if citizen["created_at"] else None,
                "updated_at":   citizen["updated_at"].isoformat() if citizen["updated_at"] else None,
            }

        team_sql = text("""
            SELECT
                et.id, et.full_name, et.email, et.phone_number,
                et.role, et.status, et.department, et.employee_id,
                et.created_at, et.updated_at,
                (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.reviewed_by_id = et.id) as reviews_count,
                (SELECT COUNT(*) FROM unit_crew uc JOIN emergency_units eu ON uc.unit_id = eu.id WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL) as assigned_units,
                (SELECT COUNT(*) FROM emergency_units eu WHERE eu.commander_id = et.id AND eu.deleted_at IS NULL) as commanding_units,
                (SELECT ARRAY_AGG(eu.unit_code) FROM unit_crew uc
                 JOIN emergency_units eu ON uc.unit_id = eu.id
                 WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL) as unit_codes
            FROM emergency_teams et
            WHERE et.id = :user_id AND et.deleted_at IS NULL
        """)
        result = await db.execute(team_sql, {"user_id": user_id})
        row = result.mappings().first()

        if row:
            return {
                "id":           str(team["id"]),
                "full_name":    team["full_name"],
                "email":        team["email"],
                "phone_number": team["phone_number"],
                "role":         str(team["role"]),
                "status":       str(team["status"]),
                "user_type":    "team",
                "department":   str(team["department"]),
                "employee_id":  team["employee_id"],
                "stats": {
                    "reviews_count":  int(team["reviews_count"] or 0),
                    "assigned_units": int(team["assigned_units"] or 0),
                    "commanding_units": int(team["commanding_units"] or 0),
                },
                "unit_codes": team["unit_codes"] or [],
                "created_at": team["created_at"].isoformat() if team["created_at"] else None,
                "updated_at": team["updated_at"].isoformat() if team["updated_at"] else None,
            }

        raise HTTPException(status_code=404, detail="User not found.")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"get_user failed")
        raise HTTPException(status_code=500, detail=f"Failed to fetch user: {str(exc)[:200]}")


@router.put(
    "/{user_id}",
    summary="Update user profile and/or status",
)
async def update_user(
    user_id: str,
    data: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    """
    Updates any combination of full_name, email, phone_number, and/or status
    for a citizen or ERT team member. Auto-detects which table to update.
    Only non-null fields in the request body are applied.
    Duplicate email / phone validation is enforced per table.
    Requires: emergency team Bearer token.
    """
    from datetime import datetime

    if all(v is None for v in [data.full_name, data.email, data.phone_number, data.status]):
        raise HTTPException(
            status_code=400,
            detail="Provide at least one field to update: full_name, email, phone_number, or status.",
        )

    try:
        now = datetime.utcnow()

        # ── Try citizens table first ──────────────────────────────────────────
        result = await db.execute(
            text("SELECT id, full_name, email, phone_number, status FROM users WHERE id = :id AND deleted_at IS NULL"),
            {"id": user_id},
        )
        user = result.mappings().first()

        if user:
            if data.email:
                dup = await db.execute(
                    text("SELECT id FROM users WHERE email = :email AND id != :id AND deleted_at IS NULL"),
                    {"email": data.email, "id": user_id},
                )
                if dup.first():
                    raise HTTPException(status_code=400, detail=f"Email {data.email} is already in use.")
            if data.phone_number:
                dup = await db.execute(
                    text("SELECT id FROM users WHERE phone_number = :phone AND id != :id AND deleted_at IS NULL"),
                    {"phone": data.phone_number, "id": user_id},
                )
                if dup.first():
                    raise HTTPException(status_code=400, detail=f"Phone {data.phone_number} is already in use.")

            set_clauses = ["updated_at = :now"]
            params: Dict[str, Any] = {"id": user_id, "now": now}
            if data.full_name:
                set_clauses.append("full_name = :full_name"); params["full_name"] = data.full_name
            if data.email:
                set_clauses.append("email = :email"); params["email"] = data.email
            if data.phone_number:
                set_clauses.append("phone_number = :phone"); params["phone"] = data.phone_number
            if data.status:
                set_clauses.append("status = CAST(:status AS user_status)"); params["status"] = data.status.upper()

            await db.execute(text(f"UPDATE users SET {', '.join(set_clauses)} WHERE id = :id"), params)
            await db.flush()
            return {"id": user_id, "user_type": "citizen", "message": "User updated successfully.", "updated_fields": list(params.keys() - {"id", "now"})}

        # ── Try team members table ────────────────────────────────────────────
        result = await db.execute(
            text("SELECT id, full_name, email, phone_number, status FROM emergency_teams WHERE id = :id AND deleted_at IS NULL"),
            {"id": user_id},
        )
        team = result.mappings().first()

        if team:
            if data.email:
                dup = await db.execute(
                    text("SELECT id FROM emergency_teams WHERE email = :email AND id != :id AND deleted_at IS NULL"),
                    {"email": data.email, "id": user_id},
                )
                if dup.first():
                    raise HTTPException(status_code=400, detail=f"Email {data.email} is already in use.")

            set_clauses = ["updated_at = :now"]
            params = {"id": user_id, "now": now}
            if data.full_name:
                set_clauses.append("full_name = :full_name"); params["full_name"] = data.full_name
            if data.email:
                set_clauses.append("email = :email"); params["email"] = data.email
            if data.phone_number:
                set_clauses.append("phone_number = :phone"); params["phone"] = data.phone_number
            if data.status:
                set_clauses.append("status = CAST(:status AS user_status)"); params["status"] = data.status.upper()

            await db.execute(text(f"UPDATE emergency_teams SET {', '.join(set_clauses)} WHERE id = :id"), params)
            await db.flush()
            return {"id": user_id, "user_type": "team", "message": "Team member updated successfully.", "updated_fields": list(params.keys() - {"id", "now"})}

        raise HTTPException(status_code=404, detail="User not found.")

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(f"update_user failed")
        raise HTTPException(status_code=500, detail=f"Failed to update user: {str(exc)[:200]}")