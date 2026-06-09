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
    """Create schemas, enum types, and all tables on a fresh database."""
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS monitoring"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        # Create enum types manually before create_all
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE monitoring.room_type AS ENUM ('room', 'fridge', 'freezer');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))
        conn.execute(text("""
            DO $$ BEGIN
                CREATE TYPE monitoring.sensor_type AS ENUM ('temperature', 'humidity');
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
        """))
        conn.commit()
    
    # Now create all tables
    Base.metadata.create_all(bind=engine)
    
    # Ensure columns name, device_id, and mock_mode exist on sensors table for backwards compatibility
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE monitoring.sensors ADD COLUMN IF NOT EXISTS name VARCHAR(200)"))
            conn.execute(text("ALTER TABLE monitoring.sensors ADD COLUMN IF NOT EXISTS device_id VARCHAR(50)"))
            conn.execute(text("ALTER TABLE monitoring.sensors ADD COLUMN IF NOT EXISTS mock_mode VARCHAR(50) DEFAULT 'normal'"))
            # Fix room_id to be nullable (model says nullable=True, but old DB may have NOT NULL)
            conn.execute(text("ALTER TABLE monitoring.sensors ALTER COLUMN room_id DROP NOT NULL"))
            
            # Add map coordinates to rooms table
            conn.execute(text("ALTER TABLE monitoring.rooms ADD COLUMN IF NOT EXISTS map_x VARCHAR(20)"))
            conn.execute(text("ALTER TABLE monitoring.rooms ADD COLUMN IF NOT EXISTS map_y VARCHAR(20)"))
            
            conn.commit()
            logger.info("Checked/added columns and fixed constraints on sensors and rooms tables.")
        except Exception as e:
            logger.error(f"Failed to check/add columns: {e}")
            
    logger.info("Database schemas, enums, and tables are ready.")
