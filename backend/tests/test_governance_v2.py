"""Phase-1 governance additions: virtual-key expiry, per-model ACL, and honest
(never-fabricated) cost accounting — exercised through the pre-existing
/v1/proxy/chat surface so these are provider/adapter-agnostic."""

import datetime as _dt

OR_MODEL = "meta-llama/llama-3.3-70b-instruct"


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _chat(client, key: str, model: str = OR_MODEL):
    return client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": model, "prompt": "hi"},
        headers=_bearer(key),
    )


# ---- expiry ----
def test_key_with_future_expiry_still_works(client, make_live_key, mock_provider):
    future = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=1)).isoformat()
    k = make_live_key(expires_at=future)
    assert _chat(client, k["key"]).status_code == 200


def test_expired_key_rejected_401(client, admin_client, make_live_key):
    k = make_live_key()
    # flip it to already-expired via a direct update (PATCH doesn't expose expiry
    # shortening below "now" through validation, so go straight at the model)
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import VirtualKey

    with SessionLocal() as db:
        vk = db.scalar(select(VirtualKey).where(VirtualKey.key_prefix == k["key_prefix"]))
        vk.expires_at = _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=1)
        db.commit()

    r = _chat(client, k["key"])
    assert r.status_code == 401
    assert "revoked" in r.json()["detail"] or "invalid" in r.json()["detail"]
    # same generic message as a wholly invalid key — no state leak
    other = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hi"},
        headers=_bearer("vk_not-a-real-key-at-all"),
    )
    assert other.json()["detail"] == r.json()["detail"]


def test_create_key_with_expiry_persists(admin_client):
    future = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=7)).isoformat()
    r = admin_client.post(
        "/api/keys",
        json={"label": "temp", "allowed_providers": ["openrouter"], "expires_at": future},
    )
    assert r.status_code == 200
    assert r.json()["expires_at"] is not None
    rows = admin_client.get("/api/keys").json()
    assert rows[0]["expires_at"] is not None


def test_clear_expiry_via_patch(admin_client):
    future = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=7)).isoformat()
    created = admin_client.post(
        "/api/keys",
        json={"label": "temp", "allowed_providers": ["openrouter"], "expires_at": future},
    ).json()
    r = admin_client.patch(f"/api/keys/{created['id']}", json={"clear_expiry": True})
    assert r.status_code == 200 and r.json()["expires_at"] is None


# ---- per-model ACL ----
def test_no_allowed_models_is_permissive_by_default(client, make_live_key, mock_provider):
    k = make_live_key()  # no allowed_models set
    assert _chat(client, k["key"], model="literally-any-model-string").status_code == 200


def test_allowed_models_restricts_to_the_list(client, make_live_key, mock_provider):
    k = make_live_key(allowed_models={"openrouter": [OR_MODEL]})
    assert _chat(client, k["key"], model=OR_MODEL).status_code == 200
    r = _chat(client, k["key"], model="some-other-model")
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"


def test_allowed_models_rejects_provider_not_in_allowed_providers(admin_client):
    r = admin_client.post(
        "/api/keys",
        json={
            "label": "bad",
            "allowed_providers": ["openrouter"],
            "allowed_models": {"anthropic": ["claude-3-5-haiku"]},
        },
    )
    assert r.status_code == 422


def test_update_allowed_models_and_clear(admin_client, make_key):
    k = make_key(allowed_providers=["openrouter"])
    r = admin_client.patch(
        f"/api/keys/{k['id']}", json={"allowed_models": {"openrouter": [OR_MODEL]}}
    )
    assert r.status_code == 200
    r2 = admin_client.patch(f"/api/keys/{k['id']}", json={"clear_allowed_models": True})
    assert r2.status_code == 200


# ---- honest cost accounting ----
def test_unregistered_model_cost_is_unknown_not_fabricated(client, admin_client, make_live_key, mock_provider):
    k = make_live_key()
    r = _chat(client, k["key"], model="totally-unregistered-model-xyz")
    assert r.status_code == 200
    j = r.json()
    assert j["cost_source"] == "unknown" and j["cost"] is None
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost"] is None and items[0]["cost_source"] == "unknown"
    # aggregates still compute (NULL cost coalesces to 0 in every SUM)
    summary = admin_client.get("/api/usage/summary").json()
    assert summary["total_requests"] == 1
    assert summary["total_cost"] >= 0


def test_registered_model_still_estimates_cost(client, make_live_key, mock_provider):
    k = make_live_key()
    j = _chat(client, k["key"], model=OR_MODEL).json()  # registered in CONFIGURED_PRICING
    assert j["cost_source"] == "configured" and j["cost"] is not None and j["cost"] >= 0
