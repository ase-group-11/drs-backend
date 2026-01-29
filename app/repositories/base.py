# File: app/repositories/base.py
"""
Base repository with generic CRUD operations.

Implements the Repository pattern for database access.
All repositories inherit from this base class.
"""

from typing import TypeVar, Generic, Type, Optional, List
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic base repository for CRUD operations.
    
    Provides common database operations for all models.
    Specific repositories can extend this for custom queries.
    """
    
    def __init__(self, model: Type[ModelType], session: AsyncSession):
        """
        Initialize repository.
        
        Args:
            model: SQLAlchemy model class
            session: Async database session
        """
        self.model = model
        self.session = session
    
    async def create(self, **kwargs) -> ModelType:
        """
        Create a new record.
        
        Args:
            **kwargs: Model fields and values
            
        Returns:
            ModelType: Created model instance
            
        Example:
            user = await repo.create(
                phone_number="+1234567890",
                full_name="John Doe"
            )
        """
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance
    
    async def get_by_id(self, id: str) -> Optional[ModelType]:
        """
        Get record by ID.
        
        Args:
            id: Record ID (UUID)
            
        Returns:
            Optional[ModelType]: Model instance or None
        """
        result = await self.session.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()
    
    async def get_by_field(self, field_name: str, value: any) -> Optional[ModelType]:
        """
        Get record by any field.
        
        Args:
            field_name: Field name to query
            value: Value to search for
            
        Returns:
            Optional[ModelType]: Model instance or None
            
        Example:
            user = await repo.get_by_field("phone_number", "+1234567890")
        """
        result = await self.session.execute(
            select(self.model).where(getattr(self.model, field_name) == value)
        )
        return result.scalar_one_or_none()
    
    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        filters: dict = None
    ) -> List[ModelType]:
        """
        Get all records with optional filtering and pagination.
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            filters: Dictionary of field:value filters
            
        Returns:
            List[ModelType]: List of model instances
        """
        query = select(self.model)
        
        if filters:
            for field, value in filters.items():
                query = query.where(getattr(self.model, field) == value)
        
        query = query.offset(skip).limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())
    
    async def update(self, id: str, **kwargs) -> Optional[ModelType]:
        """
        Update a record.
        
        Args:
            id: Record ID
            **kwargs: Fields to update
            
        Returns:
            Optional[ModelType]: Updated model instance or None
        """
        instance = await self.get_by_id(id)
        if not instance:
            return None
        
        for key, value in kwargs.items():
            setattr(instance, key, value)
        
        await self.session.flush()
        await self.session.refresh(instance)
        return instance
    
    async def delete(self, id: str) -> bool:
        """
        Hard delete a record.
        
        Args:
            id: Record ID
            
        Returns:
            bool: True if deleted, False if not found
        """
        instance = await self.get_by_id(id)
        if not instance:
            return False
        
        await self.session.delete(instance)
        await self.session.flush()
        return True
    
    async def soft_delete(self, id: str) -> Optional[ModelType]:
        """
        Soft delete a record (set deleted_at timestamp).
        
        Args:
            id: Record ID
            
        Returns:
            Optional[ModelType]: Soft-deleted instance or None
        """
        instance = await self.get_by_id(id)
        if not instance:
            return None
        
        instance.soft_delete()
        await self.session.flush()
        await self.session.refresh(instance)
        return instance
    
    async def count(self, filters: dict = None) -> int:
        """
        Count records matching filters.
        
        Args:
            filters: Dictionary of field:value filters
            
        Returns:
            int: Number of matching records
        """
        query = select(self.model)
        
        if filters:
            for field, value in filters.items():
                query = query.where(getattr(self.model, field) == value)
        
        result = await self.session.execute(query)
        return len(list(result.scalars().all()))
    
    async def exists(self, field_name: str, value: any) -> bool:
        """
        Check if record exists with given field value.
        
        Args:
            field_name: Field name to check
            value: Value to search for
            
        Returns:
            bool: True if exists, False otherwise
            
        Example:
            exists = await repo.exists("phone_number", "+1234567890")
        """
        instance = await self.get_by_field(field_name, value)
        return instance is not None