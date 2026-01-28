#!/usr/bin/env python3
"""
Test Redis Fallback and Apply Fixes

This script:
1. Tests if Redis fallback is working
2. Shows which files need updating
3. Applies the fixes automatically
"""

import sys
import os
import asyncio
import shutil
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.getcwd())

print("=" * 70)
print("🔍 Redis Fallback Diagnostic")
print("=" * 70)
print()

async def test_redis_connection():
    """Test if Redis is accessible."""
    print("Step 1: Testing Redis connection...")
    try:
        from app.db.redis_client import get_redis_client
        redis = await get_redis_client()
        await redis.ping()
        print("✅ Redis is UP and running")
        await redis.aclose()
        return True
    except Exception as e:
        print(f"❌ Redis is DOWN: {e}")
        return False


async def test_fallback_cache():
    """Test if fallback cache works."""
    print("\nStep 2: Testing fallback cache...")
    try:
        from app.db.redis_client import set_with_expiry, get_value
        
        # Try to store and retrieve
        await set_with_expiry("test_key", "test_value", 60)
        value = await get_value("test_key")
        
        if value == "test_value":
            print("✅ Fallback cache is WORKING")
            return True
        else:
            print("❌ Fallback cache returned wrong value")
            return False
            
    except Exception as e:
        print(f"❌ Fallback cache FAILED: {e}")
        return False


def check_file_imports():
    """Check which files need updating."""
    print("\nStep 3: Checking file imports...")
    
    files_to_check = {
        "app/services/otp_service.py": [
            "from app.db.redis_client import get_redis_client",
            "from app.db.redis_client import set_with_expiry"
        ],
        "app/services/registration_cache.py": [
            "from app.db.redis_client import get_redis_client",
            "from app.db.redis_client import set_with_expiry"
        ]
    }
    
    needs_update = []
    
    for filepath, patterns in files_to_check.items():
        if not os.path.exists(filepath):
            print(f"⚠️  {filepath} - NOT FOUND")
            continue
            
        with open(filepath, 'r') as f:
            content = f.read()
            
        # Check if using old Redis client directly
        if "redis = await get_redis_client()" in content and \
           "await redis.setex" in content:
            print(f"❌ {filepath} - Uses OLD Redis client (needs update)")
            needs_update.append(filepath)
        elif "set_with_expiry" in content and "get_value" in content:
            print(f"✅ {filepath} - Already uses fallback helpers")
        else:
            print(f"⚠️  {filepath} - Unknown pattern")
            needs_update.append(filepath)
    
    return needs_update


def apply_fixes():
    """Apply the fixes."""
    print("\n" + "=" * 70)
    print("📋 Fix Instructions")
    print("=" * 70)
    print()
    
    # Check if fix files exist in outputs
    fix_files = [
        "/mnt/user-data/outputs/otp_service_WITH_FALLBACK.py",
        "/mnt/user-data/outputs/registration_cache_WITH_FALLBACK.py"
    ]
    
    print("Copy these files to your project:")
    print()
    
    commands = [
        "# Update OTP service to use fallback",
        "cp /mnt/user-data/outputs/otp_service_WITH_FALLBACK.py app/services/otp_service.py",
        "",
        "# Update registration cache to use fallback",
        "cp /mnt/user-data/outputs/registration_cache_WITH_FALLBACK.py app/services/registration_cache.py",
        "",
        "# Restart server",
        "uvicorn app.main:app --reload"
    ]
    
    for cmd in commands:
        print(cmd)
    
    print()
    print("=" * 70)
    print("Or run this one-liner:")
    print("=" * 70)
    print()
    print("cp /mnt/user-data/outputs/otp_service_WITH_FALLBACK.py app/services/otp_service.py && \\")
    print("cp /mnt/user-data/outputs/registration_cache_WITH_FALLBACK.py app/services/registration_cache.py && \\")
    print("uvicorn app.main:app --reload")


async def main():
    """Main diagnostic function."""
    
    # Test 1: Redis
    redis_up = await test_redis_connection()
    
    # Test 2: Fallback
    fallback_works = await test_fallback_cache()
    
    # Test 3: Check files
    needs_update = check_file_imports()
    
    # Summary
    print("\n" + "=" * 70)
    print("📊 Summary")
    print("=" * 70)
    
    if redis_up:
        print("✅ Redis is running - app should work normally")
    else:
        print("❌ Redis is DOWN")
        
    if fallback_works:
        print("✅ Fallback cache is working")
    else:
        print("❌ Fallback cache is NOT working")
    
    if needs_update:
        print(f"❌ {len(needs_update)} files need updating:")
        for f in needs_update:
            print(f"   - {f}")
        print()
        print("🔧 Action: Update files to use fallback helpers")
        apply_fixes()
    else:
        print("✅ All files are using fallback helpers")
        
    print()
    print("=" * 70)
    
    if not redis_up and fallback_works and not needs_update:
        print("✅ EVERYTHING IS READY!")
        print("   Redis is down but fallback should work.")
        print("   Try registering a user - it should work!")
    elif not redis_up and needs_update:
        print("⚠️  ACTION REQUIRED:")
        print("   1. Update the files shown above")
        print("   2. Restart your server")
        print("   3. Try registration again")
    
    print()


if __name__ == "__main__":
    asyncio.run(main())