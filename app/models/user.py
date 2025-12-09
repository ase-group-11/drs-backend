from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    mobile_number: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True) 
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)