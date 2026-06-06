"""PostgreSQL connection for Monitoring API — uses 'monitoring' schema."""
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from api.config import DATABASE_URL
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
    logger.info("Database schemas, enums, and tables are ready.")
