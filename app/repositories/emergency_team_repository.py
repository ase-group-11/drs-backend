# File: app/repositories/emergency_team_repository.py
"""
Emergency Team repository for team-specific database operations.

Extends BaseRepository with emergency team-specific queries.
"""

from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.emergency_team import EmergencyTeam
from app.models.enums import UserStatus, EmergencyTeamRole, Department
from app.repositories.base import BaseRepository


class EmergencyTeamRepository(BaseRepository[EmergencyTeam]):
    """
    Emergency team repository with team-specific queries.
    
    Provides CRUD operations plus emergency team-specific methods.
    """
    
    def __init__(self, session: AsyncSession):
        """Initialize emergency team repository."""
        super().__init__(EmergencyTeam, session)
    
    async def get_by_phone_number(self, phone_number: str) -> Optional[EmergencyTeam]:
        """
        Get emergency team member by phone number.
        
        Args:
            phone_number: Team member's phone number
            
        Returns:
            Optional[EmergencyTeam]: Team member instance or None
        """
        return await self.get_by_field("phone_number", phone_number)
    
    async def get_by_email(self, email: str) -> Optional[EmergencyTeam]:
        """
        Get emergency team member by email.
        
        Args:
            email: Team member's email address
            
        Returns:
            Optional[EmergencyTeam]: Team member instance or None
        """
        return await self.get_by_field("email", email)
    
    async def get_by_employee_id(self, employee_id: str) -> Optional[EmergencyTeam]:
        """
        Get emergency team member by employee ID.
        
        Args:
            employee_id: Employee ID
            
        Returns:
            Optional[EmergencyTeam]: Team member instance or None
        """
        return await self.get_by_field("employee_id", employee_id)
    
    async def phone_exists(self, phone_number: str) -> bool:
        """
        Check if phone number is already registered.
        
        Args:
            phone_number: Phone number to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        return await self.exists("phone_number", phone_number)
    
    async def email_exists(self, email: str) -> bool:
        """
        Check if email is already registered.
        
        Args:
            email: Email to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        return await self.exists("email", email)
    
    async def employee_id_exists(self, employee_id: str) -> bool:
        """
        Check if employee ID is already registered.
        
        Args:
            employee_id: Employee ID to check
            
        Returns:
            bool: True if exists, False otherwise
        """
        if not employee_id:
            return False
        return await self.exists("employee_id", employee_id)
    
    async def get_active_team_member_by_phone(
        self, 
        phone_number: str
    ) -> Optional[EmergencyTeam]:
        """
        Get active emergency team member by phone number.
        
        Only returns team member if status is ACTIVE.
        
        Args:
            phone_number: Team member's phone number
            
        Returns:
            Optional[EmergencyTeam]: Active team member or None
        """
        result = await self.session.execute(
            select(EmergencyTeam).where(
                EmergencyTeam.phone_number == phone_number,
                EmergencyTeam.status == UserStatus.ACTIVE,
                EmergencyTeam.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()
    
    async def get_active_team_member_by_email(
        self, 
        email: str
    ) -> Optional[EmergencyTeam]:
        """
        Get active emergency team member by email.
        
        Args:
            email: Team member's email
            
        Returns:
            Optional[EmergencyTeam]: Active team member or None
        """
        result = await self.session.execute(
            select(EmergencyTeam).where(
                EmergencyTeam.email == email,
                EmergencyTeam.status == UserStatus.ACTIVE,
                EmergencyTeam.deleted_at.is_(None)
            )
        )
        return result.scalar_one_or_none()
    
    async def get_by_department(
        self, 
        department: Department,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get all team members in a specific department.
        
        Args:
            department: Department to filter by
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of team members
        """
        return await self.get_all(
            skip=skip,
            limit=limit,
            filters={"department": department, "status": UserStatus.ACTIVE}
        )
    
    async def get_by_role(
        self,
        role: EmergencyTeamRole,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get all team members with a specific role.
        
        Args:
            role: Role to filter by
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of team members
        """
        return await self.get_all(
            skip=skip,
            limit=limit,
            filters={"role": role, "status": UserStatus.ACTIVE}
        )
    
    async def get_by_department_and_role(
        self,
        department: Department,
        role: EmergencyTeamRole,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get team members by department and role.
        
        Args:
            department: Department to filter by
            role: Role to filter by
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of team members
        """
        return await self.get_all(
            skip=skip,
            limit=limit,
            filters={
                "department": department,
                "role": role,
                "status": UserStatus.ACTIVE
            }
        )
    
    async def get_admins(
        self,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get all admin users.
        
        Args:
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of admin users
        """
        return await self.get_by_role(EmergencyTeamRole.ADMIN, skip, limit)
    
    async def get_managers(
        self,
        department: Optional[Department] = None,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get all managers, optionally filtered by department.
        
        Args:
            department: Optional department filter
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of managers
        """
        if department:
            return await self.get_by_department_and_role(
                department,
                EmergencyTeamRole.MANAGER,
                skip,
                limit
            )
        return await self.get_by_role(EmergencyTeamRole.MANAGER, skip, limit)
    
    async def activate_team_member(self, team_member_id: str) -> Optional[EmergencyTeam]:
        """
        Activate a pending team member.
        
        Args:
            team_member_id: Team member ID
            
        Returns:
            Optional[EmergencyTeam]: Activated team member or None
        """
        team_member = await self.get_by_id(team_member_id)
        if not team_member:
            return None
        
        team_member.activate()
        await self.session.flush()
        await self.session.refresh(team_member)
        return team_member
    
    async def deactivate_team_member(
        self, 
        team_member_id: str
    ) -> Optional[EmergencyTeam]:
        """
        Deactivate a team member.
        
        Args:
            team_member_id: Team member ID
            
        Returns:
            Optional[EmergencyTeam]: Deactivated team member or None
        """
        team_member = await self.get_by_id(team_member_id)
        if not team_member:
            return None
        
        team_member.deactivate()
        await self.session.flush()
        await self.session.refresh(team_member)
        return team_member
    
    async def get_pending_team_members(
        self,
        skip: int = 0,
        limit: int = 100
    ) -> List[EmergencyTeam]:
        """
        Get all pending team members (awaiting approval).
        
        Args:
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            List[EmergencyTeam]: List of pending team members
        """
        return await self.get_all(
            skip=skip,
            limit=limit,
            filters={"status": UserStatus.PENDING}
        )
    
    async def count_by_department(self, department: Department) -> int:
        """
        Count team members in a department.
        
        Args:
            department: Department to count
            
        Returns:
            int: Number of team members
        """
        return await self.count(
            filters={"department": department, "status": UserStatus.ACTIVE}
        )
    
    async def count_by_role(self, role: EmergencyTeamRole) -> int:
        """
        Count team members with a specific role.
        
        Args:
            role: Role to count
            
        Returns:
            int: Number of team members
        """
        return await self.count(
            filters={"role": role, "status": UserStatus.ACTIVE}
        )