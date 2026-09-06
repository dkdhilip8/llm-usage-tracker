"""Encrypted DB-stored provider keys (dev-only feature, ALLOW_DB_PROVIDER_KEYS)."""

from app import providers
from app.config import settings
from app.crypto import decrypt, encrypt


def test_encrypt_roundtrip():
    ct = encrypt("sk-or-v1-secret-value")
    assert ct != "sk-or-v1-secret-value"
    assert decrypt(ct) == "sk-or-v1-secret-value"
    assert decrypt("not-a-valid-token") is None


def test_set_returns_last4_never_the_key(client, admin):
    r = client.put(
        "/api/providers/openrouter/key",
        json={"api_key": "sk-or-v1-abcdefgh1234"},
        headers=admin,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "db" and body["last4"] == "1234"
    assert "sk-or-v1-abcdefgh1234" not in r.text

    rows = client.get("/api/providers", headers=admin).json()
    orr = next(x for x in rows if x["provider"] == "openrouter")
    assert orr["configured"] is True and orr["source"] == "db" and orr["last4"] == "1234"
    assert "abcdefgh" not in client.get("/api/providers", headers=admin).text


def test_key_is_encrypted_at_rest(client, admin):
    client.put(
        "/api/providers/openai/key", json={"api_key": "sk-plaintext-1234"}, headers=admin
    )
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import ProviderCredential

    with SessionLocal() as db:
        row = db.scalar(
            select(ProviderCredential).where(
                ProviderCredential.user_id.is_(None),
                ProviderCredential.provider == "openai",
            )
        )
        assert row is not None
        assert "sk-plaintext-1234" not in row.ciphertext
        assert decrypt(row.ciphertext) == "sk-plaintext-1234"


def test_clear_removes_the_key(client, admin):
    client.put(
        "/api/providers/openrouter/key", json={"api_key": "sk-or-v1-zzzz9999"}, headers=admin
    )
    assert client.delete("/api/providers/openrouter/key", headers=admin).json()["source"] == "none"
    rows = client.get("/api/providers", headers=admin).json()
    assert next(x for x in rows if x["provider"] == "openrouter")["source"] == "none"


def test_env_var_wins_over_db(client, admin, monkeypatch):
    client.put(
        "/api/providers/openrouter/key", json={"api_key": "sk-db-key-5678"}, headers=admin
    )
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-env-key-0000")
    assert providers.resolved_key("openrouter") == "sk-env-key-0000"
    assert providers.key_source("openrouter") == "env"
    # env-set provider cannot be overwritten via the API
    assert (
        client.put(
            "/api/providers/openrouter/key", json={"api_key": "sk-x-9999"}, headers=admin
        ).status_code
        == 409
    )


def test_feature_gate_off_hides_the_endpoints(client, admin, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_DB_PROVIDER_KEYS", False)
    assert (
        client.put(
            "/api/providers/openai/key", json={"api_key": "sk-nope-1234"}, headers=admin
        ).status_code
        == 404
    )
    assert client.delete("/api/providers/openai/key", headers=admin).status_code == 404


def test_endpoints_require_admin(client):
    assert client.put("/api/providers/openai/key", json={"api_key": "sk-nope-1234"}).status_code == 403
    assert client.delete("/api/providers/openai/key").status_code == 403


def test_unknown_provider_422(client, admin):
    assert (
        client.put(
            "/api/providers/googley/key", json={"api_key": "sk-nope-1234"}, headers=admin
        ).status_code
        == 422
    )
