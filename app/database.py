import os
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# Load environment variables (e.g., from .env file)
load_dotenv()

# Fetch the connection string from the environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

# Initialize the asynchronous engine with lean connection pooling for Render 512MB RAM ceiling
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,      # Tests connection liveness before every query
    pool_recycle=300,        # Recycles connections every 5 minutes to prevent stale drops
    pool_size=2,             # Lean pool prevents multi-socket SSL buffer bloat
    max_overflow=3,          # Caps maximum burst connections at 5
    connect_args={
        "statement_cache_size": 0,          # Disables asyncpg primary cache (Required for Supavisor)
        "prepared_statement_cache_size": 0, # Disables secondary LRU cache (Required for Supavisor)
        "timeout": 30                       # Fails fast on network drops rather than hanging
    }
)

# Create a configured "Session" class for async database interactions
AsyncSessionLocal = sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

# Base class for the declarative memory models
Base = declarative_base()

# Dependency generator to yield database sessions for FastAPI endpoints
async def get_db_session():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()