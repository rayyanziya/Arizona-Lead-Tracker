"""API tests for keyword admin CRUD (tenant-scoped)."""

from __future__ import annotations

from app.models import Keyword


async def test_list_requires_auth(client):
    assert (await client.get("/keywords")).status_code == 401


async def test_create_and_list(auth):
    resp = await auth.client.post(
        "/keywords",
        json={"term": "need erp", "language": "en", "match_type": "phrase"},
        headers=auth.headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["term"] == "need erp"
    assert body["is_active"] is True
    listing = await auth.client.get("/keywords", headers=auth.headers)
    assert [k["term"] for k in listing.json()] == ["need erp"]


async def test_create_duplicate_conflicts(auth):
    payload = {"term": "erp", "language": "any", "match_type": "exact"}
    created = await auth.client.post("/keywords", json=payload, headers=auth.headers)
    assert created.status_code == 201
    dup = await auth.client.post("/keywords", json=payload, headers=auth.headers)
    assert dup.status_code == 409


async def test_create_rejects_bad_enum(auth):
    resp = await auth.client.post(
        "/keywords",
        json={"term": "x", "language": "en", "match_type": "bogus"},
        headers=auth.headers,
    )
    assert resp.status_code == 422


async def test_update_keyword(auth):
    kid = (
        await auth.client.post(
            "/keywords",
            json={"term": "x", "language": "en", "match_type": "phrase"},
            headers=auth.headers,
        )
    ).json()["id"]
    resp = await auth.client.patch(
        f"/keywords/{kid}", json={"is_active": False, "term": "y"}, headers=auth.headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["term"] == "y"


async def test_delete_keyword(auth):
    kid = (
        await auth.client.post(
            "/keywords",
            json={"term": "x", "language": "en", "match_type": "phrase"},
            headers=auth.headers,
        )
    ).json()["id"]
    assert (await auth.client.delete(f"/keywords/{kid}", headers=auth.headers)).status_code == 204
    gone = await auth.client.patch(f"/keywords/{kid}", json={"term": "z"}, headers=auth.headers)
    assert gone.status_code == 404


async def test_cannot_touch_other_tenant_keyword(auth, seed_user, session_factory):
    other = await seed_user(email="o@x.com", tenant_slug="other")
    async with session_factory() as session:
        kw = Keyword(
            tenant_id=other.tenant_id, term="secret", language="en",
            match_type="phrase", is_active=True,
        )
        session.add(kw)
        await session.commit()
        await session.refresh(kw)
        kid = kw.id
    assert (await auth.client.get("/keywords", headers=auth.headers)).json() == []
    assert (
        await auth.client.patch(f"/keywords/{kid}", json={"term": "z"}, headers=auth.headers)
    ).status_code == 404
    assert (await auth.client.delete(f"/keywords/{kid}", headers=auth.headers)).status_code == 404