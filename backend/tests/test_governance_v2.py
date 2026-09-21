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


# ---- workspace-owned model pricing registry ----
def test_upsert_model_pricing_creates_then_updates_in_place(admin_client):
    body = {"provider": "openai", "model": "my-finetune", "input_per_1m": 1.0, "output_per_1m": 2.0}
    created = admin_client.put("/api/workspace/pricing", json=body)
    assert created.status_code == 200
    row = created.json()
    assert row["input_per_1m"] == 1.0 and row["output_per_1m"] == 2.0

    updated = admin_client.put(
        "/api/workspace/pricing",
        json={**body, "input_per_1m": 5.0, "output_per_1m": 6.0},
    )
    assert updated.status_code == 200
    row2 = updated.json()
    assert row2["id"] == row["id"]  # same (workspace, provider, model) -> update, not a new row
    assert row2["input_per_1m"] == 5.0 and row2["output_per_1m"] == 6.0

    listed = admin_client.get("/api/workspace/pricing").json()
    assert len(listed) == 1 and listed[0]["id"] == row["id"]


def test_model_pricing_invalid_provider_422(admin_client):
    r = admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "not-a-provider", "model": "x", "input_per_1m": 1, "output_per_1m": 1},
    )
    assert r.status_code == 422


def test_delete_model_pricing(admin_client):
    row = admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "x", "input_per_1m": 1, "output_per_1m": 1},
    ).json()
    assert admin_client.delete(f"/api/workspace/pricing/{row['id']}").status_code == 200
    assert admin_client.get("/api/workspace/pricing").json() == []
    assert admin_client.delete(f"/api/workspace/pricing/{row['id']}").status_code == 404


def test_delete_model_pricing_cross_workspace_404(admin_client, new_admin):
    row = admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "x", "input_per_1m": 1, "output_per_1m": 1},
    ).json()
    other_admin, _ = new_admin("Other Co")
    # another workspace's admin cannot see or delete this row — tenant isolation
    assert row["id"] not in [r["id"] for r in other_admin.get("/api/workspace/pricing").json()]
    assert other_admin.delete(f"/api/workspace/pricing/{row['id']}").status_code == 404
    assert admin_client.get("/api/workspace/pricing").json()  # untouched


def test_model_pricing_limit_enforced(admin_client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "MAX_MODEL_PRICING_PER_WORKSPACE", 1)
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "a", "input_per_1m": 1, "output_per_1m": 1},
    )
    r = admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "b", "input_per_1m": 1, "output_per_1m": 1},
    )
    assert r.status_code == 409


def test_workspace_pricing_override_used_for_cost(client, admin_client, make_live_key, mock_provider):
    # unregistered model -> would normally be "unknown"/None
    model = "totally-custom-finetune"
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openrouter", "model": model, "input_per_1m": 2.0, "output_per_1m": 4.0},
    )
    k = make_live_key()
    j = _chat(client, k["key"], model=model).json()
    assert j["cost_source"] == "workspace"
    # mock_provider's fake reply is echoed as prompt tokens/completion tokens by
    # the legacy call_provider path (5 prompt, 7 completion by convention elsewhere) —
    # just assert the number is computed from the override rate, not zero/unknown
    assert j["cost"] is not None and j["cost"] > 0
    assert j["pricing"]["source"] == "workspace"
    assert j["pricing"]["input_per_1m"] == 2.0 and j["pricing"]["output_per_1m"] == 4.0

    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "workspace"


def test_workspace_pricing_override_does_not_leak_across_workspaces(client, admin_client, new_admin, make_live_key, mock_provider):
    model = "totally-custom-finetune-2"
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openrouter", "model": model, "input_per_1m": 2.0, "output_per_1m": 4.0},
    )
    other_admin, _ = new_admin("Other Co 2")
    for p in ("openrouter", "openai", "anthropic", "gemini"):
        other_admin.put(f"/api/workspace/providers/{p}/key", json={"api_key": f"sk-fake-{p}-000000"})
    other_key = other_admin.post(
        "/api/keys", json={"label": "t", "allowed_providers": ["openrouter"], "allow_live": True}
    ).json()

    j = _chat(client, other_key["key"], model=model).json()
    assert j["cost_source"] != "workspace"  # the first workspace's override must not apply here


def test_workspace_pricing_override_never_supersedes_a_real_provider_cost(client, admin_client, make_live_key, monkeypatch):
    """M8: record_usage only applies a workspace override when cost_source is
    "configured"/"unknown" (gateway.py) — a real provider-reported charge
    (cost_source "provider") must win even when an override also exists for
    that exact (provider, model). This proves it, using a model that's BOTH
    registered in CONFIGURED_PRICING and has a workspace override set, so a
    regression (the override winning) would be unambiguous."""
    model = "meta-llama/llama-3.3-70b-instruct"  # registered in CONFIGURED_PRICING too
    real_provider_cost = 0.123456  # distinct from both the override and the configured-table price

    def fake_call_provider(provider, model, prompt, api_key=None):
        return (f"reply to: {prompt}", 5, 7, real_provider_cost)

    monkeypatch.setattr("app.providers.call_provider", fake_call_provider)

    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openrouter", "model": model, "input_per_1m": 999.0, "output_per_1m": 999.0},
    )
    k = make_live_key()
    j = _chat(client, k["key"], model=model).json()

    assert j["cost_source"] == "provider"
    assert j["cost"] == round(real_provider_cost, 6)  # the real charge, untouched by the override
    assert j["pricing"]["source"] == "openrouter"  # the provider's own name, not "workspace"
    # the override's rate (999.0) never got picked up for the pricing display either
    assert j["pricing"]["input_per_1m"] == 0.10 and j["pricing"]["output_per_1m"] == 0.32

    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "provider"
    assert items[0]["cost"] == round(real_provider_cost, 6)
