"""Async API test harness: in-memory aiosqlite + ASGI transport.

These tests exercise the real FastAPI app (routing, dependencies, status codes)
with no Postgres/Redis/network: get_db is overridden to a StaticPool in-memory
aiosqlite engine, so a single shared connection keeps the schema across sessions.
asyncio_mode=auto (pyproject) runs the async fixtures/tests directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models import Base, Match, Post, Tenant, User


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


@pytest_asyncio.fixture
async def auth(client, seed_user):
    """A logged-in owner: returns the client, auth header, and the user/tenant."""
    user = await seed_user()
    token = (
        await client.post(
            "/auth/login", json={"email": "admin@example.com", "password": "secret123"}
        )
    ).json()["access_token"]
    return SimpleNamespace(
        client=client,
        headers={"Authorization": f"Bearer {token}"},
        user=user,
        tenant_id=user.tenant_id,
    )


@pytest_asyncio.fixture
async def make_lead(session_factory):
    """Insert a Post + its Match (a lead) in the given tenant; returns the Match."""

    async def _make(
        *,
        tenant_id,
        external_id="x1",
        platform="reddit",
        status="pending",
        ai_score=8,
        ai_is_buyer=True,
        author="someuser",
        title="Need help",
        body="looking for a developer to build an app",
        url=None,
        matched_term="erp",
    ):
        async with session_factory() as session:
            post = Post(
                tenant_id=tenant_id,
                platform=platform,
                external_id=external_id,
                url=url or f"https://example.com/{external_id}",
                author=author,
                title=title,
                body=body,
                content_hash=f"{abs(hash(external_id)):064d}"[:64],
            )
            session.add(post)
            await session.flush()
            match = Match(
                tenant_id=tenant_id,
                post_id=post.id,
                status=status,
                ai_score=ai_score,
                ai_is_buyer=ai_is_buyer,
                matched_term=matched_term,
            )
            session.add(match)
            await session.commit()
            await session.refresh(match)
            return match

    return _make