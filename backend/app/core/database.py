"""Database engines and session factories.

Two engines on purpose:
  * async (asyncpg)  -> FastAPI request handlers
  * sync  (psycopg)  -> Celery tasks and Alembic migrations
Mixing async sessions into Celery's prefork workers is a known footgun, so the
collectors/pipeline use the plain sync session via ``session_scope()``.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# Engines are built lazily (and cached) rather than at import time: instantiating
# an engine imports its DBAPI (asyncpg / psycopg), so deferring it keeps the app
# importable wherever a driver is absent -- the API tests, which override get_db
# against in-memory SQLite, and any tooling that imports app.main without a DB.


# --- Async (FastAPI) ---
@lru_cache(maxsize=1)
def get_async_sessionmaker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url_async, pool_pre_ping=True, future=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async session."""
    async with get_async_sessionmaker()() as session:
        yield session


# --- Sync (Celery / Alembic) ---
@lru_cache(maxsize=1)
def get_sync_sessionmaker() -> sessionmaker[Session]:
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True, future=True)
    return sessionmaker(engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for Celery tasks: commit on success, rollback on error."""
    session = get_sync_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
