#!/usr/bin/env python3
"""
Check what's wrong with your setup
Run from project root: python test_what_is_wrong.py
"""

import os
import sys

print("=" * 70)
print("DRS Backend Diagnostic")
print("=" * 70)
print()

# Check 1: Are we in the right place?
print("Step 1: Checking location...")
if os.path.exists("app/services/user_service.py"):
    print("✅ Found app/services/user_service.py")
else:
    print("❌ Not in project root or file missing")
    sys.exit(1)

# Check 2: Does registration_cache exist?
print("\nStep 2: Checking registration_cache.py...")
if os.path.exists("app/services/registration_cache.py"):
    print("✅ Found app/services/registration_cache.py")
else:
    print("❌ app/services/registration_cache.py is MISSING")
    print("   Solution: Copy registration_cache.py to app/services/")
    sys.exit(1)

# Check 3: Does user_service import registration_cache?
print("\nStep 3: Checking imports in user_service.py...")
with open("app/services/user_service.py", "r") as f:
    content = f.read()
    
if "registration_cache" in content:
    print("✅ user_service.py imports registration_cache")
    
    # Check specific imports
    if "store_registration_data" in content:
        print("✅ Has store_registration_data")
    else:
        print("❌ Missing store_registration_data")
        
    if "get_registration_data" in content:
        print("✅ Has get_registration_data")
    else:
        print("❌ Missing get_registration_data")
else:
    print("❌ user_service.py does NOT import registration_cache")
    print("   This is the problem!")
    print()
    print("   Solution:")
    print("   Add this to the top of app/services/user_service.py:")
    print()
    print("   from app.services.registration_cache import (")
    print("       store_registration_data,")
    print("       get_registration_data,")
    print("       delete_registration_data")
    print("   )")
    sys.exit(1)

# Check 4: Does register_user use cache?
print("\nStep 4: Checking if register_user uses cache...")
if "await store_registration_data" in content:
    print("✅ register_user stores data in cache")
else:
    print("❌ register_user does NOT store data in cache")
    print("   This will cause 500 error!")

# Check 5: Does verify_registration use cache?
print("\nStep 5: Checking if verify_registration uses cache...")
if "await get_registration_data" in content:
    print("✅ verify_registration gets data from cache")
else:
    print("❌ verify_registration does NOT get data from cache")
    print("   This will cause 400 error!")

# Check 6: Environment
print("\nStep 6: Checking .env file...")
if os.path.exists(".env"):
    print("✅ Found .env file")
    with open(".env", "r") as f:
        env_content = f.read()
        if "ENVIRONMENT" in env_content:
            import re
            match = re.search(r'ENVIRONMENT="?(\w+)"?', env_content)
            if match:
                env = match.group(1)
                print(f"   Environment: {env}")
                if env == "testing":
                    print("   ✅ Testing mode (Mock SMS - no Twilio errors)")
                elif env == "development":
                    print("   ⚠️  Development mode (Real SMS - needs Twilio)")
        else:
            print("   ⚠️  ENVIRONMENT not set in .env")
else:
    print("⚠️  .env file not found")
    print("   Consider creating one for easier config management")

print()
print("=" * 70)
print("Summary")
print("=" * 70)

# Final check
if ("registration_cache" in content and 
    "store_registration_data" in content and 
    "await store_registration_data" in content):
    print("✅ Everything looks good! Registration should work.")
    print()
    print("If still getting 500 error:")
    print("1. Check if Redis is accessible: redis-cli ping")
    print("2. Look for error details in your FastAPI terminal")
    print("3. Try running: python test_registration_error.py")
else:
    print("❌ Found issues that will cause 500 error!")
    print()
    print("Quick fix:")
    print("1. Copy user_service_COMPLETE.py to app/services/user_service.py")
    print("2. Restart server")