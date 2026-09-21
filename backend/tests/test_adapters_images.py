"""OpenAI Images passthrough: POST /v1/images/generations. Contract-tested
against captured real response shapes (app.providers.send_request mocked in
conftest.mock_provider) — no live network calls. Generation only — edits/
variations (multipart uploads) are out of scope, see openai_adapter.py.
No streaming — deliberately not attempted this pass."""


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_images_generations_forwards_body_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    body = {
        "model": "dall-e-3",
        "prompt": "a red bicycle leaning on a brick wall",
        "size": "1024x1024",
        "quality": "hd",
        "style": "vivid",
        "n": 1,
        "response_format": "b64_json",
    }
    r = client.post("/v1/images/generations", json=body, headers=_bearer(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert len(j["data"]) == 1

    sent = mock_provider[0][2]
    assert sent["prompt"] == body["prompt"]
    assert sent["size"] == "1024x1024"
    assert sent["quality"] == "hd"
    assert sent["style"] == "vivid"
    assert sent["response_format"] == "b64_json"
    assert mock_provider[0][0].endswith("/v1/images/generations")


def test_images_multiple_n_forwarded_and_all_returned(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-2", "prompt": "a cat", "n": 3},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    assert len(r.json()["data"]) == 3
    assert mock_provider[0][2]["n"] == 3


def test_images_dalle_has_no_usage_cost_is_honestly_unknown(client, admin_client, make_live_key, mock_provider):
    # dall-e-2/3 report no usage at all (real API behavior) -> honest "unknown", never fabricated
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost"] is None and items[0]["cost_source"] == "unknown"
    assert items[0]["prompt_tokens"] == 0 and items[0]["completion_tokens"] == 0


def test_images_gpt_image_1_usage_captured_but_still_unknown_cost(client, admin_client, make_live_key, mock_provider):
    # gpt-image-1 DOES report token usage, but it mixes text+image tokens at
    # different rates our 2-bucket pricing table can't represent honestly ->
    # tokens ARE captured (for usage_raw / a workspace's own pricing), cost
    # stays "unknown" rather than a fabricated blended number.
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 15 and items[0]["completion_tokens"] == 1290
    assert items[0]["cost"] is None and items[0]["cost_source"] == "unknown"

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog

    with SessionLocal() as db:
        row = db.scalar(select(UsageLog).order_by(UsageLog.id.desc()))
        assert row.usage_raw["input_tokens_details"] == {"text_tokens": 15, "image_tokens": 0}


def test_images_workspace_pricing_override_applies_to_gpt_image_1(client, admin_client, make_live_key, mock_provider):
    # ties into the model pricing registry: a workspace that wants accurate
    # gpt-image-1 cost tracking can register its own blended rate.
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "gpt-image-1", "input_per_1m": 5.0, "output_per_1m": 40.0},
    )
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "gpt-image-1", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "workspace"
    expected = round(15 / 1_000_000 * 5.0 + 1290 / 1_000_000 * 40.0, 6)
    assert items[0]["cost"] == expected


def test_images_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["openai"],
        allowed_models={"openai": ["dall-e-3"]},
    )
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-2", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []

    ok = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert ok.status_code == 200


def test_images_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403


def test_images_missing_model_422(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post("/v1/images/generations", json={"prompt": "a cat"}, headers=_bearer(k["key"]))
    assert r.status_code == 422


def test_images_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], monthly_budget_usd=0)
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_images_real_upstream_error_passes_through_status_and_body(client, admin_client, make_live_key, monkeypatch):
    """H1 integration: the new endpoint automatically inherits real
    provider-status passthrough via run_native_completion — no special-casing
    needed, confirming the fix generalizes to a new endpoint for free."""
    import httpx

    provider_body = b'{"error":{"message":"billing_hard_limit_reached","type":"invalid_request_error"}}'

    def boom(*a, **kw):
        request = httpx.Request("POST", "https://example.test/x")
        response = httpx.Response(
            403, content=provider_body, headers={"content-type": "application/json"}, request=request
        )
        raise httpx.HTTPStatusError("HTTP 403", request=request, response=response)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/images/generations",
        json={"model": "dall-e-3", "prompt": "a cat"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403
    assert r.content == provider_body
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "openai"
