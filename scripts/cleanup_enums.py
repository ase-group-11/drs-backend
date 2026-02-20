import asyncio
import asyncpg

async def cleanup():
    conn = await asyncpg.connect(
        host='pg-ase-project.postgres.database.azure.com',
        port=5432,
        user='pgadmin',
        password='Ase4life!',
        database='db_ase_project',
        ssl='require'
    )
    
    try:
        # Drop the remaining enum types
        await conn.execute("""
            DROP TYPE IF EXISTS disaster_type CASCADE;
            DROP TYPE IF EXISTS severity CASCADE;
            DROP TYPE IF EXISTS report_status CASCADE;
        """)
        print("✅ Remaining enum types dropped successfully!")
    finally:
        await conn.close()

asyncio.run(cleanup())