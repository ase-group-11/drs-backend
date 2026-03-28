# File: conftest.py
# Location: drs-backend/ (project ROOT — same folder as pytest.ini)
#
# WHY THIS FILE EXISTS HERE (not inside app/tests/):
#   app/core/config.py calls `settings = get_settings()` at module level.
#   This means pydantic-settings tries to read env vars the moment ANY file
#   that imports from app.* is collected by pytest.
#
#   Fixtures in app/tests/conftest.py run AFTER collection — too late.
#   A root-level conftest.py is loaded by pytest BEFORE collection starts,
#   so os.environ values set here are available when config.py is first imported.

import os

# ── Set all required env vars before pytest collects any test files ──────────
os.environ.setdefault("ENVIRONMENT",           "testing")
os.environ.setdefault("DATABASE_URL",          "postgresql+asyncpg://test:test@localhost:5432/test_db")
os.environ.setdefault("REDIS_URL",             "redis://localhost:6379/1")
os.environ.setdefault("RABBITMQ_URL",          "amqp://guest:guest@localhost:5672/")
os.environ.setdefault("JWT_SECRET_KEY",        "test-secret-key-for-testing-minimum-32-characters-long")
os.environ.setdefault("JWT_ALGORITHM",         "HS256")
os.environ.setdefault("TWILIO_ACCOUNT_SID",    "ACtest000000000000000000000000000000")
os.environ.setdefault("TWILIO_AUTH_TOKEN",     "test_auth_token_32chars_minimum000")
os.environ.setdefault("TWILIO_PHONE_NUMBER",   "+15005550006")
os.environ.setdefault("TWILIO_SERVICE_SID",    "")
os.environ.setdefault("MAPBOX_API_KEY",        "pk.test_mapbox_key")
os.environ.setdefault("TRAFFIC_API_KEY",       "test_tomtom_key")
os.environ.setdefault("OPENWEATHER_API_KEY",   "test_openweather_key")
os.environ.setdefault("GEONAMES_USERNAME",     "test_geonames_user")
os.environ.setdefault("SENDGRID_API_KEY",      "SG.test_sendgrid_key")
os.environ.setdefault("SENDGRID_FROM_EMAIL",   "test@drs.ie")
os.environ.setdefault("AZURE_STORAGE_CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=dGVzdA==;EndpointSuffix=core.windows.net")
os.environ.setdefault("AZURE_CONTAINER_NAME",  "test-container")
os.environ.setdefault("LOG_LEVEL",             "WARNING")