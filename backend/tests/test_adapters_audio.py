"""OpenAI Audio passthrough: POST /v1/audio/transcriptions (multipart) and
POST /v1/audio/speech (JSON in, raw audio out). Contract-tested against
captured real response shapes (app.providers.send_multipart /
send_request_binary mocked in conftest.mock_provider) — no live network
calls.

The core architectural question this file answers, per operation:
  transcription (whisper-1)          -> duration_seconds -> configured cost
  transcription (gpt-4o-transcribe)  -> prompt/completion tokens -> unknown cost (unregistered)
  speech (tts-1)                     -> characters -> configured cost
  speech (an unregistered TTS model) -> characters -> unknown cost
Never tokens for either, by default. See test_*_billing_dimension_* below.
"""

import io


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _audio_file():
    return {"file": ("clip.mp3", io.BytesIO(b"FAKE_MP3_CONTENT"), "audio/mpeg")}


# ---- transcription ----


def test_transcription_forwards_form_fields_and_file(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1", "language": "en", "prompt": "hello", "response_format": "json"},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "reply to: mock transcription"

    url, headers, data, files = mock_provider[0]
    assert url.endswith("/audio/transcriptions")
    assert data["model"] == "whisper-1"
    assert data["language"] == "en"
    assert data["prompt"] == "hello"
    assert "content-type" not in {h.lower() for h in headers}  # httpx sets the multipart boundary itself
    assert files["file"][0] == "clip.mp3"
    assert files["file"][1] == b"FAKE_MP3_CONTENT"


def test_transcription_whisper1_bills_by_duration_not_tokens(client, admin_client, make_live_key, mock_provider):
    """THE billing-unit proof for transcription's duration-billed models."""
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["prompt_tokens"] == 0 and row["completion_tokens"] == 0  # NOT tokens
    assert row["duration_seconds"] == 12.5  # the actual billing unit
    assert row["characters"] is None
    # whisper-1's $0.006/min rate is registered -> a real, non-fabricated cost
    assert row["cost_source"] == "configured"
    assert row["cost"] == round(12.5 / 60 * 0.006, 6)


def test_transcription_token_billed_model_unregistered_cost_unknown(client, admin_client, make_live_key, mock_provider):
    """gpt-4o-transcribe bills by token (verified against OpenAI's own API
    reference) — captured as real prompt/completion tokens, not duration.
    Not registered in the token price table -> honest "unknown", not guessed."""
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "gpt-4o-transcribe"},
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["prompt_tokens"] == 20 and row["completion_tokens"] == 8
    assert row["duration_seconds"] is None and row["characters"] is None
    assert row["cost_source"] == "unknown" and row["cost"] is None


def test_transcription_workspace_token_override_does_not_misapply_to_duration_billing(
    client, admin_client, make_live_key, mock_provider
):
    """Regression guard: model_pricing is a token-rate registry with no
    duration equivalent. A workspace override registered for (openai,
    whisper-1) must NOT get applied to a duration-billed row — that would
    silently compute cost from the always-0 prompt/completion tokens (a
    wrong near-zero number), which is worse than the honest built-in
    duration rate this row should keep using instead."""
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "whisper-1", "input_per_1m": 999.0, "output_per_1m": 999.0},
    )
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["cost_source"] == "configured"  # not "workspace"
    assert row["cost"] == round(12.5 / 60 * 0.006, 6)  # the real duration rate, not the token override


def test_transcription_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"], allowed_models={"openai": ["gpt-4o-transcribe"]})
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []


def test_transcription_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 403


def test_transcription_missing_model_422(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post("/v1/audio/transcriptions", headers=_bearer(k["key"]), files=_audio_file(), data={})
    assert r.status_code == 422


def test_transcription_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], monthly_budget_usd=0)
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_transcription_real_upstream_error_passes_through_status_and_body(client, admin_client, make_live_key, monkeypatch):
    """H1 integration: the multipart endpoint inherits real provider-status
    passthrough via run_native_completion_raw, same as every other native
    endpoint — no special-casing needed."""
    import httpx

    provider_body = b'{"error":{"message":"file too large","type":"invalid_request_error"}}'

    def boom(*a, **kw):
        request = httpx.Request("POST", "https://example.test/x")
        response = httpx.Response(
            413, content=provider_body, headers={"content-type": "application/json"}, request=request
        )
        raise httpx.HTTPStatusError("HTTP 413", request=request, response=response)

    monkeypatch.setattr("app.providers.send_multipart", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(k["key"]),
        files=_audio_file(),
        data={"model": "whisper-1"},
    )
    assert r.status_code == 413
    assert r.content == provider_body
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "openai"


# ---- speech (TTS) ----


def test_speech_forwards_body_and_returns_raw_audio_bytes(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "tts-1", "input": "hello world", "voice": "alloy", "response_format": "mp3"}
    r = client.post("/v1/audio/speech", json=body, headers=_bearer(k["key"]))
    assert r.status_code == 200
    assert r.content == b"FAKE_AUDIO_BYTES"
    assert r.headers["content-type"] == "audio/mpeg"

    sent = mock_provider[0][2]
    assert sent["input"] == "hello world"
    assert sent["voice"] == "alloy"
    assert sent["response_format"] == "mp3"


def test_speech_bills_by_characters_not_tokens(client, admin_client, make_live_key, mock_provider):
    """THE billing-unit proof for TTS: no usage object exists anywhere in the
    response (it's pure audio bytes) — the unit is counted from the request."""
    k = make_live_key(allowed_providers=["openai"])
    text = "hello world"  # 11 characters
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": text, "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["prompt_tokens"] == 0 and row["completion_tokens"] == 0  # NOT tokens
    assert row["characters"] == len(text)
    assert row["duration_seconds"] is None
    # tts-1's $15/1M-char rate is registered -> a real, non-fabricated cost
    assert row["cost_source"] == "configured"
    assert row["cost"] == round(len(text) / 1_000_000 * 15.0, 6)


def test_speech_unregistered_model_characters_captured_cost_unknown(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1-hd", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["characters"] == 2
    assert row["cost_source"] == "unknown" and row["cost"] is None


def test_speech_workspace_token_override_does_not_misapply_to_character_billing(
    client, admin_client, make_live_key, mock_provider
):
    admin_client.put(
        "/api/workspace/pricing",
        json={"provider": "openai", "model": "tts-1", "input_per_1m": 999.0, "output_per_1m": 999.0},
    )
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hello world", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    row = items[0]
    assert row["cost_source"] == "configured"  # not "workspace"
    assert row["cost"] == round(11 / 1_000_000 * 15.0, 6)


def test_speech_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"], allowed_models={"openai": ["tts-1-hd"]})
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []


def test_speech_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403


def test_speech_missing_model_422(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post("/v1/audio/speech", json={"input": "hi", "voice": "alloy"}, headers=_bearer(k["key"]))
    assert r.status_code == 422


def test_speech_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], monthly_budget_usd=0)
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_speech_real_upstream_error_passes_through_status_and_body(client, admin_client, make_live_key, monkeypatch):
    import httpx

    provider_body = b'{"error":{"message":"input too long","type":"invalid_request_error"}}'

    def boom(*a, **kw):
        request = httpx.Request("POST", "https://example.test/x")
        response = httpx.Response(
            400, content=provider_body, headers={"content-type": "application/json"}, request=request
        )
        raise httpx.HTTPStatusError("HTTP 400", request=request, response=response)

    monkeypatch.setattr("app.providers.send_request_binary", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 400
    assert r.content == provider_body
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "openai"


def test_speech_transport_failure_still_502s(client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise ConnectionError("connection reset")

    monkeypatch.setattr("app.providers.send_request_binary", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts-1", "input": "hi", "voice": "alloy"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "502"


def test_speech_requires_a_virtual_key(client):
    r = client.post("/v1/audio/speech", json={"model": "tts-1", "input": "hi", "voice": "alloy"})
    assert r.status_code == 401


def test_transcription_requires_a_virtual_key(client):
    r = client.post("/v1/audio/transcriptions", files=_audio_file(), data={"model": "whisper-1"})
    assert r.status_code == 401
