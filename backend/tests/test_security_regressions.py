"""M7: a real per-provider API key (resolved server-side via
require_live_ready/live_key_for) must never appear anywhere in a client-facing
response — body or headers — across every native passthrough endpoint. This
has only ever been verified manually (grep) once per phase; this is the
permanent, automated guard. Add new native endpoints (image/audio APIs) to
the `checks`/`stream_checks` lists below as they ship."""

import httpx


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _no_leak(response, secrets: list[str], where: str) -> None:
    text = response.text
    for secret in secrets:
        assert secret not in text, f"{where}: response body leaked a provider credential"
    for header_value in response.headers.values():
        for secret in secrets:
            assert secret not in header_value, f"{where}: response header leaked a provider credential"


def test_native_endpoints_never_leak_the_provider_credential(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["openai", "anthropic", "gemini"], default_provider="openai"
    )
    # exactly the fake provider keys make_live_key attaches (conftest.py) — the
    # real material every one of these calls resolves server-side and sends
    # upstream via require_live_ready/live_key_for.
    secrets = [
        "sk-fake-openai-key-000000",
        "sk-fake-anthropic-key-000000",
        "sk-fake-gemini-key-000000",
        "sk-fake-openrouter-key-000000",
    ]

    checks = [
        (
            "/v1/messages",
            {"model": "claude-3-5-haiku-20241022", "max_tokens": 50, "messages": [{"role": "user", "content": "hi"}]},
            {"x-api-key": k["key"]},
        ),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hi"}, _bearer(k["key"])),
        ("/v1/embeddings", {"model": "text-embedding-3-small", "input": "hi"}, _bearer(k["key"])),
        (
            "/v1beta/models/gemini-2.5-flash:generateContent",
            {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            {"x-goog-api-key": k["key"]},
        ),
        (
            "/v1beta/models/gemini-embedding-001:embedContent",
            {"content": {"parts": [{"text": "hi"}]}},
            {"x-goog-api-key": k["key"]},
        ),
        (
            "/v1/chat/completions",
            {"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
            _bearer(k["key"]),
        ),
    ]
    for path, body, headers in checks:
        r = client.post(path, json=body, headers=headers)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
        _no_leak(r, secrets, path)

    stream_checks = [
        (
            "/v1/messages",
            {
                "model": "claude-3-5-haiku-20241022", "max_tokens": 50, "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
            {"x-api-key": k["key"]},
        ),
        ("/v1/responses", {"model": "gpt-4o-mini", "input": "hi", "stream": True}, _bearer(k["key"])),
        (
            "/v1beta/models/gemini-2.5-flash:streamGenerateContent",
            {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
            {"x-goog-api-key": k["key"]},
        ),
    ]
    for path, body, headers in stream_checks:
        with client.stream("POST", path, json=body, headers=headers) as r:
            assert r.status_code == 200, f"{path}: {r.status_code}"
            r.read()  # buffer the body so .text/.headers checks below can run
            _no_leak(r, secrets, path)


def test_upstream_error_passthrough_never_leaks_the_provider_credential(client, make_live_key, monkeypatch):
    """The H1 byte-passthrough error path is a new place a leak could sneak in
    (it relays a raw provider body) — prove it doesn't relay our own headers."""

    def boom(*a, **kw):
        request = httpx.Request("POST", "https://example.test/x")
        response = httpx.Response(
            401, content=b'{"error":"invalid api key"}', headers={"content-type": "application/json"}, request=request
        )
        raise httpx.HTTPStatusError("HTTP 401", request=request, response=response)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 401
    _no_leak(r, ["sk-fake-openai-key-000000"], "/v1/embeddings (error path)")
