"""API tests for monitored-source admin CRUD (tenant-scoped)."""

from __future__ import annotations

from app.models import MonitoredSource


async def test_list_requires_auth(client):
    assert (await client.get("/sources")).status_code == 401


async def test_create_and_list(auth):
    resp = await auth.client.post(
        "/sources",
        json={
            "platform": "reddit",
            "identifier": "https://www.reddit.com/r/Phoenix",
            "label": "PHX",
        },
        headers=auth.headers,
    )
    assert resp.status_code == 201
    assert resp.json()["platform"] == "reddit"
    listing = await auth.client.get("/sources", headers=auth.headers)
    assert [s["identifier"] for s in listing.json()] == ["https://www.reddit.com/r/Phoenix"]


async def test_create_duplicate_conflicts(auth):
    payload = {"platform": "facebook", "identifier": "https://facebook.com/groups/1"}
    first = await auth.client.post("/sources", json=payload, headers=auth.headers)
    assert first.status_code == 201
    dup = await auth.client.post("/sources", json=payload, headers=auth.headers)
    assert dup.status_code == 409


async def test_create_rejects_bad_platform(auth):
    resp = await auth.client.post(
        "/sources", json={"platform": "myspace", "identifier": "x"}, headers=auth.headers
    )
    assert resp.status_code == 422


async def test_update_source(auth):
    sid = (
        await auth.client.post(
            "/sources", json={"platform": "reddit", "identifier": "r/x"}, headers=auth.headers
        )
    ).json()["id"]
    resp = await auth.client.patch(
        f"/sources/{sid}", json={"is_active": False, "label": "off"}, headers=auth.headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False
    assert resp.json()["label"] == "off"


async def test_delete_source(auth):
    sid = (
        await auth.client.post(
            "/sources", json={"platform": "reddit", "identifier": "r/x"}, headers=auth.headers
        )
    ).json()["id"]
    assert (await auth.client.delete(f"/sources/{sid}", headers=auth.headers)).status_code == 204
    assert (
        await auth.client.patch(f"/sources/{sid}", json={"label": "z"}, headers=auth.headers)
    ).status_code == 404


async def test_cannot_touch_other_tenant_source(auth, seed_user, session_factory):
    other = await seed_user(email="o@x.com", tenant_slug="other")
    async with session_factory() as session:
        src = MonitoredSource(
            tenant_id=other.tenant_id, platform="reddit", identifier="secret", is_active=True
        )
        session.add(src)
        await session.commit()
        await session.refresh(src)
        sid = src.id
    assert (await auth.client.get("/sources", headers=auth.headers)).json() == []
    assert (
        await auth.client.patch(f"/sources/{sid}", json={"label": "z"}, headers=auth.headers)
    ).status_code == 404
    assert (await auth.client.delete(f"/sources/{sid}", headers=auth.headers)).status_code == 404