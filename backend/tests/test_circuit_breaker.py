"""Phase 4: per-provider circuit breaker (app.providers.breaker_*), wired into
gateway.require_live_ready (the gate) and the four call sites that already
distinguish a real upstream HTTP error from a transport failure (the H1
work) — breaker_record_failure(is_upstream_fault=...) reuses that same
distinction: a provider 5xx or a transport failure counts against the
breaker, a 4xx never does (that's this request being wrong, not the
provider being down)."""

import httpx

from app import providers as p


def _upstream_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/x")
    response = httpx.Response(
        status_code, content=b'{"error":"boom"}', headers={"content-type": "application/json"}, request=request
    )
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ---- unit-level: the breaker's own state machine ----


def test_breaker_allows_by_default():
    assert p.breaker_allows("openai") is True


def test_breaker_opens_after_threshold_consecutive_upstream_faults():
    for _ in range(p._BREAKER_THRESHOLD - 1):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_allows("openai") is True  # not yet at threshold
    p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_allows("openai") is False  # threshold reached, cooling down


def test_breaker_ignores_non_upstream_faults():
    """A 4xx (client's own bad request/key/rate-limit) never counts — only a
    5xx or a transport failure (is_upstream_fault=True) does."""
    for _ in range(p._BREAKER_THRESHOLD * 2):
        p.breaker_record_failure("openai", is_upstream_fault=False)
    assert p.breaker_allows("openai") is True
    assert "openai" not in p.breaker_status()


def test_breaker_success_resets_failure_count():
    for _ in range(p._BREAKER_THRESHOLD - 1):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    p.breaker_record_success("openai")
    # if the count had carried over, one more failure would immediately open it
    p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_allows("openai") is True


def test_breaker_half_open_after_cooldown(monkeypatch):
    for _ in range(p._BREAKER_THRESHOLD):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_allows("openai") is False

    real_time = p.time.time
    monkeypatch.setattr(p.time, "time", lambda: real_time() + p._BREAKER_COOLDOWN_SECONDS + 1)
    assert p.breaker_allows("openai") is True  # cooldown elapsed -> trial call let through


def test_breaker_is_per_provider():
    for _ in range(p._BREAKER_THRESHOLD):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_allows("openai") is False
    assert p.breaker_allows("anthropic") is True


def test_breaker_status_reports_failures_and_open_state():
    p.breaker_record_failure("openai", is_upstream_fault=True)
    p.breaker_record_failure("openai", is_upstream_fault=True)
    status = p.breaker_status()
    assert status["openai"] == {"failures": 2, "open": False}

    for _ in range(p._BREAKER_THRESHOLD - 2):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    assert p.breaker_status()["openai"]["open"] is True


# ---- integration: wired into require_live_ready + the four call sites ----


def test_repeated_upstream_5xx_opens_the_breaker_then_503s(client, make_live_key, monkeypatch):
    def boom(*a, **kw):
        raise _upstream_error(500)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "text-embedding-3-small", "input": "hi"}
    headers = {"Authorization": f"Bearer {k['key']}"}

    for _ in range(p._BREAKER_THRESHOLD):
        r = client.post("/v1/embeddings", json=body, headers=headers)
        assert r.status_code == 500  # the provider's real status, passed through (H1)

    # breaker is now open — the NEXT call must fail fast with 503, without
    # even attempting send_request (prove it by making send_request itself
    # blow up if called again).
    def must_not_be_called(*a, **kw):
        raise AssertionError("send_request should not be called while the breaker is open")

    monkeypatch.setattr("app.providers.send_request", must_not_be_called)
    r = client.post("/v1/embeddings", json=body, headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["type"] == "provider_unavailable"


def test_repeated_upstream_4xx_never_opens_the_breaker(client, make_live_key, monkeypatch):
    """A 4xx is this request's own fault (bad params, bad key, rate limit) —
    not the provider being down — so it must never move the breaker at all,
    not even partially. Proven two ways: (1) breaker_status() never even
    gains an entry for the provider, checked after every single 4xx, not
    just at the end; (2) a real call still succeeds immediately afterward
    with zero special handling, proving the provider was never marked
    unhealthy in the first place."""

    def boom(*a, **kw):
        raise _upstream_error(400)

    monkeypatch.setattr("app.providers.send_request", boom)
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "text-embedding-3-small", "input": "hi"}
    headers = {"Authorization": f"Bearer {k['key']}"}

    for _ in range(p._BREAKER_THRESHOLD * 2):
        r = client.post("/v1/embeddings", json=body, headers=headers)
        assert r.status_code == 400  # never 503 — 4xx never counts against the breaker
        assert "openai" not in p.breaker_status()  # not even a partial failure count

    assert p.breaker_allows("openai") is True

    def succeed(url, headers, json_body, *, timeout=60.0):
        return {"object": "list", "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}]}

    monkeypatch.setattr("app.providers.send_request", succeed)
    r = client.post("/v1/embeddings", json=body, headers=headers)
    assert r.status_code == 200  # never gated — the breaker was never touched


def test_full_state_machine_healthy_open_half_open_closed(client, make_live_key, monkeypatch):
    """Walks the complete breaker lifecycle through the real HTTP surface, not
    just isolated unit assertions on app.providers.breaker_*:

        healthy
          -> _BREAKER_THRESHOLD qualifying (upstream-fault) failures
          -> OPEN
          -> a request while OPEN gets a gateway 503 WITHOUT an upstream call
          -> cooldown elapses
          -> HALF-OPEN trial
          -> success -> CLOSED (healthy again)
    """
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "text-embedding-3-small", "input": "hi"}
    headers = {"Authorization": f"Bearer {k['key']}"}

    # --- healthy ---
    assert p.breaker_allows("openai") is True
    assert "openai" not in p.breaker_status()

    # --- N qualifying upstream faults: real 500s from the (mocked) provider ---
    def fail_500(*a, **kw):
        raise _upstream_error(500)

    monkeypatch.setattr("app.providers.send_request", fail_500)
    for i in range(1, p._BREAKER_THRESHOLD + 1):
        r = client.post("/v1/embeddings", json=body, headers=headers)
        assert r.status_code == 500  # each one still gets the real upstream status (H1)
        if i < p._BREAKER_THRESHOLD:
            assert p.breaker_status()["openai"] == {"failures": i, "open": False}

    # --- OPEN ---
    assert p.breaker_status()["openai"]["open"] is True
    assert p.breaker_allows("openai") is False

    # --- a request while OPEN: gateway 503, WITHOUT ever attempting the upstream call ---
    def must_not_be_called(*a, **kw):
        raise AssertionError("send_request must not be called while the breaker is OPEN")

    monkeypatch.setattr("app.providers.send_request", must_not_be_called)
    r = client.post("/v1/embeddings", json=body, headers=headers)
    assert r.status_code == 503
    assert r.json()["detail"]["type"] == "provider_unavailable"

    # --- cooldown elapses ---
    real_time = p.time.time
    monkeypatch.setattr(p.time, "time", lambda: real_time() + p._BREAKER_COOLDOWN_SECONDS + 1)
    assert p.breaker_allows("openai") is True  # HALF-OPEN: one trial call now let through

    # --- HALF-OPEN trial succeeds ---
    def succeed(url, headers, json_body, *, timeout=60.0):
        return {"object": "list", "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}]}

    monkeypatch.setattr("app.providers.send_request", succeed)
    r = client.post("/v1/embeddings", json=body, headers=headers)
    assert r.status_code == 200

    # --- CLOSED (healthy again) ---
    assert p.breaker_allows("openai") is True
    assert "openai" not in p.breaker_status()


def test_full_state_machine_half_open_trial_failure_reopens(client, make_live_key, monkeypatch):
    """The other half-open branch: if the trial call itself fails again
    (still an upstream fault), the breaker must re-open rather than close —
    proves HALF-OPEN doesn't optimistically assume recovery."""
    k = make_live_key(allowed_providers=["openai"])
    body = {"model": "text-embedding-3-small", "input": "hi"}
    headers = {"Authorization": f"Bearer {k['key']}"}

    def fail_500(*a, **kw):
        raise _upstream_error(500)

    monkeypatch.setattr("app.providers.send_request", fail_500)
    for _ in range(p._BREAKER_THRESHOLD):
        client.post("/v1/embeddings", json=body, headers=headers)
    assert p.breaker_allows("openai") is False  # OPEN

    real_time = p.time.time
    monkeypatch.setattr(p.time, "time", lambda: real_time() + p._BREAKER_COOLDOWN_SECONDS + 1)
    assert p.breaker_allows("openai") is True  # HALF-OPEN

    # the trial call fails too
    r = client.post("/v1/embeddings", json=body, headers=headers)
    assert r.status_code == 500

    # re-opened: breaker_record_failure sets a fresh opened_at at the (fake)
    # current time, so the cooldown restarts rather than being treated as
    # already elapsed
    assert p.breaker_allows("openai") is False


def test_success_after_call_clears_the_breaker_state(client, make_live_key, mock_provider, monkeypatch):
    k = make_live_key(allowed_providers=["openai"])
    headers = {"Authorization": f"Bearer {k['key']}"}

    def boom(*a, **kw):
        raise _upstream_error(500)

    def succeed(url, headers, json_body, *, timeout=60.0):
        return {"object": "list", "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}]}

    monkeypatch.setattr("app.providers.send_request", boom)
    for _ in range(p._BREAKER_THRESHOLD - 1):
        r = client.post(
            "/v1/embeddings", json={"model": "text-embedding-3-small", "input": "hi"}, headers=headers
        )
        assert r.status_code == 500

    # a real (mocked-success) call in between resets the failure count
    monkeypatch.setattr("app.providers.send_request", succeed)
    r = client.post(
        "/v1/embeddings", json={"model": "text-embedding-3-small", "input": "hi"}, headers=headers
    )
    assert r.status_code == 200
    assert p.breaker_allows("openai") is True
    assert "openai" not in p.breaker_status()


def test_provider_status_endpoint_reports_breaker_state(admin_client):
    for _ in range(p._BREAKER_THRESHOLD):
        p.breaker_record_failure("openai", is_upstream_fault=True)
    r = admin_client.get("/api/providers")
    assert r.status_code == 200
    rows = {row["provider"]: row for row in r.json()}
    assert rows["openai"]["breaker_open"] is True
    assert rows["openai"]["breaker_failures"] == p._BREAKER_THRESHOLD
    assert rows["anthropic"]["breaker_open"] is False
    assert rows["anthropic"]["breaker_failures"] == 0
