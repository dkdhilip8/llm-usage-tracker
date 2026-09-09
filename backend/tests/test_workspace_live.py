"""Workspace live mode: the Workspace Admin attaches the workspace's own provider
key(s), each with a monthly cap; the workspace's virtual keys then make live calls
on those keys, capped per provider."""

import datetime as _dt


def _mk_key(c, **over):
    body = {"label": "k", "allowed_providers": ["openrouter"], **over}
    r = c.post("/api/keys", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _prov(c, provider):
    return next(p for p in c.get("/api/workspace/providers").json() if p["provider"] == provider)


def test_attach_provider_key_encrypted_and_masked(admin_client, new_admin):
    r = admin_client.put(
        "/api/workspace/providers/openai/key", json={"api_key": "sk-my-own-secret-9999"}
    )
    assert r.status_code == 200 and r.json()["last4"] == "9999"
    assert "sk-my-own-secret-9999" not in r.text

    oi = _prov(admin_client, "openai")
    assert oi["configured"] is True and oi["source"] == "workspace" and oi["last4"] == "9999"

    from sqlalchemy import select

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import ProviderCredential

    with SessionLocal() as db:
        row = db.scalar(select(ProviderCredential).where(ProviderCredential.provider == "openai"))
        assert row is not None and row.workspace_id is not None
        assert "sk-my-own-secret-9999" not in row.ciphertext
        assert decrypt(row.ciphertext) == "sk-my-own-secret-9999"


def test_provider_key_is_per_workspace(new_admin):
    a, _ = new_admin()
    b, _ = new_admin()
    a.put("/api/workspace/providers/openai/key", json={"api_key": "sk-a-key-1111"})
    assert all(p["last4"] is None for p in b.get("/api/workspace/providers").json())


def test_allow_live_requires_a_provider_key(admin_client):
    k = _mk_key(admin_client, allow_live=True)
    assert k["allow_live"] is False

    admin_client.put(
        "/api/workspace/providers/openrouter/key", json={"api_key": "sk-or-v1-mykey1234"}
    )
    k2 = _mk_key(admin_client, allow_live=True)
    assert k2["allow_live"] is True
    assert admin_client.patch(f"/api/keys/{k['id']}", json={"allow_live": True}).json()["allow_live"] is True


def test_allow_live_gated_on_the_keys_own_providers(admin_client):
    admin_client.put(
        "/api/workspace/providers/anthropic/key", json={"api_key": "sk-ant-mine-1234"}
    )
    assert _mk_key(admin_client, allowed_providers=["openai"], allow_live=True)["allow_live"] is False
    assert _mk_key(admin_client, allowed_providers=["anthropic"], allow_live=True)["allow_live"] is True
    assert (
        _mk_key(admin_client, allowed_providers=["openai", "anthropic"], allow_live=True)["allow_live"]
        is True
    )


def test_provider_cap_set_and_clear(admin_client):
    assert admin_client.get("/api/account").json()["live_cap_default_usd"] == 5.0
    assert _prov(admin_client, "openai")["monthly_cap_usd"] is None

    # can't cap a provider with no key
    assert admin_client.patch(
        "/api/workspace/providers/openai/cap", json={"monthly_cap_usd": 20}
    ).status_code == 404

    admin_client.put("/api/workspace/providers/openai/key", json={"api_key": "sk-openai-mine-1"})
    r = admin_client.patch("/api/workspace/providers/openai/cap", json={"monthly_cap_usd": 500})
    assert r.status_code == 200
    assert next(p for p in r.json() if p["provider"] == "openai")["monthly_cap_usd"] == 500.0
    assert admin_client.patch(
        "/api/workspace/providers/openai/cap", json={"monthly_cap_usd": -1}
    ).status_code == 422
    admin_client.patch("/api/workspace/providers/openai/cap", json={"monthly_cap_usd": None})
    assert _prov(admin_client, "openai")["monthly_cap_usd"] is None


def test_env_var_blocks_workspace_key(admin_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-env-key-0000")
    r = admin_client.put(
        "/api/workspace/providers/openrouter/key", json={"api_key": "sk-x-12345678"}
    )
    assert r.status_code == 409


def test_live_spend_cap_is_per_provider(admin_client, mock_provider):
    admin_client.put(
        "/api/workspace/providers/openrouter/key", json={"api_key": "sk-or-v1-teamkey"}
    )
    admin_client.put(
        "/api/workspace/providers/anthropic/key", json={"api_key": "sk-ant-mine-2"}
    )
    admin_client.patch("/api/workspace/providers/openrouter/cap", json={"monthly_cap_usd": 1})
    k = _mk_key(admin_client, allowed_providers=["openrouter", "anthropic"], allow_live=True)
    assert k["allow_live"] is True

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog, VirtualKey

    with SessionLocal() as db:
        kid = db.scalar(select(VirtualKey.id).where(VirtualKey.key_prefix == k["key_prefix"]))
        db.add(
            UsageLog(
                key_id=kid, request_id="cap-1", provider="openrouter", model="m",
                prompt_tokens=1, completion_tokens=1, total_tokens=2, cost=2,
                cost_source="provider", simulated=False, mode="live", latency_ms=1,
                status="success", ts=_dt.datetime.now(_dt.UTC),
            )
        )
        db.commit()

    hdr = {"Authorization": f"Bearer {k['key']}"}
    r = admin_client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "prompt": "hi"},
        headers=hdr,
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "live_cap_exceeded"
    assert "openrouter" in r.json()["detail"]["message"]

    assert admin_client.post(
        "/v1/proxy/chat",
        json={"provider": "anthropic", "model": "claude-3-5-haiku", "prompt": "hi"},
        headers=hdr,
    ).status_code == 200


def test_clear_provider_key(admin_client):
    admin_client.put("/api/workspace/providers/openai/key", json={"api_key": "sk-tmp-key-4321"})
    assert _prov(admin_client, "openai")["source"] == "workspace"
    assert admin_client.delete("/api/workspace/providers/openai/key").json()["provider"] == "openai"
    oi = _prov(admin_client, "openai")
    assert oi["source"] == "none" and oi["last4"] is None


def test_member_cannot_touch_provider_endpoints(admin_client, new_member):
    mc, _ = new_member(admin_client)
    assert mc.get("/api/workspace/providers").status_code == 403
    assert mc.put(
        "/api/workspace/providers/openai/key", json={"api_key": "sk-x-12345678"}
    ).status_code == 403
    assert mc.patch(
        "/api/workspace/providers/openai/cap", json={"monthly_cap_usd": 5}
    ).status_code == 403


def test_workspace_endpoints_need_membership(client, signup):
    assert client.get("/api/workspace").status_code == 401
    c, _ = signup()
    assert c.get("/api/workspace").status_code == 403
    assert c.get("/api/workspace/providers").status_code == 403
