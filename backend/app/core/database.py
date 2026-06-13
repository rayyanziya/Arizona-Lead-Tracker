"""Database engines and session factories.

Two engines on purpose:
  * async (asyncpg)  -> FastAPI request handlers
  * sync  (psycopg)  -> Celery tasks and Alembic migrations
Mixing async sessions into Celery's prefork workers is a known footgun, so the
collectors/pipeline use the plain sync session via ``session_scope()``.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# --- Async (FastAPI) ---
async_engine = create_async_engine(
    settings.database_url_async, pool_pre_ping=True, future=True
)
AsyncSessionLocal = async_sessionmaker(
    async_engine, expire_on_commit=False, class_=AsyncSession
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async session."""
    async with AsyncSessionLocal() as session:
        yield session


# --- Sync (Celery / Alembic) ---
sync_engine = create_engine(settings.database_url_sync, pool_pre_ping=True, future=True)
SyncSessionLocal = sessionmaker(sync_engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Transactional scope for Celery tasks: commit on success, rollback on error."""
    session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
