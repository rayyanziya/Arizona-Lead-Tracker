"""API tests for /auth/login and the get_current_user guard (/auth/me)."""

from __future__ import annotations


async def test_login_returns_a_bearer_token(client, seed_user):
    await seed_user(email="a@b.com", password="secret123")
    resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "secret123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


async def test_login_rejects_wrong_password(client, seed_user):
    await seed_user(email="a@b.com", password="secret123")
    resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "WRONG"})
    assert resp.status_code == 401


async def test_login_rejects_unknown_email(client, seed_user):
    await seed_user(email="a@b.com", password="secret123")
    resp = await client.post("/auth/login", json={"email": "nobody@b.com", "password": "secret123"})
    assert resp.status_code == 401


async def test_login_rejects_inactive_user(client, seed_user):
    await seed_user(email="a@b.com", password="secret123", is_active=False)
    resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "secret123"})
    assert resp.status_code == 401


async def test_me_returns_current_user_with_valid_token(client, seed_user):
    await seed_user(email="a@b.com", password="secret123", role="owner")
    token = (
        await client.post("/auth/login", json={"email": "a@b.com", "password": "secret123"})
    ).json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "a@b.com"
    assert body["role"] == "owner"


async def test_me_requires_authentication(client):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(client):
    resp = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401