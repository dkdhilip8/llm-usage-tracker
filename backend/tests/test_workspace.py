"""Workspace lifecycle, single-use invites, roles, and member management."""

import datetime as _dt

import pytest


def test_create_makes_the_creator_an_admin(signup):
    c, _ = signup()
    r = c.post("/api/workspace", json={"name": "Acme"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Acme" and body["role"] == "admin"
    assert "members" in body and "invites" in body and "providers" in body
    assert c.get("/api/auth/me").json()["user"]["workspace"]["role"] == "admin"


def test_cannot_create_a_second_workspace(admin_client):
    assert admin_client.post("/api/workspace", json={"name": "Another"}).status_code == 409


def test_invite_is_single_use(admin_client, signup):
    inv = admin_client.post("/api/workspace/invites", json={"label": "Jane"}).json()
    assert inv["code"] and inv["label"] == "Jane"

    c1, _ = signup()
    assert c1.post("/api/workspace/join", json={"code": inv["code"]}).status_code == 200

    c2, _ = signup()
    assert c2.post("/api/workspace/join", json={"code": inv["code"]}).status_code in (404, 409)


def test_expired_invite_rejected(admin_client, signup):
    inv = admin_client.post("/api/workspace/invites", json={}).json()
    from sqlalchemy import update

    from app.db import SessionLocal
    from app.models import WorkspaceInvite

    with SessionLocal() as db:
        db.execute(
            update(WorkspaceInvite)
            .where(WorkspaceInvite.code == inv["code"])
            .values(expires_at=_dt.datetime.now(_dt.UTC) - _dt.timedelta(days=1))
        )
        db.commit()
    c, _ = signup()
    assert c.post("/api/workspace/join", json={"code": inv["code"]}).status_code == 404


def test_revoke_invite(admin_client, signup):
    inv = admin_client.post("/api/workspace/invites", json={}).json()
    assert admin_client.delete(f"/api/workspace/invites/{inv['id']}").status_code == 200
    c, _ = signup()
    assert c.post("/api/workspace/join", json={"code": inv["code"]}).status_code == 404


def test_join_throttle(admin_client, signup, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "JOINS_PER_IP_PER_HOUR", 1)
    admin_client.post("/api/workspace/invites", json={})
    c, _ = signup()
    # first attempt (bad code) still consumes a throttle slot
    c.post("/api/workspace/join", json={"code": "nope"})
    r = c.post("/api/workspace/join", json={"code": "nope-again"})
    assert r.status_code == 429


ADMIN_ROUTES = [
    ("post", "/api/keys", {"label": "x", "allowed_providers": ["openrouter"]}),
    ("get", "/api/requests", None),
    ("get", "/api/workspace/providers", None),
    ("post", "/api/workspace/invites", {}),
    ("patch", "/api/workspace", {"name": "Hacked"}),
    ("delete", "/api/workspace", None),
    ("delete", "/api/workspace/members/1", None),
]


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_member_cannot_reach_admin_routes(admin_client, new_member, method, path, body):
    mc, _ = new_member(admin_client)
    fn = getattr(mc, method)
    r = fn(path, json=body) if body is not None else fn(path)
    assert r.status_code == 403


def test_member_dashboard_is_scoped_to_assigned_keys(admin_client, new_member):
    mc, uid = new_member(admin_client)
    # a key assigned to the member + an unassigned one
    assigned = admin_client.post(
        "/api/keys",
        json={"label": "hers", "allowed_providers": ["openrouter"], "assigned_user_id": uid},
    ).json()
    shared = admin_client.post(
        "/api/keys", json={"label": "shared", "allowed_providers": ["openrouter"]}
    ).json()

    rows = mc.get("/api/keys").json()
    assert [k["label"] for k in rows] == ["hers"]

    # one usage row on each key — the member only sees the one on their key
    import datetime as _dt

    from app.db import SessionLocal
    from app.models import UsageLog

    with SessionLocal() as db:
        for i, kid in enumerate((assigned["id"], shared["id"])):
            db.add(
                UsageLog(
                    key_id=kid, request_id=f"r{i}", provider="openrouter", model="m",
                    prompt_tokens=1, completion_tokens=1, total_tokens=2, cost=0,
                    latency_ms=1, status="success", ts=_dt.datetime.now(_dt.UTC),
                )
            )
        db.commit()

    assert mc.get("/api/usage/summary").json()["total_requests"] == 1
    assert admin_client.get("/api/usage/summary").json()["total_requests"] == 2


def test_remove_member_revokes_access_and_unassigns_keys(admin_client, new_member):
    mc, uid = new_member(admin_client)
    admin_client.post(
        "/api/keys",
        json={"label": "hers", "allowed_providers": ["openrouter"], "assigned_user_id": uid},
    )
    assert admin_client.delete(f"/api/workspace/members/{uid}").status_code == 200
    assert mc.get("/api/usage/summary").status_code == 403
    rows = admin_client.get("/api/keys").json()
    assert rows[0]["assigned_username"] is None


def test_leave_workspace(admin_client, new_member):
    mc, _ = new_member(admin_client)
    assert mc.post("/api/workspace/leave").status_code == 200
    assert mc.get("/api/workspace").status_code == 403


def test_delete_workspace_requires_no_other_members(admin_client, new_member):
    mc, uid = new_member(admin_client)
    assert admin_client.delete("/api/workspace").status_code == 409
    admin_client.delete(f"/api/workspace/members/{uid}")
    assert admin_client.delete("/api/workspace").status_code == 200
    # the admin is now workspace-less again
    assert admin_client.get("/api/auth/me").json()["user"]["workspace"] is None
