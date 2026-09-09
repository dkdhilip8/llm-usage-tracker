OR_MODEL = "meta-llama/llama-3.3-70b-instruct"


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _chat(client, key: str, provider: str = "openrouter", model: str = OR_MODEL):
    return client.post(
        "/v1/proxy/chat",
        json={"provider": provider, "model": model, "prompt": "hi"},
        headers=_bearer(key),
    )


def test_healthz(client):
    j = client.get("/healthz").json()
    assert j["status"] == "ok" and "version" in j


def test_read_endpoints_require_sign_in(client):
    assert client.get("/api/keys").status_code == 401
    assert (
        client.post(
            "/api/keys", json={"label": "x", "allowed_providers": ["openai"]}
        ).status_code
        == 401
    )
    assert client.get("/api/usage/summary").status_code == 401
    assert client.get("/api/requests").status_code == 401


def test_signed_in_without_workspace_is_403(signup):
    c, _ = signup()
    assert c.get("/api/usage/summary").status_code == 403
    assert c.get("/api/keys").status_code == 403
    assert c.get("/api/requests").status_code == 403


def test_key_create_list_hides_secret(admin_client, make_key):
    k = make_key(label="alice", allowed_providers=["openrouter", "openai"])
    assert k["key"].startswith("vk_")
    rows = admin_client.get("/api/keys").json()
    assert rows and "key_hash" not in rows[0] and "key" not in rows[0]
    assert rows[0]["spend_period"] == 0
    assert rows[0]["budget_period"] == "month"


def test_proxy_requires_bearer(client):
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hi"},
    )
    assert r.status_code == 401


def test_provider_acl_403(client, make_key):
    k = make_key(allowed_providers=["openrouter"])
    r = _chat(client, k["key"], provider="openai", model="gpt-4o")
    assert r.status_code == 403
    assert "not permitted" in str(r.json()["detail"])


def test_paused_key_403(client, make_live_key):
    k = make_live_key(allow_live=False)
    r = _chat(client, k["key"])
    assert r.status_code == 403 and r.json()["detail"]["type"] == "key_paused"


def test_no_provider_key_402(client, admin_client, make_key):
    # openrouter is attached; openai is not — an openai call on a live key -> 402
    admin_client.put(
        "/api/workspace/providers/openrouter/key", json={"api_key": "sk-fake-or-000000"}
    )
    k = make_key(allowed_providers=["openrouter", "openai"], allow_live=True)
    assert k["allow_live"] is True
    r = _chat(client, k["key"], provider="openai", model="gpt-4o")
    assert r.status_code == 402
    assert r.json()["detail"]["type"] == "provider_not_configured"


def test_live_completion_and_cost_source(client, make_live_key, mock_provider):
    k = make_live_key()
    j = _chat(client, k["key"]).json()
    assert j["cost_source"] == "configured" and j["cost"] >= 0
    assert j["response"].startswith("reply to:")
    u = j["usage"]
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]
    assert mock_provider and mock_provider[0][0] == "openrouter"


def test_openrouter_reports_provider_cost(client, make_live_key, monkeypatch):
    monkeypatch.setattr(
        "app.providers.call_provider",
        lambda p, m, pr, api_key=None: ("ok", 3, 4, 0.0025),
    )
    k = make_live_key()
    j = _chat(client, k["key"]).json()
    assert j["cost_source"] == "provider" and j["cost"] == 0.0025


def test_upstream_error_502_records_error_row(client, admin_client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key()
    r = _chat(client, k["key"])
    assert r.status_code == 502 and r.json()["detail"]["type"] == "upstream_error"
    items = admin_client.get("/api/requests").json()["items"]
    assert items and items[0]["status"] == "error"


def test_budget_402(client, make_key):
    k = make_key(monthly_budget_usd=0)
    r = _chat(client, k["key"])
    assert r.status_code == 402 and r.json()["detail"]["type"] == "budget_exceeded"


def test_budget_patch_clears(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(monthly_budget_usd=0)
    assert _chat(client, k["key"]).status_code == 402
    admin_client.patch(f"/api/keys/{k['id']}", json={"clear_budget": True})
    assert _chat(client, k["key"]).status_code == 200


def test_openai_compat_shape(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openrouter"], default_provider="openrouter")
    j = client.post(
        "/v1/chat/completions",
        json={
            "model": f"openrouter/{OR_MODEL}",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=_bearer(k["key"]),
    ).json()
    assert j["object"] == "chat.completion"
    assert j["choices"][0]["message"]["role"] == "assistant"
    assert set(j["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}
    assert "cost" in j["x_gateway"] and "mode" not in j["x_gateway"]


def test_openai_compat_stream_terminates(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openrouter"], default_provider="openrouter")
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": f"openrouter/{OR_MODEL}",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers=_bearer(k["key"]),
    ) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "data: [DONE]" in body
    assert '"object": "chat.completion.chunk"' in body


def test_requests_log_and_metrics(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openrouter"])
    _chat(client, k["key"])
    items = admin_client.get("/api/requests?limit=10").json()["items"]
    assert items and items[0]["provider"] == "openrouter"
    assert items[0]["status"] == "success"

    j = admin_client.get("/api/usage/summary").json()
    for key in ("latency_p50_ms", "latency_p95_ms", "error_rate", "tokens_per_sec", "total_cost"):
        assert key in j
    assert j["total_requests"] == 1


def test_revoked_key_rejected(client, admin_client, make_key):
    k = make_key(allowed_providers=["openrouter"])
    admin_client.delete(f"/api/keys/{k['id']}")
    assert _chat(client, k["key"]).status_code == 401


def test_budget_period_stored_and_used(client, admin_client, make_key):
    k = make_key(allowed_providers=["openrouter"], monthly_budget_usd=0, budget_period="day")
    r = _chat(client, k["key"])
    assert r.status_code == 402
    assert "per day" in r.json()["detail"]["message"]
    assert admin_client.get("/api/keys").json()[0]["budget_period"] == "day"


def test_custom_budget_window(client, admin_client, make_live_key, mock_provider):
    import datetime as _dt

    today = _dt.date.today()
    k = make_live_key(
        allowed_providers=["openrouter"],
        monthly_budget_usd=0,
        budget_period="custom",
        budget_start=str(today - _dt.timedelta(days=1)),
        budget_end=str(today + _dt.timedelta(days=1)),
    )
    r = _chat(client, k["key"])
    assert r.status_code == 402 and "for " in r.json()["detail"]["message"]

    k2 = make_live_key(
        allowed_providers=["openrouter"],
        monthly_budget_usd=0,
        budget_period="custom",
        budget_start=str(today - _dt.timedelta(days=10)),
        budget_end=str(today - _dt.timedelta(days=5)),
    )
    assert _chat(client, k2["key"]).status_code == 200


def test_custom_budget_requires_dates(admin_client):
    r = admin_client.post(
        "/api/keys",
        json={
            "label": "bad",
            "allowed_providers": ["openrouter"],
            "budget_period": "custom",
        },
    )
    assert r.status_code == 422
