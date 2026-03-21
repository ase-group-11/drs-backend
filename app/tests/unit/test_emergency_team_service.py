# # File: app/tests/unit/test_emergency_team_service.py
# """
# Unit tests for emergency team service.

# Tests ensure:
# 1. Team member registration with password
# 2. Password hashing
# 3. Team member login with password
# 4. Password verification
# 5. Duplicate handling
# 6. Password change
# """

# import pytest
# from unittest.mock import AsyncMock, patch
# from datetime import datetime, UTC


# @pytest.mark.asyncio
# async def test_register_team_member_success(mock_db_session):
#     """Test successful team member registration."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     # Mock team member
#     mock_team_member = EmergencyTeam(
#         id="test-team-id",
#         phone_number="+1234567890",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
#     mock_team_member.created_at = datetime.now(UTC)
    
#     with patch.object(service.team_repo, 'phone_exists', return_value=False), \
#          patch.object(service.team_repo, 'email_exists', return_value=False), \
#          patch.object(service.team_repo, 'employee_id_exists', return_value=False), \
#          patch('app.services.emergency_team_service.hash_password', return_value="hashed_password"), \
#          patch.object(service.team_repo, 'create', return_value=mock_team_member):
        
#         # Act
#         result = await service.register_team_member(
#             phone_number="+1234567890",
#             password="SecurePass123",
#             full_name="John Doe",
#             email="john.doe@emergency.ie",
#             role=EmergencyTeamRole.STAFF,
#             department=Department.MEDICAL
#         )
        
#         # Assert
#         assert result is not None
#         assert "message" in result
#         assert "team_member" in result
#         assert result["team_member"]["email"] == "john.doe@emergency.ie"
#         assert result["team_member"]["role"] == "staff"


# @pytest.mark.asyncio
# async def test_register_team_member_duplicate_phone(mock_db_session):
#     """Test registration with existing phone number."""
#     from app.services.emergency_team_service import EmergencyTeamService
    
#     service = EmergencyTeamService(mock_db_session)
    
#     with patch.object(service.team_repo, 'phone_exists', return_value=True):
        
#         # Act & Assert
#         with pytest.raises(ValueError) as exc_info:
#             await service.register_team_member(
#                 phone_number="+1234567890",
#                 password="SecurePass123",
#                 full_name="John Doe",
#                 email="john.doe@emergency.ie",
#                 role="staff",
#                 department="medical"
#             )
        
#         assert "already registered" in str(exc_info.value).lower()


# @pytest.mark.asyncio
# async def test_register_team_member_duplicate_email(mock_db_session):
#     """Test registration with existing email."""
#     from app.services.emergency_team_service import EmergencyTeamService
    
#     service = EmergencyTeamService(mock_db_session)
    
#     with patch.object(service.team_repo, 'phone_exists', return_value=False), \
#          patch.object(service.team_repo, 'email_exists', return_value=True):
        
#         # Act & Assert
#         with pytest.raises(ValueError) as exc_info:
#             await service.register_team_member(
#                 phone_number="+1234567890",
#                 password="SecurePass123",
#                 full_name="John Doe",
#                 email="john.doe@emergency.ie",
#                 role="staff",
#                 department="medical"
#             )
        
#         assert "already registered" in str(exc_info.value).lower()


# @pytest.mark.asyncio
# async def test_login_team_member_success(mock_db_session):
#     """Test successful team member login."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     # Mock team member with hashed password
#     mock_team_member = EmergencyTeam(
#         id="test-id",
#         phone_number="+1234567890",
#         password_hash="hashed_password",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
#     mock_team_member.created_at = datetime.now(UTC)
    
#     with patch.object(service.team_repo, 'get_active_team_member_by_email', return_value=mock_team_member), \
#          patch('app.services.emergency_team_service.verify_password', return_value=True), \
#          patch('app.services.emergency_team_service.create_access_token', return_value="access_token"), \
#          patch('app.services.emergency_team_service.create_refresh_token', return_value="refresh_token"):
        
#         # Act
#         result = await service.login_team_member(
#             email="john.doe@emergency.ie",
#             password="SecurePass123"
#         )
        
#         # Assert
#         assert result is not None
#         assert "team_member" in result
#         assert "tokens" in result
#         assert result["tokens"]["access_token"] == "access_token"


# @pytest.mark.asyncio
# async def test_login_team_member_invalid_credentials(mock_db_session):
#     """Test login with invalid credentials."""
#     from app.services.emergency_team_service import EmergencyTeamService
    
#     service = EmergencyTeamService(mock_db_session)
    
#     with patch.object(service.team_repo, 'get_active_team_member_by_email', return_value=None):
        
#         # Act & Assert
#         with pytest.raises(ValueError) as exc_info:
#             await service.login_team_member(
#                 email="john.doe@emergency.ie",
#                 password="WrongPassword"
#             )
        
#         assert "invalid credentials" in str(exc_info.value).lower()


# @pytest.mark.asyncio
# async def test_login_team_member_wrong_password(mock_db_session):
#     """Test login with correct email but wrong password."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     mock_team_member = EmergencyTeam(
#         id="test-id",
#         phone_number="+1234567890",
#         password_hash="hashed_password",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
    
#     with patch.object(service.team_repo, 'get_active_team_member_by_email', return_value=mock_team_member), \
#          patch('app.services.emergency_team_service.verify_password', return_value=False):
        
#         # Act & Assert
#         with pytest.raises(ValueError) as exc_info:
#             await service.login_team_member(
#                 email="john.doe@emergency.ie",
#                 password="WrongPassword"
#             )
        
#         assert "invalid credentials" in str(exc_info.value).lower()


# @pytest.mark.asyncio
# async def test_login_team_member_by_phone_success(mock_db_session):
#     """Test login with phone number instead of email."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     mock_team_member = EmergencyTeam(
#         id="test-id",
#         phone_number="+1234567890",
#         password_hash="hashed_password",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
#     mock_team_member.created_at = datetime.now(UTC)
    
#     with patch.object(service.team_repo, 'get_active_team_member_by_phone', return_value=mock_team_member), \
#          patch('app.services.emergency_team_service.verify_password', return_value=True), \
#          patch('app.services.emergency_team_service.create_access_token', return_value="access_token"), \
#          patch('app.services.emergency_team_service.create_refresh_token', return_value="refresh_token"):
        
#         # Act
#         result = await service.login_team_member_by_phone(
#             phone_number="+1234567890",
#             password="SecurePass123"
#         )
        
#         # Assert
#         assert result is not None
#         assert result["team_member"]["phone_number"] == "+1234567890"


# @pytest.mark.asyncio
# async def test_change_password_success(mock_db_session):
#     """Test successful password change."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     mock_team_member = EmergencyTeam(
#         id="test-id",
#         phone_number="+1234567890",
#         password_hash="old_hashed_password",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
    
#     with patch.object(service.team_repo, 'get_by_id', return_value=mock_team_member), \
#          patch('app.services.emergency_team_service.verify_password', return_value=True), \
#          patch('app.services.emergency_team_service.hash_password', return_value="new_hashed_password"), \
#          patch.object(service.team_repo, 'update', return_value=mock_team_member):
        
#         # Act
#         result = await service.change_password(
#             team_member_id="test-id",
#             old_password="OldPassword123",
#             new_password="NewPassword456"
#         )
        
#         # Assert
#         assert result is not None
#         assert "message" in result
#         assert "success" in result["message"].lower()


# @pytest.mark.asyncio
# async def test_change_password_wrong_old_password(mock_db_session):
#     """Test password change with incorrect old password."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.emergency_team import EmergencyTeam
#     from app.models.enums import UserStatus, EmergencyTeamRole, Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     mock_team_member = EmergencyTeam(
#         id="test-id",
#         phone_number="+1234567890",
#         password_hash="hashed_password",
#         full_name="John Doe",
#         email="john.doe@emergency.ie",
#         role=EmergencyTeamRole.STAFF,
#         department=Department.MEDICAL,
#         status=UserStatus.ACTIVE
#     )
    
#     with patch.object(service.team_repo, 'get_by_id', return_value=mock_team_member), \
#          patch('app.services.emergency_team_service.verify_password', return_value=False):
        
#         # Act & Assert
#         with pytest.raises(ValueError) as exc_info:
#             await service.change_password(
#                 team_member_id="test-id",
#                 old_password="WrongOldPassword",
#                 new_password="NewPassword456"
#             )
        
#         assert "incorrect" in str(exc_info.value).lower()


# @pytest.mark.asyncio
# async def test_get_team_members_by_department(mock_db_session):
#     """Test getting team members by department."""
#     from app.services.emergency_team_service import EmergencyTeamService
#     from app.models.enums import Department
    
#     service = EmergencyTeamService(mock_db_session)
    
#     with patch.object(service.team_repo, 'get_by_department', return_value=[]):
        
#         # Act
#         result = await service.get_team_members_by_department(Department.MEDICAL)
        
#         # Assert
#         assert result is not None
#         assert isinstance(result, list)