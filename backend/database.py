"""PostgreSQL connection for Monitoring API — uses 'monitoring' schema."""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from backend.config import DATABASE_URL
import logging

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

@event.listens_for(engine, "connect")
def set_search_path(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("SET search_path TO monitoring, auth, public")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def ensure_db_ready():
    """Create enum types, and all tables on a fresh database."""
    with engine.connect() as conn:
        # Create enum types manually before create_all
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE room_type AS ENUM ('room', 'fridge', 'freezer');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE sensor_type AS ENUM ('temperature', 'humidity');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))
        conn.commit()
    
    # Now create all tables
    Base.metadata.create_all(bind=engine)
    
    # Ensure columns exist on sensors and rooms tables for backwards compatibility
    # Each statement is wrapped individually so one failure doesn't abort all migrations
    migrations = [
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS name VARCHAR(200)",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS device_id VARCHAR(50)",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS mock_mode VARCHAR(50) DEFAULT 'normal'",
        "ALTER TABLE sensors ALTER COLUMN room_id DROP NOT NULL",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS tapo_ip VARCHAR(50)",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS tapo_username VARCHAR(100)",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS tapo_password VARCHAR(100)",
        "ALTER TABLE sensors ADD COLUMN IF NOT EXISTS tapo_billing_rate NUMERIC DEFAULT 10.0",
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS map_x VARCHAR(20)",
        "ALTER TABLE rooms ADD COLUMN IF NOT EXISTS map_y VARCHAR(20)",
    ]
    for stmt in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(stmt))
                conn.commit()
        except Exception as e:
            logger.warning(f"Migration skipped (may already exist): {stmt[:60]}... — {e}")
            
    logger.info("Database schemas, enums, and tables are ready.")

