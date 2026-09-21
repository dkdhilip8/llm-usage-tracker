"""Embeddings passthrough: OpenAI's POST /v1/embeddings and Gemini's
:embedContent / :batchEmbedContents. Contract-tested against captured real
response shapes (app.providers.send_request mocked in conftest.mock_provider)
— no live network calls. No streaming — none of these APIs support it."""


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _goog(key: str) -> dict:
    return {"x-goog-api-key": key}


# ---- OpenAI /v1/embeddings ----


def test_openai_embeddings_forwards_body_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "text-embedding-3-small", "input": ["hello", "world"], "encoding_format": "float"}
    r = client.post("/v1/embeddings", json=body, headers=_bearer(k["key"]))
    assert r.status_code == 200
    j = r.json()
    assert j["object"] == "list" and len(j["data"]) == 2

    sent = mock_provider[0][2]
    assert sent["input"] == ["hello", "world"]
    assert sent["encoding_format"] == "float"
    assert mock_provider[0][0].endswith("/v1/embeddings")


def test_openai_embeddings_registered_model_gets_configured_cost(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hello"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["cost_source"] == "configured"
    assert items[0]["prompt_tokens"] == 5 and items[0]["completion_tokens"] == 0


def test_openai_embeddings_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["openai"],
        allowed_models={"openai": ["text-embedding-3-large"]},
    )
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []

    ok = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-large", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert ok.status_code == 200


def test_openai_embeddings_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["anthropic"])
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403


def test_openai_embeddings_upstream_error_502_records_error_row(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "openai"


def test_openai_embeddings_budget_exceeded_402(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"], monthly_budget_usd=0)
    r = client.post(
        "/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_openai_embeddings_missing_model_422(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post("/v1/embeddings", json={"input": "hi"}, headers=_bearer(k["key"]))
    assert r.status_code == 422


# ---- Gemini :embedContent / :batchEmbedContents ----


def test_embed_content_forwards_body_verbatim(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["gemini"])
    body = {"content": {"parts": [{"text": "hello"}]}, "taskType": "RETRIEVAL_DOCUMENT"}
    r = client.post(
        "/v1beta/models/gemini-embedding-001:embedContent", json=body, headers=_goog(k["key"])
    )
    assert r.status_code == 200
    assert "values" in r.json()["embedding"]

    sent = mock_provider[0][2]
    assert sent["content"] == body["content"]
    assert sent["taskType"] == "RETRIEVAL_DOCUMENT"
    assert mock_provider[0][0].endswith("gemini-embedding-001:embedContent")


def test_batch_embed_contents_forwards_multiple_requests(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["gemini"])
    body = {
        "requests": [
            {"content": {"parts": [{"text": "hello"}]}},
            {"content": {"parts": [{"text": "world"}]}},
        ]
    }
    r = client.post(
        "/v1beta/models/gemini-embedding-001:batchEmbedContents", json=body, headers=_goog(k["key"])
    )
    assert r.status_code == 200
    assert len(r.json()["embeddings"]) == 2

    sent = mock_provider[0][2]
    assert len(sent["requests"]) == 2
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["prompt_tokens"] == 10  # 5 * 2 requests, from the fake usageMetadata


def test_embed_content_model_not_allowed_403(client, make_live_key, mock_provider):
    k = make_live_key(
        allowed_providers=["gemini"],
        allowed_models={"gemini": ["gemini-embedding-001"]},
    )
    r = client.post(
        "/v1beta/models/other-embed-model:embedContent",
        json={"content": {"parts": [{"text": "hi"}]}},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 403 and r.json()["detail"]["type"] == "model_not_allowed"
    assert mock_provider == []


def test_embed_content_wrong_provider_403(client, make_live_key):
    k = make_live_key(allowed_providers=["openai"])
    r = client.post(
        "/v1beta/models/gemini-embedding-001:embedContent",
        json={"content": {"parts": [{"text": "hi"}]}},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 403


def test_embed_content_upstream_error_502_records_error_row(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["gemini"])
    r = client.post(
        "/v1beta/models/gemini-embedding-001:embedContent",
        json={"content": {"parts": [{"text": "hi"}]}},
        headers=_goog(k["key"]),
    )
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items[0]["status"] == "error" and items[0]["provider"] == "gemini"
