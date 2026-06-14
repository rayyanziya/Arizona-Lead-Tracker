"""Async API test harness: in-memory aiosqlite + ASGI transport.

These tests exercise the real FastAPI app (routing, dependencies, status codes)
with no Postgres/Redis/network: get_db is overridden to a StaticPool in-memory
aiosqlite engine, so a single shared connection keeps the schema across sessions.
asyncio_mode=auto (pyproject) runs the async fixtures/tests directly.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models import Base, Tenant, User


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed_user(session_factory):
    async def _make(
        *,
        email="admin@example.com",
        password="secret123",
        role="owner",
        full_name="Admin",
        is_active=True,
        tenant_active=True,
        tenant_slug="acme",
    ):
        async with session_factory() as session:
            tenant = Tenant(name="Acme", slug=tenant_slug, is_active=tenant_active)
            session.add(tenant)
            await session.flush()
            user = User(
                tenant_id=tenant.id,
                email=email,
                hashed_password=hash_password(password),
                role=role,
                full_name=full_name,
                is_active=is_active,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return _make