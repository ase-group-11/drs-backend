import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

logger = logging.getLogger(__name__)

logger.info(f"🔌 Initializing Database connection to: {settings.DATABASE_URL.split('@')[-1]}") # Log safe part of URL

# Create engine using the validated URL from settings
engine = create_engine(settings.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()