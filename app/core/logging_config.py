# File: app/core/logging_config.py
"""
Logging configuration for the application.

Logs to both console and file with rotation.
"""

import logging
import logging.handlers
import os
from pathlib import Path

from app.core.config import settings


def setup_logging():
    """
    Configure application-wide logging.
    
    Features:
    - Console logging (colored)
    - File logging with rotation
    - Different log levels per environment
    - Separate error log file
    """
    
    # Create logs directory if it doesn't exist
    log_dir = Path(settings.LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get log level from settings
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console Handler (colored output)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    
    # File Handler (rotating, 10MB max, keep 5 backups)
    file_handler = logging.handlers.RotatingFileHandler(
        settings.LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    
    # Error File Handler (only errors and critical)
    error_log_path = str(Path(settings.LOG_FILE).parent / 'error.log')
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_path,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    
    # Add handlers to root logger
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(error_handler)
    
    # Log startup message
    root_logger.info("=" * 70)
    root_logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION}")
    root_logger.info(f"📍 Environment: {settings.ENVIRONMENT}")
    root_logger.info(f"📝 Log Level: {settings.LOG_LEVEL}")
    root_logger.info(f"📁 Log File: {settings.LOG_FILE}")
    root_logger.info(f"📁 Error Log: {error_log_path}")
    root_logger.info("=" * 70)
    
    # Suppress noisy third-party loggers
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.
    
    Args:
        name: Logger name (usually __name__)
        
    Returns:
        logging.Logger: Configured logger instance
        
    Usage:
        logger = get_logger(__name__)
        logger.info("Hello world")
    """
    return logging.getLogger(name)


# Convenience functions for common logging patterns

def log_redis_fallback(operation: str, key: str):
    """Log when Redis fallback is used."""
    logger = get_logger("redis.fallback")
    logger.warning(
        f"⚠️  REDIS FALLBACK: {operation} operation on key '{key}' - "
        f"Using in-memory cache"
    )


def log_redis_error(operation: str, key: str, error: Exception):
    """Log Redis errors."""
    logger = get_logger("redis.error")
    logger.error(
        f"❌ REDIS ERROR: {operation} failed for key '{key}' - "
        f"Error: {type(error).__name__}: {str(error)}"
    )


def log_auth_success(user_type: str, identifier: str):
    """Log successful authentication."""
    logger = get_logger("auth.success")
    logger.info(f"✅ AUTH SUCCESS: {user_type} - {identifier}")


def log_auth_failure(user_type: str, identifier: str, reason: str):
    """Log authentication failures."""
    logger = get_logger("auth.failure")
    logger.warning(f"❌ AUTH FAILED: {user_type} - {identifier} - Reason: {reason}")


def log_otp_sent(phone_number: str, via_redis: bool):
    """Log OTP sent events."""
    logger = get_logger("otp.sent")
    storage = "Redis" if via_redis else "Fallback Cache"
    logger.info(f"📱 OTP SENT: {phone_number} - Stored in: {storage}")


def log_otp_verified(phone_number: str, success: bool):
    """Log OTP verification attempts."""
    logger = get_logger("otp.verify")
    status = "SUCCESS" if success else "FAILED"
    logger.info(f"🔐 OTP VERIFY {status}: {phone_number}")