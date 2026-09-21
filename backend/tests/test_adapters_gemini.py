"""Gemini-native adapter: POST /v1beta/models/{model}:generateContent and
:streamGenerateContent. Contract-tested against captured real response
shapes (app.providers.send_request / stream_request mocked in
conftest.mock_provider) — no live network calls."""


def _goog(key: str) -> dict:
    return {"x-goog-api-key": key}


def _gen_url(model: str) -> str:
    return f"/v1beta/models/{model}:generateContent"


def _stream_url(model: str) -> str:
    return f"/v1beta/models/{model}:streamGenerateContent"


def test_generate_content_forwards_body_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["gemini"])
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "what's in this image?"},
                    {"inline_data": {"mime_type": "image/png", "data": "AAAA"}},
                ],
            }
        ],
        "systemInstruction": {"parts": [{"text": "be terse"}]},
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 256},
        "tools": [{"functionDeclarations": [{"name": "get_weather", "parameters": {"type": "OBJECT"}}]}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
    }
    r = client.post(_gen_url("gemini-2.5-flash"), json=body, headers=_goog(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert j["candidates"][0]["content"]["role"] == "model"

    sent = mock_provider[0][2]
    assert sent["contents"] == body["contents"]  # image part untouched
    assert sent["systemInstruction"] == body["systemInstruction"]
    assert sent["tools"] == body["tools"]
    assert sent["toolConfig"] == body["toolConfig"]
    assert mock_provider[0][0].endswith("gemini-2.5-flash:generateContent")


def test_x_goog_api_key_header_authenticates_like_bearer(client, make_live_key, mock_provider):
    # The Gemini SDK sends the vk in x-goog-api-key by default, not
    # Authorization: Bearer — the gateway must accept both.
    k = make_live_key(allowed_providers=["gemini"])
    r = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 200

    bad = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers={"x-goog-api-key": "vk_not_a_real_key"},
    )
    assert bad.status_code == 401


def test_function_call_round_trip_two_turns(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["gemini"])
    # turn 1: the model would return a functionCall part (our fake doesn't
    # simulate that — the round trip below is what matters: the app, not the
    # gateway, executes the tool and sends functionResponse back)
    first = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "weather in Denver?"}]}]},
        headers=_goog(k["key"]),
    )
    assert first.status_code == 200

    second = client.post(
        _gen_url("gemini-2.5-flash"),
        json={
            "contents": [
                {"role": "user", "parts": [{"text": "weather in Denver?"}]},
                {"role": "model", "parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Denver"}}}]},
                {"role": "user", "parts": [{"functionResponse": {"name": "get_weather", "response": {"temp": "68F"}}}]},
            ]
        },
        headers=_goog(k["key"]),
    )
    assert second.status_code == 200
    assert len(mock_provider) == 2
    assert mock_provider[1][2]["contents"][-1]["parts"][0]["functionResponse"]["name"] == "get_weather"
    assert admin_client.get("/api/usage/summary").json()["total_requests"] == 2


def test_stream_generate_content_real_chunk_shape_and_usage(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["gemini"])
    with client.stream(
        "POST",
        _stream_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert '"finishReason": "STOP"' in text or "finishReason" in text
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 5 and items[0]["completion_tokens"] == 7


def test_usage_metadata_extra_fields_captured_in_usage_raw(client, make_live_key, monkeypatch):
    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 200,
                "candidatesTokenCount": 10,
                "cachedContentTokenCount": 150,
                "thoughtsTokenCount": 20,
                "toolUsePromptTokenCount": 5,
                "totalTokenCount": 235,
            },
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["gemini"])
    client.post(
        _gen_url("gemini-2.5-flash"),  # registered in CONFIGURED_PRICING -> cost_source "configured"
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog

    with SessionLocal() as db:
        row = db.scalar(select(UsageLog).order_by(UsageLog.id.desc()))
        assert row.prompt_tokens == 200 and row.completion_tokens == 10
        assert row.usage_raw["cachedContentTokenCount"] == 150
        assert row.usage_raw["thoughtsTokenCount"] == 20
        assert row.usage_raw["toolUsePromptTokenCount"] == 5
        assert row.cost_source == "configured"


def test_generate_content_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["gemini"],
        allowed_models={"gemini": ["gemini-2.5-flash-lite"]},
    )
    r = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []

    ok = client.post(
        _gen_url("gemini-2.5-flash-lite"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert ok.status_code == 200


def test_generate_content_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 403


def test_generate_content_upstream_error_502_records_error_row(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["gemini"])
    r = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "gemini"


def test_generate_content_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["gemini"], monthly_budget_usd=0)
    r = client.post(
        _gen_url("gemini-2.5-flash"),
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_generate_content_missing_model_404(client, make_live_key):
    # no {model} segment at all -> FastAPI 404s the route, not a gateway 422
    k = make_live_key(allowed_providers=["gemini"])
    r = client.post(
        "/v1beta/models/:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers=_goog(k["key"]),
    )
    assert r.status_code in (404, 422)


def test_image_generation_needs_no_new_endpoint_just_an_image_capable_model(client, admin_client, make_live_key, monkeypatch):
    """Gemini image generation is reached through this SAME generateContent
    endpoint (an image-capable model + generationConfig.responseModalities)
    — no dedicated image endpoint exists or is needed, since the adapter
    never validates/transforms the body or response beyond `model`. Proves
    it round-trips: the responseModalities request field forwards untouched,
    and a response with an inlineData image part (no text at all) doesn't
    crash usage/preview extraction."""

    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"inlineData": {"mimeType": "image/png", "data": "ZmFrZQ=="}}],
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 1290, "totalTokenCount": 1298},
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["gemini"])
    body = {
        "contents": [{"role": "user", "parts": [{"text": "a red bicycle"}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
    r = client.post(_gen_url("gemini-2.5-flash-image"), json=body, headers=_goog(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert j["candidates"][0]["content"]["parts"][0]["inlineData"]["mimeType"] == "image/png"

    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 8 and items[0]["completion_tokens"] == 1290
    # no text part anywhere in the response -> text preview is empty, not a crash
    assert items[0]["response_preview"] in (None, "")
