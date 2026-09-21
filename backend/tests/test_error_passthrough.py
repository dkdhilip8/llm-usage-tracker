"""H1: a genuine HTTP error response FROM a provider (4xx/5xx) must be
distinguished from a transport failure (DNS, timeout, connection refused) —
the former preserves the provider's real status code (and, on native
passthrough endpoints, its real body too); the latter still 502s, since the
gateway genuinely never reached the provider in that case.

Every existing "upstream_error_502_records_error_row" test in the adapter
test files already proves the transport-failure side (they raise a plain
RuntimeError, which is exactly what should still 502). These tests prove the
new side: a real httpx.HTTPStatusError."""

import httpx


def _upstream_error(status_code: int, body: bytes, content_type: str = "application/json") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(status_code, content=body, headers={"content-type": content_type}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ---- native endpoints: real status + real body, byte-for-byte ----


def test_messages_real_upstream_4xx_passes_through_status_and_body(client, admin_client, make_live_key, monkeypatch):
    provider_body = b'{"type":"error","error":{"type":"invalid_request_error","message":"bad tool schema"}}'

    def boom(*a, **kw):
        raise _upstream_error(400, provider_body)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/messages",
        json={"model": "claude-3-5-haiku-20241022", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": k["key"]},
    )
    assert r.status_code == 400  # the provider's real status, not a blanket 502
    assert r.content == provider_body  # byte-identical to what the provider sent
    assert r.headers["content-type"].startswith("application/json")
    # still recorded as a failed usage row, same as any other upstream error
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "anthropic"


def test_gemini_real_upstream_429_passes_through(client, make_live_key, monkeypatch):
    provider_body = b'{"error":{"code":429,"message":"rate limited","status":"RESOURCE_EXHAUSTED"}}'

    def boom(*a, **kw):
        raise _upstream_error(429, provider_body)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["gemini"])
    r = client.post(
        "/v1beta/models/gemini-2.5-flash:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        headers={"x-goog-api-key": k["key"]},
    )
    assert r.status_code == 429
    assert r.content == provider_body


def test_embeddings_real_upstream_401_passes_through(client, make_live_key, monkeypatch):
    provider_body = b'{"error":{"message":"Incorrect API key provided","type":"invalid_request_error"}}'

    def boom(*a, **kw):
        raise _upstream_error(401, provider_body)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 401
    assert r.content == provider_body


def test_native_openai_chat_completions_real_upstream_error_passes_through(client, make_live_key, monkeypatch):
    provider_body = b'{"error":{"message":"context_length_exceeded","type":"invalid_request_error"}}'

    def boom(*a, **kw):
        raise _upstream_error(400, provider_body)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"], default_provider="openai")
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 400
    assert r.content == provider_body


# ---- legacy endpoints: real status code, existing gateway-shaped envelope kept ----


def test_proxy_chat_real_upstream_status_code_not_blanket_502(client, make_live_key, monkeypatch):
    def boom(provider, model, prompt, api_key=None):
        raise _upstream_error(429, b'{"error":"rate limited"}')

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key()
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "prompt": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 429  # not 502
    detail = r.json()["detail"]
    assert detail["type"] == "upstream_error" and detail["code"] == "429"


def test_legacy_chat_completions_translation_real_upstream_status_code(client, make_live_key, monkeypatch):
    def boom(provider, model, prompt, api_key=None):
        raise _upstream_error(400, b'{"type":"error","error":{"message":"bad request"}}')

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key(allowed_providers=["anthropic"], default_provider="anthropic")
    r = client.post(
        "/v1/chat/completions",
        json={"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "400"


def test_transport_failure_still_502s_not_confused_with_upstream_4xx(client, make_live_key, monkeypatch):
    """A real network failure (no response at all) must still 502 — proves the
    two exception branches are distinct, not that everything now passes 200."""

    def boom(provider, model, prompt, api_key=None):
        raise ConnectionError("connection reset")

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key()
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct", "prompt": "hi"},
        headers={"Authorization": f"Bearer {k['key']}"},
    )
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "502"


# ---- streaming: HTTP status is unavoidably already 200, but the in-band
# error event carries the real upstream status when one exists ----


def test_native_streaming_error_event_carries_real_upstream_status(client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise _upstream_error(503, b"service unavailable")

    monkeypatch.setattr("app.providers.stream_request", boom)
    k = make_live_key(allowed_providers=["anthropic"])
    with client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "claude-3-5-haiku-20241022", "max_tokens": 50, "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-api-key": k["key"]},
    ) as r:
        assert r.status_code == 200  # already committed by the time the error surfaces
        text = "".join(r.iter_text())
    assert "event: error" in text
    assert '"upstream_status": 503' in text


def test_legacy_streaming_error_message_carries_real_upstream_status(client, make_live_key, monkeypatch):
    def boom(provider, model, prompt, api_key=None):
        raise _upstream_error(500, b"internal error")

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key(allowed_providers=["anthropic"], default_provider="anthropic")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "claude-3-5-haiku", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        headers={"Authorization": f"Bearer {k['key']}"},
    ) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "HTTP 500" in text
