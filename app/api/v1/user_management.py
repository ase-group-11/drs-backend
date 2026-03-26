# File: app/api/v1/user_management.py
"""
User Management API — Combined CRUD for Citizens + Emergency Team.

Also handles team member listing for unit creation:
  - Filter by department / unit_type
  - Show assignment status
  - Department mapping for unit types

This replaces the separate emergency_team_list.py file.
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

from app.db.session import get_db
from app.auth.dependencies import get_current_team_member
from sqlalchemy import text

import logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["User Management"])


# ── Unit Type → Department Mapping ──
UNIT_TYPE_TO_DEPARTMENT = {
    "FIRE_ENGINE": "FIRE",
    "AMBULANCE": "MEDICAL",
    "PATROL_CAR": "POLICE",
    "RESCUE": "FIRE",
    "HAZMAT": "FIRE",
    "RAPID_RESPONSE": "MEDICAL",
    "COMMAND": "IT",
}


class UpdateUserStatusRequest(BaseModel):
    status: str = Field(..., description="New status: ACTIVE, INACTIVE, SUSPENDED")
    reason: Optional[str] = Field(None, description="Reason for status change")


class CreateCitizenRequest(BaseModel):
    phone_number: str = Field(..., description="Phone number (E.164 format: +353851234567)")
    full_name: str = Field(..., description="Full name")
    email: Optional[str] = Field(None, description="Email address")


class CreateTeamMemberRequest(BaseModel):
    phone_number: str = Field(..., description="Phone number (E.164 format: +353851234567)")
    full_name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address (used for login)")
    password: str = Field(..., description="Password (min 8 chars)")
    role: str = Field("STAFF", description="Role: ADMIN, MANAGER, STAFF")
    department: str = Field(..., description="Department: FIRE, MEDICAL, POLICE, IT")
    employee_id: Optional[str] = Field(None, description="Employee ID")


class CreateUserRequest(BaseModel):
    user_type: str = Field(..., description="'citizen' or 'team'")
    phone_number: str = Field(..., description="Phone number")
    full_name: str = Field(..., description="Full name")
    email: Optional[str] = Field(None, description="Email (required for team)")
    password: Optional[str] = Field(None, description="Password (required for team)")
    role: Optional[str] = Field(None, description="Team role: ADMIN, MANAGER, STAFF")
    department: Optional[str] = Field(None, description="Team department: FIRE, MEDICAL, POLICE, IT")
    employee_id: Optional[str] = Field(None, description="Employee ID (team only)")


# ══════════════════════════════════════════════
# STATIC ROUTES FIRST
# ══════════════════════════════════════════════

@router.get(
    "/department-map",
    summary="Unit type to department mapping",
    description="Frontend uses this to auto-select department when admin picks unit type during unit creation."
)
async def get_department_map(
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    return {
        "unit_type_to_department": UNIT_TYPE_TO_DEPARTMENT,
        "departments": ["FIRE", "MEDICAL", "POLICE", "IT"],
        "unit_types": list(UNIT_TYPE_TO_DEPARTMENT.keys()),
    }


# ══════════════════════════════════════════════
# CREATE: New user (citizen or team member)
# ══════════════════════════════════════════════

@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user (citizen or team member)",
    description=(
        "Creates a citizen or emergency team member directly. "
        "Bypasses OTP flow — used by admin to add users. "
        "For team members: email, password, role, and department are required."
    )
)
async def create_user(
    data: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    try:
        import uuid
        from datetime import datetime
        from argon2 import PasswordHasher

        now = datetime.utcnow()
        user_id = str(uuid.uuid4())

        if data.user_type == "citizen":
            # ── Create Citizen ──
            # Check phone uniqueness
            check = await db.execute(
                text("SELECT id FROM users WHERE phone_number = :phone AND deleted_at IS NULL"),
                {"phone": data.phone_number}
            )
            if check.first():
                raise HTTPException(status_code=400, detail=f"Phone number {data.phone_number} already registered.")

            # Check email uniqueness if provided
            if data.email:
                check = await db.execute(
                    text("SELECT id FROM users WHERE email = :email AND deleted_at IS NULL"),
                    {"email": data.email}
                )
                if check.first():
                    raise HTTPException(status_code=400, detail=f"Email {data.email} already registered.")

            await db.execute(text("""
                INSERT INTO users (id, created_at, updated_at, phone_number, full_name, email, role, status)
                VALUES (:id, :now, :now, :phone, :name, :email,
                        CAST('RESIDENT' AS user_role), CAST('ACTIVE' AS user_status))
            """), {
                "id": user_id, "now": now,
                "phone": data.phone_number,
                "name": data.full_name,
                "email": data.email,
            })

            await db.flush()

            return {
                "id": user_id,
                "full_name": data.full_name,
                "email": data.email,
                "phone_number": data.phone_number,
                "user_type": "citizen",
                "role": "RESIDENT",
                "status": "ACTIVE",
                "message": f"Citizen {data.full_name} created successfully.",
            }

        elif data.user_type == "team":
            # ── Create Team Member ──
            if not data.email:
                raise HTTPException(status_code=400, detail="Email is required for team members.")
            if not data.password:
                raise HTTPException(status_code=400, detail="Password is required for team members.")
            if not data.department:
                raise HTTPException(status_code=400, detail="Department is required for team members.")
            if len(data.password) < 8:
                raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

            role = (data.role or "STAFF").upper()
            department = data.department.upper()

            if role not in ("ADMIN", "MANAGER", "STAFF"):
                raise HTTPException(status_code=400, detail="Role must be ADMIN, MANAGER, or STAFF.")
            if department not in ("FIRE", "MEDICAL", "POLICE", "IT"):
                raise HTTPException(status_code=400, detail="Department must be FIRE, MEDICAL, POLICE, or IT.")

            # Check phone uniqueness
            check = await db.execute(
                text("SELECT id FROM emergency_teams WHERE phone_number = :phone AND deleted_at IS NULL"),
                {"phone": data.phone_number}
            )
            if check.first():
                raise HTTPException(status_code=400, detail=f"Phone number {data.phone_number} already registered.")

            # Check email uniqueness
            check = await db.execute(
                text("SELECT id FROM emergency_teams WHERE email = :email AND deleted_at IS NULL"),
                {"email": data.email}
            )
            if check.first():
                raise HTTPException(status_code=400, detail=f"Email {data.email} already registered.")

            # Check employee_id uniqueness
            if data.employee_id:
                check = await db.execute(
                    text("SELECT id FROM emergency_teams WHERE employee_id = :emp_id AND deleted_at IS NULL"),
                    {"emp_id": data.employee_id}
                )
                if check.first():
                    raise HTTPException(status_code=400, detail=f"Employee ID {data.employee_id} already exists.")

            ph = PasswordHasher()
            password_hash = ph.hash(data.password)

            await db.execute(text("""
                INSERT INTO emergency_teams
                    (id, created_at, updated_at, phone_number, password_hash,
                     full_name, email, employee_id, role, department, status)
                VALUES (:id, :now, :now, :phone, :pw,
                        :name, :email, :emp_id,
                        CAST(:role AS emergency_team_role),
                        CAST(:dept AS department),
                        CAST('ACTIVE' AS user_status))
            """), {
                "id": user_id, "now": now,
                "phone": data.phone_number,
                "pw": password_hash,
                "name": data.full_name,
                "email": data.email,
                "emp_id": data.employee_id,
                "role": role,
                "dept": department,
            })

            await db.flush()

            return {
                "id": user_id,
                "full_name": data.full_name,
                "email": data.email,
                "phone_number": data.phone_number,
                "user_type": "team",
                "role": role,
                "department": department,
                "employee_id": data.employee_id,
                "status": "ACTIVE",
                "message": f"Team member {data.full_name} ({role}/{department}) created successfully.",
            }

        else:
            raise HTTPException(status_code=400, detail="user_type must be 'citizen' or 'team'.")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)[:200]}")


# ══════════════════════════════════════════════
# LIST: All users (combined citizens + team)
# ══════════════════════════════════════════════

@router.get(
    "/",
    summary="List all users (citizens + emergency team)",
    description=(
        "Combined view of all users. Filters: user_type, department, role, status, search. "
        "For unit creation: use unit_type param to auto-filter by department, "
        "and exclude_assigned=true to hide members already in units."
    )
)
async def list_all_users(
    user_type: Optional[str] = Query(None, description="Filter: 'citizen' or 'team'"),
    department: Optional[str] = Query(None, description="Filter team by department: FIRE, MEDICAL, POLICE, IT"),
    unit_type: Optional[str] = Query(None, description="Auto-map to department: FIRE_ENGINE → FIRE (for unit creation)"),
    role: Optional[str] = Query(None, description="Filter team by role: ADMIN, MANAGER, STAFF"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter: ACTIVE, INACTIVE, SUSPENDED"),
    search: Optional[str] = Query(None, description="Search by name, email, phone, or employee ID"),
    exclude_assigned: bool = Query(False, description="Exclude team members already assigned to a unit"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    try:
        # Auto-map unit_type to department
        effective_department = department
        if unit_type and not department:
            effective_department = UNIT_TYPE_TO_DEPARTMENT.get(unit_type.upper())

        results = []

        # ── Citizens ──
        if user_type is None or user_type == "citizen":
            # Skip citizens if filtering by department/unit_type/role/exclude_assigned (team-only filters)
            if not effective_department and not role and not exclude_assigned:
                citizen_where = ["u.email IS NOT NULL"]
                citizen_params = {"limit": limit}

                if status_filter:
                    citizen_where.append("u.status = CAST(:status AS user_status)")
                    citizen_params["status"] = status_filter.upper()

                if search:
                    citizen_where.append("(u.full_name ILIKE :search OR u.email ILIKE :search OR u.phone_number ILIKE :search)")
                    citizen_params["search"] = f"%{search}%"

                citizen_sql = text(f"""
                    SELECT
                        u.id, u.full_name, u.email, u.phone_number,
                        u.role, u.status, u.created_at,
                        'citizen' as user_type,
                        NULL as department, NULL as employee_id,
                        (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.user_id = u.id AND dr.deleted_at IS NULL) as reports_count
                    FROM users u
                    WHERE {' AND '.join(citizen_where)}
                    ORDER BY u.created_at DESC
                    LIMIT :limit
                """)

                result = await db.execute(citizen_sql, citizen_params)
                for row in result.mappings().all():
                    results.append({
                        "id": str(row["id"]),
                        "full_name": row["full_name"],
                        "email": row["email"],
                        "phone_number": row["phone_number"],
                        "role": str(row["role"]),
                        "status": str(row["status"]),
                        "user_type": "citizen",
                        "department": None,
                        "employee_id": None,
                        "reports_count": row["reports_count"] or 0,
                        "is_assigned": False,
                        "assigned_units_count": 0,
                        "commanding_units_count": 0,
                        "current_unit_codes": [],
                        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                    })

        # ── Emergency Team ──
        if user_type is None or user_type == "team":
            team_where = ["et.email IS NOT NULL"]
            team_params = {"limit": limit}

            if status_filter:
                team_where.append("et.status = CAST(:status AS user_status)")
                team_params["status"] = status_filter.upper()

            if effective_department:
                team_where.append("et.department = CAST(:department AS department)")
                team_params["department"] = effective_department.upper()

            if role:
                team_where.append("et.role = CAST(:role AS emergency_team_role)")
                team_params["role"] = role.upper()

            if search:
                team_where.append("(et.full_name ILIKE :search OR et.email ILIKE :search OR et.phone_number ILIKE :search OR et.employee_id ILIKE :search)")
                team_params["search"] = f"%{search}%"

            team_sql = text(f"""
                SELECT
                    et.id, et.full_name, et.email, et.phone_number,
                    et.role, et.status, et.department, et.employee_id,
                    et.created_at,
                    'team' as user_type,
                    (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.reviewed_by_id = et.id) as reviews_count,
                    (SELECT COUNT(*) FROM unit_crew uc WHERE uc.team_member_id = et.id) as assigned_units_count,
                    (SELECT COUNT(*) FROM emergency_units eu WHERE eu.commander_id = et.id AND eu.deleted_at IS NULL) as commanding_units_count,
                    (SELECT ARRAY_AGG(eu.unit_code) FROM unit_crew uc
                     JOIN emergency_units eu ON uc.unit_id = eu.id
                     WHERE uc.team_member_id = et.id AND eu.deleted_at IS NULL) as current_unit_codes
                FROM emergency_teams et
                WHERE {' AND '.join(team_where)}
                ORDER BY et.department, et.role, et.full_name
                LIMIT :limit
            """)

            result = await db.execute(team_sql, team_params)
            for row in result.mappings().all():
                is_assigned = (row["assigned_units_count"] or 0) > 0

                # Skip if exclude_assigned and already assigned
                if exclude_assigned and is_assigned:
                    continue

                results.append({
                    "id": str(row["id"]),
                    "full_name": row["full_name"],
                    "email": row["email"],
                    "phone_number": row["phone_number"],
                    "role": str(row["role"]),
                    "status": str(row["status"]),
                    "user_type": "team",
                    "department": str(row["department"]),
                    "employee_id": row["employee_id"],
                    "reviews_count": row["reviews_count"] or 0,
                    "is_assigned": is_assigned,
                    "assigned_units_count": row["assigned_units_count"] or 0,
                    "commanding_units_count": row["commanding_units_count"] or 0,
                    "current_unit_codes": row["current_unit_codes"] or [],
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                })

        # Summary
        citizens = [u for u in results if u["user_type"] == "citizen"]
        team = [u for u in results if u["user_type"] == "team"]

        return {
            "users": results,
            "total_count": len(results),
            "summary": {
                "citizens": len(citizens),
                "team_members": len(team),
                "active": sum(1 for u in results if u["status"] == "ACTIVE"),
                "inactive": sum(1 for u in results if u["status"] != "ACTIVE"),
            },
            "by_department": {
                dept: sum(1 for u in team if u["department"] == dept)
                for dept in set(u["department"] for u in team if u["department"])
            },
            "filtered_by": {
                "user_type": user_type,
                "department": effective_department,
                "unit_type": unit_type,
                "role": role,
                "search": search,
                "exclude_assigned": exclude_assigned,
            },
        }

    except Exception as e:
        logger.exception(f"Error listing users: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list users: {str(e)[:200]}")


# ══════════════════════════════════════════════
# GET: Single user (auto-detect citizen or team)
# ══════════════════════════════════════════════

@router.get(
    "/{user_id}",
    summary="Get user details",
    description="Get details for any user. Auto-detects if citizen or team member. Returns stats."
)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    try:
        # Try citizens first
        citizen_sql = text("""
            SELECT
                u.id, u.full_name, u.email, u.phone_number,
                u.role, u.status, u.created_at, u.updated_at,
                (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.user_id = u.id AND dr.deleted_at IS NULL) as reports_count,
                (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.user_id = u.id AND dr.report_status = CAST('VERIFIED' AS disaster_report_status)) as verified_reports,
                (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.user_id = u.id AND dr.report_status = CAST('REJECTED' AS disaster_report_status)) as rejected_reports
            FROM users u
            WHERE u.id = :user_id AND u.deleted_at IS NULL
        """)

        result = await db.execute(citizen_sql, {"user_id": user_id})
        row = result.mappings().first()

        if row:
            return {
                "id": str(row["id"]),
                "full_name": row["full_name"],
                "email": row["email"],
                "phone_number": row["phone_number"],
                "role": str(row["role"]),
                "status": str(row["status"]),
                "user_type": "citizen",
                "department": None,
                "employee_id": None,
                "stats": {
                    "total_reports": row["reports_count"] or 0,
                    "verified_reports": row["verified_reports"] or 0,
                    "rejected_reports": row["rejected_reports"] or 0,
                },
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

        # Try emergency team
        team_sql = text("""
            SELECT
                et.id, et.full_name, et.email, et.phone_number,
                et.role, et.status, et.department, et.employee_id,
                et.created_at, et.updated_at,
                (SELECT COUNT(*) FROM disaster_reports dr WHERE dr.reviewed_by_id = et.id) as reviews_count,
                (SELECT COUNT(*) FROM unit_crew uc WHERE uc.team_member_id = et.id) as assigned_units,
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
                "id": str(row["id"]),
                "full_name": row["full_name"],
                "email": row["email"],
                "phone_number": row["phone_number"],
                "role": str(row["role"]),
                "status": str(row["status"]),
                "user_type": "team",
                "department": str(row["department"]),
                "employee_id": row["employee_id"],
                "stats": {
                    "reviews_count": row["reviews_count"] or 0,
                    "assigned_units": row["assigned_units"] or 0,
                    "commanding_units": row["commanding_units"] or 0,
                },
                "unit_codes": row["unit_codes"] or [],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }

        raise HTTPException(status_code=404, detail="User not found.")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch user: {str(e)[:200]}")


# ══════════════════════════════════════════════
# UPDATE: User status
# ══════════════════════════════════════════════

@router.put(
    "/{user_id}/status",
    summary="Update user status",
    description="Activate, deactivate, or suspend any user (citizen or team member)."
)
async def update_user_status(
    user_id: str,
    data: UpdateUserStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    try:
        from datetime import datetime
        now = datetime.utcnow()
        new_status = data.status.upper()
 
        valid_statuses = ("ACTIVE", "INACTIVE", "SUSPENDED", "PENDING", "DELETED")
        if new_status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Status must be one of: {', '.join(valid_statuses)}")
 
        # Try citizens first
        result = await db.execute(
            text("SELECT id, full_name, status FROM users WHERE id = :id"),
            {"id": user_id}
        )
        user = result.mappings().first()
 
        if user:
            if new_status == "DELETED":
                # Soft delete — also set deleted_at so email/phone can be reused
                await db.execute(text("""
                    UPDATE users SET status = CAST(:status AS user_status),
                        deleted_at = :now, updated_at = :now
                    WHERE id = :id
                """), {"id": user_id, "status": new_status, "now": now})
            else:
                # If reactivating a deleted user, clear deleted_at
                await db.execute(text("""
                    UPDATE users SET status = CAST(:status AS user_status),
                        deleted_at = NULL, updated_at = :now
                    WHERE id = :id
                """), {"id": user_id, "status": new_status, "now": now})
            await db.flush()
            return {
                "id": user_id, "full_name": user["full_name"], "user_type": "citizen",
                "previous_status": str(user["status"]), "new_status": new_status,
                "reason": data.reason, "message": f"{user['full_name']} → {new_status}",
            }
 
        # Try emergency team
        result = await db.execute(
            text("SELECT id, full_name, status, department FROM emergency_teams WHERE id = :id"),
            {"id": user_id}
        )
        team = result.mappings().first()
 
        if team:
            if new_status == "DELETED":
                await db.execute(text("""
                    UPDATE emergency_teams SET status = CAST(:status AS user_status),
                        deleted_at = :now, updated_at = :now
                    WHERE id = :id
                """), {"id": user_id, "status": new_status, "now": now})
                # Remove from unit crews
                await db.execute(text("DELETE FROM unit_crew WHERE team_member_id = :id"), {"id": user_id})
            else:
                await db.execute(text("""
                    UPDATE emergency_teams SET status = CAST(:status AS user_status),
                        deleted_at = NULL, updated_at = :now
                    WHERE id = :id
                """), {"id": user_id, "status": new_status, "now": now})
            await db.flush()
            return {
                "id": user_id, "full_name": team["full_name"], "user_type": "team",
                "department": str(team["department"]),
                "previous_status": str(team["status"]), "new_status": new_status,
                "reason": data.reason, "message": f"{team['full_name']} → {new_status}",
            }
 
        raise HTTPException(status_code=404, detail="User not found.")
 
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating user status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update status: {str(e)[:200]}")
 
 
# ══════════════════════════════════════════════
# DELETE: Soft delete user
# ══════════════════════════════════════════════
 
@router.delete(
    "/{user_id}",
    summary="Soft delete user",
    description="Soft delete any user. Blocks if team member is commanding units."
)
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_team_member),
):
    try:
        from datetime import datetime
        now = datetime.utcnow()
 
        # Try citizens
        result = await db.execute(
            text("SELECT id, full_name FROM users WHERE id = :id AND deleted_at IS NULL"),
            {"id": user_id}
        )
        user = result.mappings().first()
 
        if user:
            await db.execute(text("""
                UPDATE users SET deleted_at = :now, status = CAST('DELETED' AS user_status), updated_at = :now WHERE id = :id
            """), {"id": user_id, "now": now})
            await db.flush()
            return {"id": user_id, "full_name": user["full_name"], "user_type": "citizen", "status": "DELETED"}
 
        # Try emergency team
        result = await db.execute(
            text("SELECT id, full_name FROM emergency_teams WHERE id = :id AND deleted_at IS NULL"),
            {"id": user_id}
        )
        team = result.mappings().first()
 
        if team:
            cmd_check = await db.execute(
                text("SELECT COUNT(*) FROM emergency_units WHERE commander_id = :id AND deleted_at IS NULL"),
                {"id": user_id}
            )
            if cmd_check.scalar() > 0:
                raise HTTPException(status_code=400, detail="Cannot delete — commanding active unit(s). Reassign commander first.")
 
            await db.execute(text("""
                UPDATE emergency_teams SET deleted_at = :now, status = CAST('DELETED' AS user_status), updated_at = :now WHERE id = :id
            """), {"id": user_id, "now": now})
            await db.execute(text("DELETE FROM unit_crew WHERE team_member_id = :id"), {"id": user_id})
            await db.flush()
            return {"id": user_id, "full_name": team["full_name"], "user_type": "team", "status": "DELETED"}
 
        raise HTTPException(status_code=404, detail="User not found.")
 
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error deleting user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {str(e)[:200]}")
 