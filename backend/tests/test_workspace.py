"""Workspaces: owner holds the shared provider key, members consume it blind."""

import datetime as _dt


def _mk_key(client, **over):
    body = {"label": "k", "allowed_providers": ["openrouter"], **over}
    r = client.post("/api/keys", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_join(signup):
    owner_c, owner = signup("owner@example.com")
    owner_c.delete("/api/account/data")
    r = owner_c.post("/api/workspace", json={"name": "Acme"})
    assert r.status_code == 200
    ws = r.json()
    assert ws["is_owner"] is True and ws["name"] == "Acme" and ws["member_count"] == 1
    code = ws["join_code"]

    mem_c, _ = signup("mem@example.com")
    mem_c.delete("/api/account/data")
    j = mem_c.post("/api/workspace/join", json={"code": code}).json()
    assert j["is_owner"] is False and j["member_count"] == 2
    assert "join_code" not in j  # members don't get the code

    # can't create/join a second one
    assert owner_c.post("/api/workspace", json={"name": "x"}).status_code == 409
    assert mem_c.post("/api/workspace/join", json={"code": code}).status_code == 409
    other_c, _ = signup("other@example.com")
    assert other_c.post("/api/workspace/join", json={"code": "nope"}).status_code == 404


def test_provider_key_owner_only_and_masked(signup):
    owner_c, _ = signup("o2@example.com")
    owner_c.post("/api/workspace", json={"name": "W"})
    mem_c, _ = signup("m2@example.com")
    code = owner_c.get("/api/workspace").json()["join_code"]
    mem_c.post("/api/workspace/join", json={"code": code})

    r = owner_c.put(
        "/api/workspace/providers/openai/key", json={"api_key": "sk-team-secret-9999"}
    )
    assert r.status_code == 200 and r.json()["last4"] == "9999"
    assert "sk-team-secret-9999" not in r.text

    ow = owner_c.get("/api/workspace").json()
    oi = next(p for p in ow["providers"] if p["provider"] == "openai")
    assert oi["configured"] is True and oi["source"] == "db" and oi["last4"] == "9999"

    mw = mem_c.get("/api/workspace").json()
    mi = next(p for p in mw["providers"] if p["provider"] == "openai")
    assert mi["configured"] is True and mi["last4"] is None  # member can't see last4

    # member cannot set / clear / patch / remove
    assert mem_c.put("/api/workspace/providers/openai/key", json={"api_key": "sk-x-123456"}).status_code == 403
    assert mem_c.delete("/api/workspace/providers/openai/key").status_code == 403
    assert mem_c.patch("/api/workspace", json={"monthly_cap_usd": 9}).status_code == 403


def test_allow_live_only_in_workspace(signup):
    solo_c, _ = signup("solo@example.com")
    solo_c.delete("/api/account/data")
    k = _mk_key(solo_c, allow_live=True)
    assert k["allow_live"] is False  # standalone -> forced simulated

    ws_c, _ = signup("wsuser@example.com")
    ws_c.delete("/api/account/data")
    ws_c.post("/api/workspace", json={"name": "L"})
    k2 = _mk_key(ws_c, allow_live=True)
    assert k2["allow_live"] is True


def test_owner_sees_member_keys_member_does_not(signup):
    owner_c, _ = signup("o3@example.com")
    owner_c.delete("/api/account/data")
    owner_c.post("/api/workspace", json={"name": "W3"})
    code = owner_c.get("/api/workspace").json()["join_code"]
    mem_c, _ = signup("m3@example.com")
    mem_c.delete("/api/account/data")
    mem_c.post("/api/workspace/join", json={"code": code})

    _mk_key(owner_c, label="owner key")
    _mk_key(mem_c, label="member key")

    owner_keys = {k["label"]: k for k in owner_c.get("/api/keys").json()}
    assert set(owner_keys) == {"owner key", "member key"}
    assert owner_keys["member key"]["owner_email"] == "m3@example.com"

    mem_keys = mem_c.get("/api/keys").json()
    assert [k["label"] for k in mem_keys] == ["member key"]
    # owner can revoke a member's key; member can't touch the owner's
    assert owner_c.delete(f"/api/keys/{owner_keys['member key']['id']}").status_code == 200
    assert mem_c.delete(f"/api/keys/{owner_keys['owner key']['id']}").status_code == 404


def test_workspace_dashboard_scoping(signup):
    owner_c, _ = signup("o4@example.com")  # keeps its seeded sample
    owner_c.post("/api/workspace", json={"name": "W4"})
    code = owner_c.get("/api/workspace").json()["join_code"]
    mem_c, _ = signup("m4@example.com")  # keeps its own seeded sample
    mem_c.post("/api/workspace/join", json={"code": code})

    owner_total = owner_c.get("/api/usage/summary").json()["total_requests"]
    member_total = mem_c.get("/api/usage/summary").json()["total_requests"]
    # owner sees the whole workspace, member only their own
    assert owner_total > member_total > 50


def test_spend_cap_enforced(signup):
    owner_c, _ = signup("cap@example.com")
    owner_c.delete("/api/account/data")
    owner_c.post("/api/workspace", json={"name": "Cap"})
    owner_c.patch("/api/workspace", json={"monthly_cap_usd": 1})
    owner_c.put("/api/workspace/providers/openrouter/key", json={"api_key": "sk-or-v1-teamkey"})
    k = _mk_key(owner_c, allowed_providers=["openrouter"], allow_live=True)

    # bump recorded live spend past the $1 cap directly
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog, VirtualKey

    with SessionLocal() as db:
        kid = db.scalar(select(VirtualKey.id).where(VirtualKey.key_prefix == k["key_prefix"]))
        db.add(
            UsageLog(
                key_id=kid, request_id="cap-test-1", provider="openrouter", model="m",
                prompt_tokens=1, completion_tokens=1, total_tokens=2, cost=2,
                cost_source="provider", simulated=False, mode="live", latency_ms=1,
                status="success", ts=_dt.datetime.now(_dt.UTC),
            )
        )
        db.commit()

    r = owner_c.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "prompt": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 402
    assert r.json()["detail"]["type"] == "workspace_cap_exceeded"


def test_leave_and_delete(signup):
    owner_c, _ = signup("o5@example.com")
    owner_c.post("/api/workspace", json={"name": "W5"})
    code = owner_c.get("/api/workspace").json()["join_code"]
    mem_c, _ = signup("m5@example.com")
    mem_c.post("/api/workspace/join", json={"code": code})

    assert owner_c.post("/api/workspace/leave").status_code == 400  # owner can't leave
    assert mem_c.post("/api/workspace/leave").status_code == 200
    assert mem_c.get("/api/workspace").json() == {"workspace": None}
    assert mem_c.get("/api/usage/summary").json()["total_requests"] == 0  # member data dropped

    assert owner_c.delete("/api/workspace").status_code == 200
    assert owner_c.get("/api/workspace").json() == {"workspace": None}
