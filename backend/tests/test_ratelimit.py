"""H2 (Phase 4 reliability): the Redis-backed atomic rate limiter
(app.ratelimit). Three things this suite exists to prove, matching exactly
what motivated the rewrite:

1. Atomicity/concurrency-safety: a real burst of CONCURRENT callers on the
   same key never lets more than `limit` through — the property a
   single-process, non-atomic check-then-append (the prior implementation)
   cannot guarantee once it's not the only process. Skipped if this test
   environment has no reachable Redis (docker-compose's `redis` service);
   the module still works via its fallback in that case, but the fallback
   makes no new atomicity claim — see its own test below.
2. Correct sliding-window semantics (not a fixed/bucketed window).
3. The documented fail-open-with-local-fallback behavior when Redis is
   unreachable: still enforces a limit (never "let everything through"),
   never raises, and is observable (logs a warning).

Integration coverage for the three real call sites (signup, workspace join,
Playground/proxy) lives in test_auth.py::test_signup_ip_throttle,
test_workspace.py::test_join_throttle, and test_playground_rate_limit_http
below — those prove this module is actually wired in, not just correct in
isolation.
"""

import logging
import threading

import pytest
import redis

from app import ratelimit


def _redis_reachable() -> bool:
    client, _ = ratelimit._ensure_client()
    if client is None:
        return False
    try:
        client.ping()
        return True
    except redis.RedisError:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_reachable(), reason="no reachable Redis in this test environment"
)


# ---- basic allow/deny + window semantics (backend-agnostic: whichever
# backend is active in this environment, real Redis or the fallback) ----


def test_allow_up_to_limit_then_blocks():
    for _ in range(3):
        assert ratelimit.allow("t:basic", limit=3, window_seconds=60) is True
    assert ratelimit.allow("t:basic", limit=3, window_seconds=60) is False


def test_keys_are_isolated():
    for _ in range(3):
        assert ratelimit.allow("t:iso-a", limit=3, window_seconds=60) is True
    assert ratelimit.allow("t:iso-a", limit=3, window_seconds=60) is False
    # a different name has its own independent budget
    assert ratelimit.allow("t:iso-b", limit=3, window_seconds=60) is True


def test_sliding_window_expires_old_hits(monkeypatch):
    real_time = ratelimit.time.time
    now = real_time()
    monkeypatch.setattr(ratelimit.time, "time", lambda: now)

    for _ in range(2):
        assert ratelimit.allow("t:window", limit=2, window_seconds=10) is True
    assert ratelimit.allow("t:window", limit=2, window_seconds=10) is False

    # advance past the window: the earlier hits must no longer count
    monkeypatch.setattr(ratelimit.time, "time", lambda: now + 11)
    assert ratelimit.allow("t:window", limit=2, window_seconds=10) is True


def test_sliding_window_not_a_fixed_bucket(monkeypatch):
    """A fixed/bucketed window would reset fully at a bucket boundary,
    letting a second full `limit` burst through immediately after. A true
    sliding window only frees up hits as they individually age out."""
    real_time = ratelimit.time.time
    now = real_time()
    monkeypatch.setattr(ratelimit.time, "time", lambda: now)
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is True

    monkeypatch.setattr(ratelimit.time, "time", lambda: now + 5)
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is True
    # both hits (t=0, t=5) are within the last 10s window as of t=5 -> full
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is False

    # at t=9, the t=0 hit is still within the window (9-0=9 < 10) -> still full
    monkeypatch.setattr(ratelimit.time, "time", lambda: now + 9)
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is False

    # at t=11, the t=0 hit has aged out (11-0=11 >= 10) but the t=5 one hasn't
    # (11-5=6 < 10) -> exactly one slot freed, not the full bucket
    monkeypatch.setattr(ratelimit.time, "time", lambda: now + 11)
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is True
    assert ratelimit.allow("t:slide", limit=2, window_seconds=10) is False


# ---- atomicity under real concurrency (requires a reachable Redis) ----


@requires_redis
def test_concurrent_callers_never_exceed_the_limit():
    """The property a non-atomic check-then-append cannot guarantee: fire
    50 threads at the same key with limit=10 simultaneously (a
    threading.Barrier holds every thread at the starting line so they hit
    Redis at the same instant, not staggered) and assert EXACTLY 10 succeed
    — never more, proving the Lua script's read-then-write is a single
    atomic Redis operation with no race window between them."""
    name = "t:concurrency"
    limit = 10
    n_callers = 50
    barrier = threading.Barrier(n_callers)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        r = ratelimit.allow(name, limit=limit, window_seconds=60)
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(n_callers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == n_callers
    assert sum(1 for r in results if r) == limit
    assert sum(1 for r in results if not r) == n_callers - limit


@requires_redis
def test_uses_real_redis_not_the_fallback_when_reachable():
    """Sanity check that the concurrency test above is actually exercising
    Redis, not silently falling back — the key must exist server-side."""
    client, _ = ratelimit._ensure_client()
    assert ratelimit.allow("t:real-backend", limit=5, window_seconds=60) is True
    assert client.zcard("t:real-backend") == 1
    assert "t:real-backend" not in ratelimit._fallback


# ---- fail-open-with-local-fallback when Redis is unreachable ----


def test_falls_back_and_still_enforces_a_limit_when_redis_is_unreachable(monkeypatch):
    class _BoomScript:
        def __call__(self, *a, **kw):
            raise redis.ConnectionError("simulated Redis outage")

    class _BoomClient:
        pass

    monkeypatch.setattr(ratelimit, "_ensure_client", lambda: (_BoomClient(), _BoomScript()))

    for _ in range(3):
        assert ratelimit.allow("t:fallback", limit=3, window_seconds=60) is True
    # still enforced — NOT "skip the check entirely"
    assert ratelimit.allow("t:fallback", limit=3, window_seconds=60) is False


def test_fallback_engagement_is_logged(monkeypatch, caplog):
    class _BoomScript:
        def __call__(self, *a, **kw):
            raise redis.ConnectionError("simulated Redis outage")

    monkeypatch.setattr(ratelimit, "_ensure_client", lambda: (object(), _BoomScript()))
    monkeypatch.setattr(ratelimit, "_last_fallback_warning", 0.0)

    with caplog.at_level(logging.WARNING, logger="app.ratelimit"):
        ratelimit.allow("t:logged", limit=5, window_seconds=60)
    assert any("Redis unreachable" in rec.message for rec in caplog.records)


def test_redis_failure_engages_a_backoff_instead_of_retrying_every_call(monkeypatch):
    """A DNS-level failure (unresolvable host) is NOT bounded by
    socket_connect_timeout — measured at ~2s in this environment, regardless
    of the 200ms socket timeout — so without a backoff, every single request
    during an outage would pay that penalty individually. After the first
    failure, subsequent calls within _REDIS_RETRY_COOLDOWN_SECONDS must skip
    attempting Redis entirely (the script is never invoked again) rather
    than re-discovering the same outage on every call."""
    calls = {"n": 0}

    class _BoomScript:
        def __call__(self, *a, **kw):
            calls["n"] += 1
            raise redis.ConnectionError("simulated Redis outage")

    monkeypatch.setattr(ratelimit, "_ensure_client", lambda: (object(), _BoomScript()))
    monkeypatch.setattr(ratelimit, "_redis_down_until", 0.0)

    assert ratelimit.allow("t:backoff", limit=100, window_seconds=60) is True
    assert calls["n"] == 1  # the one call that discovers the outage

    for _ in range(5):
        ratelimit.allow("t:backoff", limit=100, window_seconds=60)
    assert calls["n"] == 1  # every call during the cooldown skipped Redis entirely

    # once the cooldown elapses, the next call retries Redis
    monkeypatch.setattr(ratelimit, "_redis_down_until", 0.0)
    ratelimit.allow("t:backoff", limit=100, window_seconds=60)
    assert calls["n"] == 2


def test_no_redis_configured_uses_fallback_directly(monkeypatch):
    """REDIS_URL unset (e.g. a fresh local dev clone with no Redis running at
    all) must behave exactly like a reachable-but-then-failing Redis: still
    enforces the limit via the fallback, never raises, never skips the
    check."""
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "")
    monkeypatch.setattr(ratelimit, "_client", None)
    monkeypatch.setattr(ratelimit, "_script", None)

    for _ in range(2):
        assert ratelimit.allow("t:no-redis-configured", limit=2, window_seconds=60) is True
    assert ratelimit.allow("t:no-redis-configured", limit=2, window_seconds=60) is False


# ---- integration: the one real call site with no existing HTTP-level test ----


def test_playground_rate_limit_http(client, make_live_key, mock_provider, monkeypatch):
    """test_auth.py::test_signup_ip_throttle and test_workspace.py::
    test_join_throttle already cover their call sites end-to-end; this
    closes the one gap — gateway.enforce_workspace_quota's rate-limit half,
    exercised via the real Playground endpoint (/v1/proxy/chat)."""
    from app.config import settings

    monkeypatch.setattr(settings, "PLAYGROUND_REQUESTS_PER_HOUR", 2)
    k = make_live_key(allowed_providers=["openrouter"])
    body = {"provider": "openrouter", "model": "openrouter/test", "prompt": "hi"}
    headers = {"Authorization": f"Bearer {k['key']}"}

    for _ in range(2):
        r = client.post("/v1/proxy/chat", json=body, headers=headers)
        assert r.status_code == 200, r.text

    r = client.post("/v1/proxy/chat", json=body, headers=headers)
    assert r.status_code == 429
    assert r.json()["detail"]["type"] == "rate_limited"
