"""Shared demo dataset + the public/admin split for the LinkedIn deployment."""


def test_demo_reset_requires_admin(client):
    assert client.post("/api/demo/reset").status_code == 403


def test_demo_reset_builds_shared_dataset(client, admin):
    r = client.post("/api/demo/reset", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert body["keys"] == 5
    assert body["usage_rows"] > 200
    assert body["days"] == 30

    # every public visitor now sees a populated dashboard / log / insights
    summary = client.get("/api/usage/summary").json()
    assert summary["total_requests"] == body["usage_rows"]
    assert summary["total_cost"] > 0

    reqs = client.get("/api/requests").json()
    assert len(reqs["items"]) > 0

    alerts = client.get("/api/insights/alerts").json()["alerts"]
    assert alerts and any(a["type"] == "cost_spike" for a in alerts)


def test_demo_reset_is_deterministic(client, admin):
    a = client.post("/api/demo/reset", headers=admin).json()
    b = client.post("/api/demo/reset", headers=admin).json()
    assert a == b
    # reset replaces rather than appends
    assert client.get("/api/usage/summary").json()["total_requests"] == b["usage_rows"]


def test_public_cannot_mutate_shared_data(client):
    assert client.get("/api/keys").status_code == 403
    assert (
        client.post(
            "/api/keys", json={"label": "x", "allowed_providers": ["openai"]}
        ).status_code
        == 403
    )
    assert client.post("/api/demo/reset").status_code == 403
    assert client.post("/api/insights/demo-spike").status_code == 403
    # the proxy still needs a virtual key -> no anonymous writes to usage_logs
    assert (
        client.post(
            "/v1/proxy/chat",
            json={"provider": "openrouter", "model": "x", "prompt": "hi"},
        ).status_code
        == 401
    )
