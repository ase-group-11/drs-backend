#!/usr/bin/env python3
"""
Check if .env file is loading correctly.

Run from project root:
    python check_env.py
"""

import os
import sys
from pathlib import Path

print("=" * 70)
print("🔍 Environment Configuration Checker")
print("=" * 70)
print()

# Check 1: Does .env file exist?
print("Step 1: Checking if .env file exists...")
env_file = Path(".env")
if env_file.exists():
    print(f"✅ Found .env file at: {env_file.absolute()}")
    print(f"   File size: {env_file.stat().st_size} bytes")
else:
    print("❌ .env file NOT FOUND!")
    print("   Create one with:")
    print("   touch .env")
    sys.exit(1)

print()

# Check 2: Show .env contents
print("Step 2: Contents of .env file:")
print("-" * 70)
try:
    with open(".env", "r") as f:
        content = f.read()
        if content.strip():
            for line in content.split('\n'):
                if line.strip() and not line.startswith('#'):
                    # Mask sensitive values
                    if '=' in line:
                        key, value = line.split('=', 1)
                        if 'TOKEN' in key or 'SECRET' in key or 'PASSWORD' in key:
                            masked = f"{value[:10]}..." if len(value) > 10 else "***"
                            print(f"   {key}={masked}")
                        else:
                            print(f"   {line}")
                elif line.startswith('#'):
                    print(f"   {line}")
        else:
            print("   ⚠️  File is empty!")
except Exception as e:
    print(f"❌ Error reading .env: {e}")

print("-" * 70)
print()

# Check 3: Try loading with python-dotenv
print("Step 3: Testing if python-dotenv can load it...")
try:
    from dotenv import load_dotenv
    print("✅ python-dotenv is installed")
    
    # Load .env
    load_dotenv()
    print("✅ .env file loaded successfully")
except ImportError:
    print("❌ python-dotenv is NOT installed!")
    print("   Install it with: pip install python-dotenv")
    sys.exit(1)

print()

# Check 4: Verify specific environment variables
print("Step 4: Checking if variables are accessible...")
print("-" * 70)

important_vars = [
    "ENVIRONMENT",
    "DEBUG",
    "DATABASE_URL",
    "REDIS_URL",
    "JWT_SECRET_KEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
]

all_good = True
for var in important_vars:
    value = os.getenv(var)
    if value:
        # Mask sensitive values
        if 'TOKEN' in var or 'SECRET' in var or 'PASSWORD' in var:
            masked = f"{value[:10]}..." if len(value) > 10 else "***"
            print(f"✅ {var:25} = {masked}")
        else:
            print(f"✅ {var:25} = {value}")
    else:
        print(f"❌ {var:25} = NOT SET")
        all_good = False

print("-" * 70)
print()

# Check 5: Try loading Settings from your app
print("Step 5: Testing your app's Settings class...")
try:
    # Add project to path
    sys.path.insert(0, os.getcwd())
    
    from app.core.config import settings
    print("✅ Settings imported successfully")
    print()
    print("Settings values:")
    print(f"   ENVIRONMENT: {settings.ENVIRONMENT}")
    print(f"   DEBUG: {settings.DEBUG}")
    print(f"   DATABASE_URL: {settings.DATABASE_URL[:30]}...")
    print(f"   REDIS_URL: {settings.REDIS_URL}")
    print(f"   TWILIO_ACCOUNT_SID: {settings.TWILIO_ACCOUNT_SID[:10]}...")
    print(f"   OTP_EXPIRY_SECONDS: {settings.OTP_EXPIRY_SECONDS}")
    
except Exception as e:
    print(f"❌ Failed to load Settings: {e}")
    import traceback
    traceback.print_exc()
    all_good = False

print()
print("=" * 70)
print("Summary")
print("=" * 70)

if all_good:
    print("✅ Everything looks good!")
    print()
    print("Your .env is loading correctly.")
    print(f"Current ENVIRONMENT: {os.getenv('ENVIRONMENT', 'NOT SET')}")
    print()
    
    # Check if in testing mode
    if os.getenv('ENVIRONMENT') == 'testing':
        print("🧪 You're in TESTING mode")
        print("   - OTP will be printed in terminal")
        print("   - No real SMS will be sent")
        print("   - No Twilio charges")
    elif os.getenv('ENVIRONMENT') == 'development':
        print("📡 You're in DEVELOPMENT mode")
        print("   - Real SMS will be sent")
        print("   - Twilio charges apply")
        print("   - Make sure Twilio credentials are correct")
    
else:
    print("❌ Found some issues!")
    print()
    print("Fixes:")
    print("1. Make sure .env exists in project root")
    print("2. Install python-dotenv: pip install python-dotenv")
    print("3. Add missing variables to .env")
    print("4. Restart your server after changes")

print()
print("=" * 70)