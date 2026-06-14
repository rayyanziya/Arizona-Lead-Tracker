"""API tests for the Leads endpoints (list/filter + status update).

The headline guarantee is tenant isolation: a logged-in user must never see or
mutate another tenant's leads.
"""

from __future__ import annotations


async def test_list_requires_authentication(client):
    assert (await client.get("/leads")).status_code == 401


async def test_lists_tenant_leads_newest_first(auth, make_lead):
    await make_lead(tenant_id=auth.tenant_id, external_id="older")
    await make_lead(tenant_id=auth.tenant_id, external_id="newer")
    resp = await auth.client.get("/leads", headers=auth.headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [i["post"]["external_id"] for i in items] == ["newer", "older"]


async def test_does_not_leak_other_tenants_leads(auth, make_lead, seed_user):
    other = await seed_user(email="other@x.com", tenant_slug="other")
    await make_lead(tenant_id=other.tenant_id, external_id="secret")
    await make_lead(tenant_id=auth.tenant_id, external_id="mine")
    resp = await auth.client.get("/leads", headers=auth.headers)
    assert [i["post"]["external_id"] for i in resp.json()["items"]] == ["mine"]


async def test_filter_by_status(auth, make_lead):
    await make_lead(tenant_id=auth.tenant_id, external_id="p", status="pending")
    await make_lead(tenant_id=auth.tenant_id, external_id="g", status="ignored")
    resp = await auth.client.get("/leads?status=ignored", headers=auth.headers)
    assert [i["post"]["external_id"] for i in resp.json()["items"]] == ["g"]


async def test_filter_by_min_score(auth, make_lead):
    await make_lead(tenant_id=auth.tenant_id, external_id="lo", ai_score=4)
    await make_lead(tenant_id=auth.tenant_id, external_id="hi", ai_score=9)
    resp = await auth.client.get("/leads?min_score=7", headers=auth.headers)
    assert [i["post"]["external_id"] for i in resp.json()["items"]] == ["hi"]


async def test_filter_by_platform(auth, make_lead):
    await make_lead(tenant_id=auth.tenant_id, external_id="r", platform="reddit")
    await make_lead(tenant_id=auth.tenant_id, external_id="f", platform="facebook")
    resp = await auth.client.get("/leads?platform=facebook", headers=auth.headers)
    assert [i["post"]["external_id"] for i in resp.json()["items"]] == ["f"]


async def test_pagination_reports_total_and_limits_page(auth, make_lead):
    for n in range(3):
        await make_lead(tenant_id=auth.tenant_id, external_id=f"e{n}")
    resp = await auth.client.get("/leads?limit=2&offset=0", headers=auth.headers)
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


async def test_update_status(auth, make_lead):
    match = await make_lead(tenant_id=auth.tenant_id, external_id="x", status="pending")
    resp = await auth.client.patch(
        f"/leads/{match.id}", json={"status": "responded"}, headers=auth.headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "responded"


async def test_update_rejects_unknown_status(auth, make_lead):
    match = await make_lead(tenant_id=auth.tenant_id, external_id="x")
    resp = await auth.client.patch(
        f"/leads/{match.id}", json={"status": "bogus"}, headers=auth.headers
    )
    assert resp.status_code == 422


async def test_cannot_update_other_tenants_lead(auth, make_lead, seed_user):
    other = await seed_user(email="other@x.com", tenant_slug="other")
    match = await make_lead(tenant_id=other.tenant_id, external_id="x")
    resp = await auth.client.patch(
        f"/leads/{match.id}", json={"status": "ignored"}, headers=auth.headers
    )
    assert resp.status_code == 404