import logging
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from pathlib import Path

# Setup dedicated audit logger
audit_log_dir = Path("logs")
audit_log_dir.mkdir(parents=True, exist_ok=True)

audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)

# Create separate file handler for audit logs
audit_handler = logging.FileHandler(audit_log_dir / "audit.log")
audit_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [AUDIT] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
)
audit_logger.addHandler(audit_handler)

# Prevent propagation to root logger
audit_logger.propagate = False


def log_event(
    event_type: str,
    user_id: Optional[str],  # UUID string
    details: Dict[str, Any],
    ip_address: Optional[str] = None
) -> None:
    """
    Log an audit event to the dedicated audit log file.

    Args:
        event_type: Type of event (e.g., 'disaster_reported', 'status_changed')
        user_id: ID of user performing the action (None for system events)
        details: Dictionary of event-specific details
        ip_address: Optional IP address of the request

    Example:
        log_event(
            event_type='disaster_reported',
            user_id='550e8400-e29b-41d4-a716-446655440000',
            details={
                'disaster_id': '660e8400-e29b-41d4-a716-446655440000',
                'severity': 'critical',
                'location': '53.3498,-6.2603'
            },
            ip_address='192.168.1.1'
        )
    """
    event_data = {
        'event_type': event_type,
        'user_id': user_id,
        'timestamp': datetime.now(timezone.utc).isoformat(),  # Fixed: timezone-aware datetime
        'details': details
    }

    if ip_address:
        event_data['ip_address'] = ip_address

    # Format as JSON string for easy parsing
    audit_logger.info(json.dumps(event_data))
