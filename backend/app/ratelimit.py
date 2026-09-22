"""Atomic, distributed rate limiting via Redis (sliding-window log per key),
with a same-semantics in-process fallback when Redis is unreachable.

Scope, and a deliberate non-goal: this module enforces REQUEST-RATE limits
only — signups/hour per IP, workspace joins/hour per IP, Playground/proxy
requests/hour per workspace. It is abuse/DoS protection, answering "how many
requests can this identity make in a window". It has nothing to do with, and
must never be merged with, dollar-denominated spend control
(gateway.enforce_budget, gateway.enforce_workspace_cap — "how much money can
this key/workspace spend") or the permanent Postgres row-count cap (the
other half of gateway.enforce_workspace_quota — "how much data has this
workspace ever stored, all-time, no window"). Those three stay exactly as
they are: Postgres-backed, already durable and already correct regardless
of Redis's availability. That separation is what makes the failure-mode
decision below safe to make the way it's made.

---- Why atomic, and why Redis ----
The prior implementation (a module-level `dict[key, deque[float]]` sliding-
window log, one per call site) is correct only within a single process.
Two concurrent requests on the same key could both read the same
pre-append count and both pass, letting more than `limit` through — a
classic check-then-act race. Today's deployment (bare `uvicorn`, no
`--workers`, one Render instance) happens not to hit this because CPython's
GIL serializes the dict/deque operations themselves, but that stops being
true the moment traffic is ever split across more than one process or
instance — which is exactly the scaling step this module exists to
unblock. A Lua script (EVAL) executes atomically inside Redis — nothing
else runs between its read and its write — so the race is closed
correctly, in the store itself, not just moved to a bigger single process.

---- Window semantics ----
A sliding-window log, matching the prior in-memory behavior exactly (not a
fixed/bucketed counter, which can let up to 2x the limit through in a burst
that straddles a bucket boundary): every allowed hit is stored as a member
of a Redis sorted set, scored by its own timestamp. Each check first evicts
members older than the window, then compares the remaining count to the
limit before conditionally adding a new one — all inside the one atomic
script.

---- Key isolation ----
Every call site passes its own fully-qualified name (e.g. "signup:1.2.3.4",
"join:1.2.3.4", "playground:workspace:42") — a distinct Redis sorted set
per identity, mirroring the prior one-dict-key-per-identity isolation
exactly. Nothing in this module aggregates across identities.

---- Failure semantics: the decision this module exists to make explicit ----
Redis unreachable => FAIL OPEN, with the same-shape in-process fallback
engaged automatically (never "skip the check entirely"):

  - These three limiters are abuse/DoS protection, not financial controls.
    Provider spend is governed entirely by enforce_budget /
    enforce_workspace_cap, both Postgres-backed and completely unaffected
    by Redis's availability — a Redis outage can NEVER let a workspace
    spend past its cap. That's what makes fail-open acceptable *here*; the
    calculus would be different if this module gated spend.
  - Fail-CLOSED would turn an optional scaling dependency (Redis) into a
    hard availability dependency for signup / workspace-join / the
    Playground — a Redis blip would take down real product surfaces for a
    mechanism that exists to stop abuse, not to protect money. That
    tradeoff is backwards.
  - A bare fail-open (just skip the check) would silently regress every
    instance to fully unlimited for the outage's duration. Instead, on any
    Redis error, the check falls back to the exact deque-based
    sliding-window log this module replaces, scoped to this process. That's
    strictly better than no limiting, and no worse than today's actual
    behavior — this app runs single-process, so the fallback is fully
    correct until it's ever the case that traffic is split across multiple
    instances, at which point it degrades to a real per-instance bound
    rather than a coordinated one, not to nothing.
  - Every fallback engagement logs a warning (rate-limited to avoid log
    spam on a sustained outage) so this is observable, not silent.
"""

import logging
import time
import uuid
from collections import defaultdict, deque

import redis

from app.config import settings

logger = logging.getLogger("app.ratelimit")

_client: "redis.Redis | None" = None
_script = None  # a redis.commands.core.Script bound to _client, created together

# Local sliding-window fallback — same algorithm as the pre-Redis
# implementation, keyed by the same fully-qualified name every caller
# already passes to allow().
_fallback: dict[str, deque[float]] = defaultdict(deque)

_last_fallback_warning = 0.0
_FALLBACK_WARNING_INTERVAL_SECONDS = 30.0

# A failed Redis attempt is skipped for this long before the next retry,
# instead of re-attempting (and re-paying its failure latency) on every
# single call. This matters because a DNS-level failure (unresolvable host)
# is NOT bounded by socket_connect_timeout below — getaddrinfo() has its own
# OS-level timeout, measured at ~2s in this environment for an unresolvable
# host — so without this backoff, every request during an outage would pay
# that multi-second penalty individually rather than falling back instantly.
# Same shape as app.providers' circuit breaker, applied to this module's own
# one dependency.
_REDIS_RETRY_COOLDOWN_SECONDS = 5.0
_redis_down_until = 0.0

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, math.ceil(window))
return 1
"""


def _ensure_client():
    """The shared (Redis client, bound script) pair, or (None, None) if
    REDIS_URL isn't configured — callers then use the in-process fallback
    directly, same as an unreachable Redis. Lazily created: a bad/unreachable
    URL surfaces on the first real call, not at import time. Short connect/
    socket timeouts (200ms) matter here specifically because this sits in
    the hot path of every signup/join/proxy request — a slow-to-fail Redis
    must not become a slow-to-fail gateway."""
    global _client, _script
    if not settings.REDIS_URL:
        return None, None
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            health_check_interval=30,
        )
        _script = _client.register_script(_SLIDING_WINDOW_SCRIPT)
    return _client, _script


def close_client() -> None:
    """Called once from app.main's lifespan shutdown. Safe to call even if
    _ensure_client() was never invoked."""
    global _client, _script
    if _client is not None:
        _client.close()
        _client = None
        _script = None


def _fallback_allow(name: str, limit: int, window_seconds: float) -> bool:
    now = time.time()
    q = _fallback[name]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _warn_fallback_engaged(exc: Exception) -> None:
    global _last_fallback_warning
    now = time.time()
    if now - _last_fallback_warning >= _FALLBACK_WARNING_INTERVAL_SECONDS:
        logger.warning(
            "rate limiter: Redis unreachable (%s) — falling back to the "
            "per-process limiter until it recovers",
            exc,
        )
        _last_fallback_warning = now


def reset_for_tests() -> None:
    """Test-only: clears every rate-limit counter — the local fallback dict,
    the Redis-down backoff timer, and, if Redis is configured and reachable,
    every key in the Redis logical DB it points at — so counters and outage
    state never leak between tests. Not called by application code."""
    global _redis_down_until
    _fallback.clear()
    _redis_down_until = 0.0
    client, _ = _ensure_client()
    if client is not None:
        try:
            client.flushdb()
        except redis.RedisError:
            pass


def allow(name: str, *, limit: int, window_seconds: float) -> bool:
    """True if this call may proceed, False if `name` has already had
    `limit` allowed hits within the last `window_seconds`. `name` must
    already be the fully-qualified, isolated identity (e.g.
    "signup:1.2.3.4") — see the module docstring's "Key isolation" section.

    On any Redis error (unreachable, timeout, ...), falls back to a
    per-process sliding-window check instead of raising or silently
    allowing everything through — see "Failure semantics" above. While
    Redis is believed down (within _REDIS_RETRY_COOLDOWN_SECONDS of the last
    failure), skips attempting it entirely rather than re-paying a possibly
    multi-second failure per call."""
    global _redis_down_until
    client, script = _ensure_client()
    if client is None:
        return _fallback_allow(name, limit, window_seconds)
    now = time.time()
    if now < _redis_down_until:
        return _fallback_allow(name, limit, window_seconds)
    member = f"{now!r}:{uuid.uuid4().hex}"
    try:
        result = script(keys=[name], args=[now, window_seconds, limit, member])
        return bool(result)
    except redis.RedisError as exc:
        _redis_down_until = now + _REDIS_RETRY_COOLDOWN_SECONDS
        _warn_fallback_engaged(exc)
        return _fallback_allow(name, limit, window_seconds)
