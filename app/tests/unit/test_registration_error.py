#!/usr/bin/env python3
"""
Quick test to see actual registration error
"""

import asyncio
import sys
import os

# Add project to path
sys.path.insert(0, '/users/pgrad/bingis/Documents/projects/drs-project/drs-backend')

async def test_registration():
    """Test registration and catch the actual error."""
    print("=" * 60)
    print("Testing Registration Flow")
    print("=" * 60)
    print()
    
    try:
        # Import after adding to path
        from app.services.user_service import UserService
        from app.db.session import async_session_factory
        
        print("✅ Imports successful")
        
        # Create session
        async with async_session_factory() as session:
            print("✅ Database session created")
            
            # Create service
            service = UserService(session)
            print("✅ UserService created")
            
            # Try to register
            print()
            print("Attempting registration...")
            result = await service.register_user(
                phone_number="+918125019220",
                full_name="John Doe",
                email="john@example.com"
            )
            
            print("✅ Registration successful!")
            print(f"Result: {result}")
            
    except ImportError as e:
        print(f"❌ Import Error: {e}")
        print()
        print("This means registration_cache is not being imported.")
        print("Solution: Check if app/services/registration_cache.py exists")
        
    except AttributeError as e:
        print(f"❌ Attribute Error: {e}")
        print()
        print("This means a function is missing.")
        print("Check if user_service.py has all registration_cache imports")
        
    except Exception as e:
        print(f"❌ Error: {type(e).__name__}")
        print(f"Message: {str(e)}")
        print()
        print("Full traceback:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_registration())