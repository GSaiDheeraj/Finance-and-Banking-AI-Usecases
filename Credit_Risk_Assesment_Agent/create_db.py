#!/usr/bin/env python
"""Create the credit_risk database and initialize schema if it doesn't exist."""
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# Load environment variables
import dotenv
dotenv.load_dotenv()

# Default Postgres connection (without database)
PG_HOST = os.getenv("PG_HOST", "127.0.0.1")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "")
PG_DATABASE = os.getenv("PG_DATABASE", "credit_risk")

def create_database():
    """Create the credit_risk database if it doesn't exist."""
    # Connect to default postgres database
    auth = f"{PG_USER}:{PG_PASSWORD}" if PG_PASSWORD else PG_USER
    default_url = f"postgresql+psycopg://{auth}@{PG_HOST}:{PG_PORT}/postgres"
    
    engine = create_engine(default_url, isolation_level="AUTOCOMMIT")
    
    try:
        with engine.connect() as conn:
            # Check if database exists
            result = conn.execute(text(
                "SELECT 1 FROM pg_database WHERE datname = :dbname"
            ), {"dbname": PG_DATABASE})
            
            if result.fetchone() is None:
                print(f"Creating database '{PG_DATABASE}'...")
                conn.execute(text(f"CREATE DATABASE {PG_DATABASE}"))
                print(f"Database '{PG_DATABASE}' created successfully.")
            else:
                print(f"Database '{PG_DATABASE}' already exists.")
    except OperationalError as e:
        print(f"Error connecting to Postgres: {e}")
        print("Make sure PostgreSQL is running and credentials are correct.")
        sys.exit(1)
    finally:
        engine.dispose()

def init_schema():
    """Initialize the schema (create tables)."""
    # Now connect to the credit_risk database
    auth = f"{PG_USER}:{PG_PASSWORD}" if PG_PASSWORD else PG_USER
    db_url = f"postgresql+psycopg://{auth}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    
    engine = create_engine(db_url)
    
    try:
        with engine.begin() as conn:
            # Create pgvector extension
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print("pgvector extension created/verified.")
    except OperationalError as e:
        print(f"Error creating extension: {e}")
        sys.exit(1)
    finally:
        engine.dispose()
    
    # Now run the full schema init from models
    from credit_risk.db.models import init_schema as models_init_schema
    models_init_schema()
    print("Schema initialized successfully.")

if __name__ == "__main__":
    print("Setting up credit_risk database...")
    create_database()
    init_schema()
    print("Done!")