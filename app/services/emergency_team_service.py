# File: app/services/emergency_team_service.py
"""
Emergency team service for password-based authentication.

Business logic for:
- Emergency team registration (with password)
- Emergency team login (password verification)
- Token generation
- Account management
"""

from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.emergency_team_repository import EmergencyTeamRepository
from app.auth.password_handler import hash_password, verify_password
from app.auth.jwt_handler import create_access_token, create_refresh_token
from app.models.emergency_team import EmergencyTeam
from app.models.enums import UserStatus, EmergencyTeamRole, Department
from app.core.config import settings


class EmergencyTeamService:
    """
    Emergency team service for authentication and team management.
    
    Handles registration, login, and team operations.
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize emergency team service.
        
        Args:
            session: Database session
        """
        self.session = session
        self.team_repo = EmergencyTeamRepository(session)
    
    async def register_team_member(
        self,
        phone_number: str,
        password: str,
        full_name: str,
        email: str,
        role: EmergencyTeamRole,
        department: Department,
        employee_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Register a new emergency team member.
        
        Steps:
        1. Validate uniqueness (phone, email, employee_id)
        2. Hash password with Argon2
        3. Create team member account (status: PENDING or ACTIVE)
        4. Return success message
        
        Args:
            phone_number: Team member's phone number
            password: Plain text password (will be hashed)
            full_name: Team member's full name
            email: Email address
            role: Team role (admin, manager, staff)
            department: Department (medical, police, fire, it)
            employee_id: Optional employee ID
            
        Returns:
            Dict with team member data
            
        Raises:
            ValueError: If phone, email, or employee_id already exists
            
        Example:
            >>> result = await service.register_team_member(
            ...     phone_number="+1234567890",
            ...     password="SecurePass123!",
            ...     full_name="John Doe",
            ...     email="john.doe@emergency.ie",
            ...     role=EmergencyTeamRole.STAFF,
            ...     department=Department.MEDICAL
            ... )
        """
        # Check if phone number already exists
        if await self.team_repo.phone_exists(phone_number):
            raise ValueError(f"Phone number {phone_number} is already registered")
        
        # Check if email already exists
        if await self.team_repo.email_exists(email):
            raise ValueError(f"Email {email} is already registered")
        
        # Check if employee ID already exists (if provided)
        if employee_id and await self.team_repo.employee_id_exists(employee_id):
            raise ValueError(f"Employee ID {employee_id} is already registered")
        
        # Hash password
        password_hash = hash_password(password)
        
        # Create team member
        # Note: Status can be PENDING (requires admin approval) or ACTIVE
        # For this implementation, we'll create as ACTIVE directly
        # In production, you might want admin approval workflow
        team_member = await self.team_repo.create(
            phone_number=phone_number,
            password_hash=password_hash,
            full_name=full_name,
            email=email,
            role=role,
            department=department,
            employee_id=employee_id,
            status=UserStatus.ACTIVE  # Or PENDING if approval required
        )
        
        # Commit transaction
        await self.session.commit()
        await self.session.refresh(team_member)
        
        return {
            "message": "Registration successful",
            "team_member": {
                "id": team_member.id,
                "phone_number": team_member.phone_number,
                "full_name": team_member.full_name,
                "email": team_member.email,
                "role": team_member.role.value,
                "department": team_member.department.value,
                "employee_id": team_member.employee_id,
                "status": team_member.status.value,
                "created_at": team_member.created_at.isoformat()
            }
        }
    
    async def login_team_member(
        self,
        email: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Login emergency team member with email and password.
        
        Steps:
        1. Find active team member by email
        2. Verify password with Argon2
        3. Generate JWT tokens
        4. Return team member data + tokens
        
        Args:
            email: Team member's email
            password: Plain text password
            
        Returns:
            Dict with team member data and tokens
            
        Raises:
            ValueError: If credentials invalid or account not active
            
        Example:
            >>> result = await service.login_team_member(
            ...     email="john.doe@emergency.ie",
            ...     password="SecurePass123!"
            ... )
            >>> print(result["tokens"]["access_token"])
        """
        # Get active team member by email
        team_member = await self.team_repo.get_active_team_member_by_email(email)
        
        if not team_member:
            raise ValueError(
                "Invalid credentials or account is not active"
            )
        
        # Verify password
        is_valid = verify_password(password, team_member.password_hash)
        
        if not is_valid:
            raise ValueError("Invalid credentials")
        
        # Generate JWT tokens
        access_token = create_access_token(
            user_id=team_member.id,
            user_type="emergency_team"
        )
        
        refresh_token = create_refresh_token(user_id=team_member.id)
        
        return {
            "team_member": {
                "id": team_member.id,
                "phone_number": team_member.phone_number,
                "full_name": team_member.full_name,
                "email": team_member.email,
                "role": team_member.role.value,
                "department": team_member.department.value,
                "employee_id": team_member.employee_id,
                "status": team_member.status.value,
                "created_at": team_member.created_at.isoformat()
            },
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }
        }
    
    async def login_team_member_by_phone(
        self,
        phone_number: str,
        password: str
    ) -> Dict[str, Any]:
        """
        Login emergency team member with phone number and password.
        
        Alternative login method using phone number instead of email.
        
        Args:
            phone_number: Team member's phone number
            password: Plain text password
            
        Returns:
            Dict with team member data and tokens
            
        Raises:
            ValueError: If credentials invalid
        """
        # Get active team member by phone
        team_member = await self.team_repo.get_active_team_member_by_phone(
            phone_number
        )
        
        if not team_member:
            raise ValueError(
                "Invalid credentials or account is not active"
            )
        
        # Verify password
        is_valid = verify_password(password, team_member.password_hash)
        
        if not is_valid:
            raise ValueError("Invalid credentials")
        
        # Generate JWT tokens
        access_token = create_access_token(
            user_id=team_member.id,
            user_type="emergency_team"
        )
        
        refresh_token = create_refresh_token(user_id=team_member.id)
        
        return {
            "team_member": {
                "id": team_member.id,
                "phone_number": team_member.phone_number,
                "full_name": team_member.full_name,
                "email": team_member.email,
                "role": team_member.role.value,
                "department": team_member.department.value,
                "employee_id": team_member.employee_id,
                "status": team_member.status.value,
                "created_at": team_member.created_at.isoformat()
            },
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
            }
        }
    
    async def get_team_member_by_id(
        self, 
        team_member_id: str
    ) -> Optional[EmergencyTeam]:
        """
        Get team member by ID.
        
        Args:
            team_member_id: Team member ID (UUID)
            
        Returns:
            Optional[EmergencyTeam]: Team member instance or None
        """
        return await self.team_repo.get_by_id(team_member_id)
    
    async def get_team_member_by_email(
        self, 
        email: str
    ) -> Optional[EmergencyTeam]:
        """
        Get team member by email.
        
        Args:
            email: Email address
            
        Returns:
            Optional[EmergencyTeam]: Team member instance or None
        """
        return await self.team_repo.get_by_email(email)
    
    async def get_team_members_by_department(
        self,
        department: Department,
        skip: int = 0,
        limit: int = 100
    ) -> list[EmergencyTeam]:
        """
        Get all team members in a department.
        
        Args:
            department: Department to filter by
            skip: Number of records to skip
            limit: Maximum records to return
            
        Returns:
            list[EmergencyTeam]: List of team members
        """
        return await self.team_repo.get_by_department(department, skip, limit)
    
    async def change_password(
        self,
        team_member_id: str,
        old_password: str,
        new_password: str
    ) -> Dict[str, str]:
        """
        Change team member password.
        
        Args:
            team_member_id: Team member ID
            old_password: Current password
            new_password: New password
            
        Returns:
            Dict with success message
            
        Raises:
            ValueError: If old password incorrect or team member not found
        """
        team_member = await self.team_repo.get_by_id(team_member_id)
        
        if not team_member:
            raise ValueError("Team member not found")
        
        # Verify old password
        is_valid = verify_password(old_password, team_member.password_hash)
        
        if not is_valid:
            raise ValueError("Current password is incorrect")
        
        # Hash new password
        new_password_hash = hash_password(new_password)
        
        # Update password
        await self.team_repo.update(
            team_member_id,
            password_hash=new_password_hash
        )
        
        await self.session.commit()
        
        return {"message": "Password changed successfully"}
    
    async def deactivate_team_member(
        self, 
        team_member_id: str
    ) -> Dict[str, str]:
        """
        Deactivate a team member account.
        
        Args:
            team_member_id: Team member ID
            
        Returns:
            Dict with success message
            
        Raises:
            ValueError: If team member not found
        """
        team_member = await self.team_repo.deactivate_team_member(team_member_id)
        
        if not team_member:
            raise ValueError("Team member not found")
        
        await self.session.commit()
        
        return {"message": "Team member deactivated successfully"}