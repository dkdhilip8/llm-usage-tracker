OR_MODEL = "meta-llama/llama-3.3-70b-instruct"


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_healthz(client):
    j = client.get("/healthz").json()
    assert j["status"] == "ok" and j["live_enabled"] is False


def test_admin_endpoints_require_token(client):
    assert client.get("/api/keys").status_code == 403
    assert (
        client.post(
            "/api/keys", json={"label": "x", "allowed_providers": ["openai"]}
        ).status_code
        == 403
    )
    assert client.post("/api/demo/reset").status_code == 403
    assert client.post("/api/insights/demo-spike").status_code == 403


def test_public_endpoints_need_no_token(client):
    # the shared demo: dashboard + request log + insights are open to everyone
    assert client.get("/api/usage/summary").status_code == 200
    assert client.get("/api/requests").status_code == 200
    assert client.get("/api/insights/alerts").status_code == 200


def test_key_create_list_hides_secret(client, admin, make_key):
    k = make_key(label="alice", allowed_providers=["openrouter", "openai"])
    assert k["key"].startswith("vk_")
    rows = client.get("/api/keys", headers=admin).json()
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
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openai", "model": "gpt-4o", "prompt": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 403


def test_simulated_completion_and_cost_source(client, make_key):
    k = make_key(allowed_providers=["openrouter"])
    j = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hello"},
        headers=_bearer(k["key"]),
    ).json()
    assert j["mode"] == "simulated" and j["cost_source"] == "configured"
    assert j["cost"] >= 0
    u = j["usage"]
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]


def test_budget_402(client, make_key):
    k = make_key(monthly_budget_usd=0)
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 402
    assert r.json()["detail"]["type"] == "budget_exceeded"


def test_budget_patch_clears(client, admin, make_key):
    k = make_key(monthly_budget_usd=0)
    client.patch(f"/api/keys/{k['id']}", json={"clear_budget": True}, headers=admin)
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 200


def test_openai_compat_shape(client, make_key):
    k = make_key(allowed_providers=["openrouter"], default_provider="openrouter")
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
    assert j["x_gateway"]["mode"] == "simulated"


def test_openai_compat_stream_terminates(client, make_key):
    k = make_key(allowed_providers=["openrouter"], default_provider="openrouter")
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


def test_requests_log_and_metrics(client, admin, make_key):
    k = make_key(allowed_providers=["openrouter"])
    client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "log me"},
        headers=_bearer(k["key"]),
    )
    items = client.get("/api/requests?limit=10", headers=admin).json()["items"]
    assert items and items[0]["provider"] == "openrouter"
    assert items[0]["prompt_preview"] == "log me"  # LOG_BODIES=true in tests

    j = client.get("/api/usage/summary").json()
    for key in (
        "latency_p50_ms",
        "latency_p95_ms",
        "error_rate",
        "tokens_per_sec",
        "total_cost",
    ):
        assert key in j
    assert j["total_requests"] == 1


def test_simulator_deterministic():
    from app.simulator import simulate_chat

    a = simulate_chat("openrouter", "m", "same prompt")
    b = simulate_chat("openrouter", "m", "same prompt")
    assert a == b


def test_revoked_key_rejected(client, admin, make_key):
    k = make_key(allowed_providers=["openrouter"])
    client.delete(f"/api/keys/{k['id']}", headers=admin)
    r = client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": OR_MODEL, "prompt": "hi"},
        headers=_bearer(k["key"]),
    )
    assert r.status_code == 401


def _chat(client, key: str, model: str = OR_MODEL):
    return client.post(
        "/v1/proxy/chat",
        json={"provider": "openrouter", "model": model, "prompt": "hi"},
        headers=_bearer(key),
    )


def test_budget_period_stored_and_used(client, admin, make_key):
    k = make_key(
        allowed_providers=["openrouter"], monthly_budget_usd=0, budget_period="day"
    )
    r = _chat(client, k["key"])
    assert r.status_code == 402
    assert "per day" in r.json()["detail"]["message"]
    rows = client.get("/api/keys", headers=admin).json()
    assert rows[0]["budget_period"] == "day"


def test_custom_budget_window(client, admin, make_key):
    import datetime as _dt

    today = _dt.date.today()
    # window that is active now -> 0 budget blocks
    k = make_key(
        allowed_providers=["openrouter"],
        monthly_budget_usd=0,
        budget_period="custom",
        budget_start=str(today - _dt.timedelta(days=1)),
        budget_end=str(today + _dt.timedelta(days=1)),
    )
    r = _chat(client, k["key"])
    assert r.status_code == 402
    assert "for " in r.json()["detail"]["message"]
    rows = client.get("/api/keys", headers=admin).json()
    assert rows[0]["budget_period"] == "custom"
    assert rows[0]["budget_start"] and rows[0]["budget_end"]

    # window entirely in the past -> cap no longer applies
    k2 = make_key(
        allowed_providers=["openrouter"],
        monthly_budget_usd=0,
        budget_period="custom",
        budget_start=str(today - _dt.timedelta(days=10)),
        budget_end=str(today - _dt.timedelta(days=5)),
    )
    assert _chat(client, k2["key"]).status_code == 200


def test_custom_budget_requires_dates(client, admin):
    r = client.post(
        "/api/keys",
        json={
            "label": "bad",
            "allowed_providers": ["openrouter"],
            "budget_period": "custom",
        },
        headers=admin,
    )
    assert r.status_code == 422
