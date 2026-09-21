"""OpenAI-native adapter: Chat Completions (via /v1/chat/completions, full
fidelity for openai/openrouter) and the Responses API (/v1/responses).
Contract-tested against captured real response shapes (app.providers.send_request
/ stream_request mocked in conftest.mock_provider) — no live network calls."""


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_chat_completions_forwards_tools_and_multiturn_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "what's the weather in Denver?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{\"city\":\"Denver\"}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "68F, sunny"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    r = client.post("/v1/chat/completions", json=body, headers=_bearer(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "chat.completion"

    # transparent forwarding: the outbound body has the client's fields intact
    sent = mock_provider[0][2]
    assert sent["model"] == "gpt-4o-mini"  # "openai/" prefix stripped before forwarding
    assert sent["messages"] == body["messages"]
    assert sent["tools"] == body["tools"]
    assert sent["tool_choice"] == "auto"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["temperature"] == 0.2
    assert "stream" not in sent  # non-streaming call never sends stream:true upstream


def test_chat_completions_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["openai"],
        default_provider="openai",
        allowed_models={"openai": ["gpt-4o-mini"]},
    )
    r = client.post(
        "/v1/chat/completions",
        json={"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []  # never reached the provider


def test_chat_completions_multiple_models_forwarded_unmodified(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    for model in ("gpt-4o-mini", "gpt-4o", "o4-mini", "gpt-5-not-yet-in-any-price-table"):
        r = client.post(
            "/v1/chat/completions",
            json={"model": f"openai/{model}", "messages": [{"role": "user", "content": "hi"}]},
            headers=_bearer(k["key"]),
        )
        assert r.status_code == 200, (model, r.text)
    forwarded = [c[2]["model"] for c in mock_provider]
    assert forwarded == ["gpt-4o-mini", "gpt-4o", "o4-mini", "gpt-5-not-yet-in-any-price-table"]


def test_chat_completions_openrouter_reports_real_cost(client, admin_client, make_live_key, monkeypatch):
    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "id": "x", "object": "chat.completion", "created": 1,
            "model": json_body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "cost": 0.0033},
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["openrouter"], default_provider="openrouter")
    r = client.post(
        "/v1/chat/completions",
        json={"model": "openrouter/meta-llama/llama-3.3-70b-instruct", "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "provider" and abs(items[0]["cost"] - 0.0033) < 1e-9


def test_chat_completions_unregistered_model_is_honest_not_fabricated(client, admin_client, make_live_key, monkeypatch):
    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "id": "x", "object": "chat.completion", "created": 1,
            "model": json_body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    r = client.post(
        "/v1/chat/completions",
        json={"model": "openai/some-brand-new-model-not-in-the-table", "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "unknown" and items[0]["cost"] is None
    # a NULL-cost row must not break budget/usage aggregates (coalesce handles it)
    assert admin_client.get("/api/usage/summary").json()["total_requests"] == 1


def test_chat_completions_streaming_forwards_tools(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    body = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}],
        "stream": True,
    }
    with client.stream("POST", "/v1/chat/completions", json=body, headers=_bearer(k["key"])) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: [DONE]" in text
    assert '"object": "chat.completion.chunk"' in text
    sent = mock_provider[0][2]
    assert sent["tools"] == body["tools"]
    assert sent["stream"] is True


def test_responses_api_basic(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/responses",
        json={"model": "gpt-4o-mini", "input": "hi", "tools": [{"type": "web_search"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "response" and j["status"] == "completed"
    sent = mock_provider[0][2]
    assert sent["input"] == "hi"
    assert sent["tools"] == [{"type": "web_search"}]
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 5 and items[0]["completion_tokens"] == 7


def test_responses_api_model_not_allowed_403(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], allowed_models={"openai": ["gpt-4o-mini"]})
    r = client.post(
        "/v1/responses",
        json={"model": "gpt-4o", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"


def test_responses_api_wrong_provider_403(client, make_live_key):
    # a key only allowed on anthropic can't reach the OpenAI-only /v1/responses
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post("/v1/responses", json={"model": "gpt-4o-mini", "input": "hi"}, headers=_bearer(k["key"]))
    assert r.status_code == 403


def test_responses_api_streaming(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    with client.stream(
        "POST", "/v1/responses",
        json={"model": "gpt-4o-mini", "input": "hi", "stream": True},
        headers=_bearer(k["key"]),
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: [DONE]" in text
    assert "response.completed" in text


def test_responses_api_upstream_error_502(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("timeout")

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post("/v1/responses", json={"model": "gpt-4o-mini", "input": "hi"}, headers=_bearer(k["key"]))
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error"


def test_no_provider_key_402_on_native_endpoints(client, admin_client):
    admin_client.put("/api/workspace/providers/openai/key", json={"api_key": "sk-fake-000000"})
    r = admin_client.post(
        "/api/keys", json={"label": "k", "allowed_providers": ["openai"], "allow_live": True}
    )
    key = r.json()["key"]
    admin_client.delete("/api/workspace/providers/openai/key")
    resp = client.post(
        "/v1/responses", json={"model": "gpt-4o-mini", "input": "hi"}, headers=_bearer(key)
    )
    assert resp.status_code == 402 and resp.json()["detail"]["type"] == "provider_not_configured"


def test_paused_key_403_on_native_endpoints(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], allow_live=False)
    r = client.post("/v1/responses", json={"model": "gpt-4o-mini", "input": "hi"}, headers=_bearer(k["key"]))
    assert r.status_code == 403 and r.json()["detail"]["type"] == "key_paused"


def test_missing_model_422(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    assert client.post("/v1/responses", json={"input": "hi"}, headers=_bearer(k["key"])).status_code == 422
    assert client.post("/v1/messages", json={"messages": []}, headers=_bearer(k["key"])).status_code == 422


def test_cached_and_reasoning_tokens_captured_in_usage_raw(client, admin_client, make_live_key, monkeypatch):
    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "id": "x", "object": "chat.completion", "created": 1, "model": json_body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 80},
                "completion_tokens_details": {"reasoning_tokens": 20},
            },
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    client.post(
        "/v1/chat/completions",
        json={"model": "openai/o4-mini", "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 100 and items[0]["completion_tokens"] == 50
    # usage_raw isn't in the /api/requests payload today — verify it landed in the DB directly
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog

    with SessionLocal() as db:
        row = db.scalar(select(UsageLog).order_by(UsageLog.id.desc()))
        assert row.usage_raw["prompt_tokens_details"]["cached_tokens"] == 80
        assert row.usage_raw["completion_tokens_details"]["reasoning_tokens"] == 20
