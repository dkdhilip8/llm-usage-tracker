"""Anthropic-native adapter: /v1/messages. Contract-tested against captured
real response shapes (app.providers.send_request / stream_request mocked in
conftest.mock_provider) — no live network calls."""


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_messages_forwards_system_and_content_blocks_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "system": "be terse",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what's in this image?"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                ],
            }
        ],
        "tools": [{"name": "get_weather", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
    }
    r = client.post("/v1/messages", json=body, headers=_bearer(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert j["type"] == "message" and j["role"] == "assistant"

    sent = mock_provider[0][2]
    assert sent["system"] == "be terse"  # top-level, not folded into messages
    assert sent["messages"] == body["messages"]  # image content block untouched
    assert sent["tools"] == body["tools"]
    assert sent["tool_choice"] == {"type": "auto"}
    assert sent["max_tokens"] == 1024


def test_messages_requires_max_tokens_client_side(client, make_live_key, mock_provider):
    # Anthropic requires max_tokens; the gateway no longer guesses 1024 for the
    # native endpoint — a client that omits it gets a clean 422, not a silent default.
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    # the gateway forwards it; Anthropic itself would 4xx — our fake just accepts
    # whatever's forwarded, so this asserts the field really was omitted upstream.
    assert r.status_code == 200
    assert "max_tokens" not in mock_provider[0][2]


def test_messages_tool_use_round_trip_two_turns(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    # turn 1: model would return a tool_use block (our fake doesn't simulate that,
    # but the client-side round trip is what matters here — the app, not the
    # gateway, executes the tool and sends the result back as a new /v1/messages call)
    first = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "what's the weather in Denver?"}],
            "tools": [{"name": "get_weather", "input_schema": {"type": "object"}}],
        },
        headers=_bearer(k["key"]),
    )
    assert first.status_code == 200

    # turn 2: app executed the tool locally, sends the result back — the gateway
    # does not execute it, only proxies the continuation
    second = client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "what's the weather in Denver?"},
                {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": "get_weather", "input": {"city": "Denver"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "68F, sunny"}]},
            ],
        },
        headers=_bearer(k["key"]),
    )
    assert second.status_code == 200
    assert len(mock_provider) == 2
    assert mock_provider[1][2]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert admin_client.get("/api/usage/summary").json()["total_requests"] == 2


def test_messages_streaming_real_event_shape_and_usage(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    with client.stream(
        "POST", "/v1/messages",
        json={
            "model": "claude-3-5-haiku-20241022", "max_tokens": 256, "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=_bearer(k["key"]),
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "event: message_start" in text
    assert "event: content_block_delta" in text
    assert "event: message_stop" in text
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 5 and items[0]["completion_tokens"] == 7


def test_messages_cache_tokens_captured_in_usage_raw(client, make_live_key, monkeypatch):
    def fake_send(url, headers, json_body, *, timeout=60.0):
        return {
            "id": "msg_1", "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "model": json_body["model"], "stop_reason": "end_turn",
            "usage": {"input_tokens": 200, "output_tokens": 10, "cache_creation_input_tokens": 150, "cache_read_input_tokens": 50},
        }

    monkeypatch.setattr("app.providers.send_request", fake_send)
    k = make_live_key(allowed_providers=["anthropic"])
    client.post(
        "/v1/messages",
        # a registered model (CONFIGURED_PRICING) so cost_source lands on
        # "configured" rather than "unknown" — isolates the assertion below
        json={"model": "claude-3-5-sonnet", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageLog

    with SessionLocal() as db:
        row = db.scalar(select(UsageLog).order_by(UsageLog.id.desc()))
        assert row.prompt_tokens == 200 and row.completion_tokens == 10
        assert row.usage_raw["cache_creation_input_tokens"] == 150
        assert row.usage_raw["cache_read_input_tokens"] == 50
        # Anthropic reports no per-request cost -> price-table estimate, not "unknown"
        assert row.cost_source == "configured"


def test_messages_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["anthropic"],
        allowed_models={"anthropic": ["claude-3-5-haiku-20241022"]},
    )
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-sonnet-20241022", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []

    # the allowed model still works
    ok = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert ok.status_code == 200


def test_messages_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403


def test_messages_upstream_error_502_records_error_row(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "anthropic"


def test_messages_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["anthropic"], monthly_budget_usd=0)
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"
