"""Per-account live mode: attach your own provider key + a monthly cap, then
your virtual keys can make live calls billed to that key, capped."""

import datetime as _dt


def _mk_key(client, **over):
    body = {"label": "k", "allowed_providers": ["openrouter"], **over}
    r = client.post("/api/keys", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_attach_provider_key_encrypted_and_masked(signup):
    c, _ = signup("owner@example.com")

    r = c.put("/api/account/providers/openai/key", json={"api_key": "sk-my-own-secret-9999"})
    assert r.status_code == 200 and r.json()["last4"] == "9999"
    assert "sk-my-own-secret-9999" not in r.text

    acct = c.get("/api/account").json()
    oi = next(p for p in acct["providers"] if p["provider"] == "openai")
    assert oi["configured"] is True and oi["source"] == "account" and oi["last4"] == "9999"
    assert acct["can_live"] is True

    # encrypted at rest
    from sqlalchemy import select

    from app.crypto import decrypt
    from app.db import SessionLocal
    from app.models import ProviderCredential

    with SessionLocal() as db:
        row = db.scalar(
            select(ProviderCredential).where(ProviderCredential.provider == "openai")
        )
        assert row is not None and row.user_id is not None
        assert "sk-my-own-secret-9999" not in row.ciphertext
        assert decrypt(row.ciphertext) == "sk-my-own-secret-9999"


def test_provider_key_is_per_account(signup):
    a, _ = signup("a@example.com")
    b, _ = signup("b@example.com")
    a.put("/api/account/providers/openai/key", json={"api_key": "sk-a-key-1111"})

    b_acct = b.get("/api/account").json()
    assert all(p["last4"] is None for p in b_acct["providers"])
    assert b_acct["can_live"] is False
    # /me agrees
    assert a.get("/api/auth/me").json()["user"]["can_live"] is True
    assert b.get("/api/auth/me").json()["user"]["can_live"] is False


def test_allow_live_requires_a_provider_key(signup):
    c, _ = signup("live@example.com")
    c.delete("/api/account/data")

    k = _mk_key(c, allow_live=True)
    assert k["allow_live"] is False  # no provider key yet -> forced simulated

    c.put("/api/account/providers/openrouter/key", json={"api_key": "sk-or-v1-mykey1234"})
    k2 = _mk_key(c, allow_live=True)
    assert k2["allow_live"] is True

    # toggling an existing key on now works too
    assert c.patch(f"/api/keys/{k['id']}", json={"allow_live": True}).json()["allow_live"] is True


def test_live_cap_patch_and_clamp(signup):
    c, _ = signup("cap@example.com")
    acct = c.get("/api/account").json()
    assert acct["live_cap_usd"] is None
    assert acct["live_cap_default_usd"] == 5.0 and acct["live_cap_max_usd"] == 10.0

    assert c.patch("/api/account", json={"live_cap_usd": 500}).json()["live_cap_usd"] == 10.0
    assert c.patch("/api/account", json={"live_cap_usd": 3}).json()["live_cap_usd"] == 3.0
    assert c.patch("/api/account", json={"live_cap_usd": -1}).status_code == 422


def test_env_var_blocks_account_key(signup, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-env-key-0000")
    c, _ = signup("env@example.com")
    r = c.put("/api/account/providers/openrouter/key", json={"api_key": "sk-x-12345678"})
    assert r.status_code == 409


def test_live_spend_cap_enforced(signup):
    c, _ = signup("spend@example.com")
    c.delete("/api/account/data")
    c.put("/api/account/providers/openrouter/key", json={"api_key": "sk-or-v1-teamkey"})
    c.patch("/api/account", json={"live_cap_usd": 1})
    k = _mk_key(c, allowed_providers=["openrouter"], allow_live=True)
    assert k["allow_live"] is True

    # record live spend past the $1 cap directly
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

    r = c.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "prompt": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 402
    assert r.json()["detail"]["type"] == "live_cap_exceeded"


def test_clear_provider_key_drops_can_live(signup):
    c, _ = signup("clr@example.com")
    c.put("/api/account/providers/openai/key", json={"api_key": "sk-tmp-key-4321"})
    assert c.get("/api/account").json()["can_live"] is True
    assert c.delete("/api/account/providers/openai/key").json()["provider"] == "openai"
    acct = c.get("/api/account").json()
    assert acct["can_live"] is False
    oi = next(p for p in acct["providers"] if p["provider"] == "openai")
    assert oi["source"] == "none" and oi["last4"] is None


def test_account_endpoints_need_sign_in(client):
    assert client.get("/api/account").status_code == 401
    assert client.patch("/api/account", json={"live_cap_usd": 5}).status_code == 401
    assert client.put("/api/account/providers/openai/key", json={"api_key": "sk-x-12345678"}).status_code == 401
