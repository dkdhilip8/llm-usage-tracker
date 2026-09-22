"""Provider credentials, liveness checks (cached), a per-provider circuit
breaker, and real upstream calls.

A server env var configures a provider instance-wide (usable by every workspace);
a workspace can also attach its own key (resolved in gateway.live_key_for). Every
proxied request makes a real call — a virtual key with allow_live off, or with no
configured provider key, gets an error, not a simulated response."""

import json
import time
from collections.abc import Iterator

import httpx

from app.config import settings

SUPPORTED = ("openai", "anthropic", "openrouter", "gemini")

ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Gemini is reached through Google's OpenAI-compatible surface, so it shares the
# openai/openrouter request+response shape (Bearer auth, chat/completions, SSE).
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

_LIVENESS_URLS = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/key",
    "gemini": f"{_GEMINI_BASE}/models",
}

_CHAT_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "gemini": f"{_GEMINI_BASE}/chat/completions",
}

# provider -> (valid: bool, checked_at: epoch seconds)
_cache: dict[str, tuple[bool, float]] = {}


# ---- shared outbound HTTP client (connection pooling) ----
# One long-lived httpx.Client for the process, instead of a fresh connection
# (and, for send_request/send_request_binary/send_multipart, a fresh
# httpx.post() call each spins up and tears down its own transport) per
# request — real keep-alive reuse to the same host now applies across calls,
# not just within one. Created lazily, closed from app.main's lifespan on
# shutdown. This is process-local state, same caveat as the in-memory rate
# limiter and liveness cache below: correct for this app's current
# single-process deployment (see the Dockerfile's CMD — no --workers), not
# yet shared across multiple instances/processes.
_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client()
    return _client


def close_client() -> None:
    """Called once from app.main's lifespan shutdown. Safe to call even if
    get_client() was never invoked (e.g. a test run that never made a real
    call)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ---- the one chokepoint for outbound provider HTTP calls ----
# Every adapter (app/adapters/*) and the legacy call_provider/stream_openai_compatible
# below funnel through these functions. Tests monkeypatch these (or the
# higher-level functions that call them) to avoid any real network call.
def send_request(url: str, headers: dict, json_body: dict, *, timeout: float = 60.0) -> dict:
    """One blocking POST, parsed JSON response. Raises httpx.HTTPStatusError (via
    raise_for_status) or a transport error on failure — callers turn that into a
    502 upstream_error."""
    r = get_client().post(url, headers=headers, json=json_body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def stream_request(
    url: str, headers: dict, json_body: dict, *, timeout: float = 60.0
) -> Iterator[str]:
    """Streaming POST: yields raw response lines (already text-decoded) exactly
    as httpx reassembles them from the chunked transfer — this is line-oriented
    because SSE itself is line-oriented (`event: ...` / `data: ...` / blank
    separators), so re-emitting each line reconstructs a spec-compliant stream
    without buffering the whole response first.

    On a real HTTP error status, the (usually short, JSON) error body is read
    before `raise_for_status()` — a streaming response's body isn't available
    on the exception otherwise. Callers that want the provider's real
    status+body (not just a generic failure) read it off `exc.response`."""
    with get_client().stream("POST", url, headers=headers, json=json_body, timeout=timeout) as r:
        if r.is_error:
            r.read()
        r.raise_for_status()
        yield from r.iter_lines()


def send_request_binary(
    url: str, headers: dict, json_body: dict, *, timeout: float = 60.0
) -> tuple[bytes, str]:
    """Like send_request, but for an endpoint whose response is never JSON
    (OpenAI's TTS returns raw audio bytes) — returns (content, content_type)
    untouched rather than trying to .json() it. Raises httpx.HTTPStatusError
    on a real error status (same as send_request; the response is already
    fully buffered by a non-streaming POST, so exc.response.content is
    available to the caller without any extra read)."""
    r = get_client().post(url, headers=headers, json=json_body, timeout=timeout)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


def send_multipart(
    url: str, headers: dict, data: dict, files: dict, *, timeout: float = 60.0
) -> tuple[bytes, str]:
    """Like send_request_binary, but for a multipart/form-data upload (audio
    transcription takes a file). `data` is the plain form fields, `files` is
    httpx's files= mapping (field name -> (filename, bytes, content_type)).
    `headers` must NOT include Content-Type — httpx sets the multipart
    boundary itself from `files`. Returns (content, content_type) rather than
    parsed JSON: a transcription's response_format can be plain text/srt/vtt,
    not just JSON, so parsing here would be wrong as often as it's right —
    the caller decides how to interpret the bytes."""
    r = get_client().post(url, headers=headers, data=data, files=files, timeout=timeout)
    r.raise_for_status()
    return r.content, r.headers.get("content-type", "application/octet-stream")


def resolved_key(provider: str) -> str:
    """The instance-wide server env var for this provider, if any."""
    return settings.provider_api_key(provider)


def key_source(provider: str) -> str:
    return "env" if settings.provider_api_key(provider) else "none"


def is_configured(provider: str) -> bool:
    """True when a server env var configures this provider instance-wide. A
    workspace's own attached key is resolved separately in gateway.live_key_for."""
    return bool(resolved_key(provider))


def _auth_headers(provider: str, key: str | None = None) -> dict[str, str]:
    key = resolved_key(provider) if key is None else key
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


def check_key(provider: str, api_key: str) -> bool:
    """One-off liveness check for an explicit key (an account's own). Not cached."""
    if not api_key:
        return False
    try:
        resp = get_client().get(
            _LIVENESS_URLS[provider], headers=_auth_headers(provider, api_key), timeout=8.0
        )
        return resp.status_code == 200
    except Exception:
        return False


def check_liveness(provider: str, *, force: bool = False) -> bool:
    """Cheap GET against the provider to confirm the key works. Cached for
    PROVIDER_CHECK_TTL seconds."""
    if not is_configured(provider):
        return False
    now = time.time()
    cached = _cache.get(provider)
    if cached and not force and now - cached[1] < settings.PROVIDER_CHECK_TTL:
        return cached[0]
    valid = False
    try:
        resp = get_client().get(
            _LIVENESS_URLS[provider], headers=_auth_headers(provider), timeout=8.0
        )
        valid = resp.status_code == 200
    except Exception:
        valid = False
    _cache[provider] = (valid, now)
    return valid


def status(*, force: bool = False) -> list[dict]:
    out = []
    for provider in SUPPORTED:
        configured = is_configured(provider)
        valid = check_liveness(provider, force=force) if configured else False
        checked = _cache.get(provider)
        out.append(
            {
                "provider": provider,
                "env_var": ENV_VARS[provider],
                "configured": configured,
                "valid": valid,
                "source": key_source(provider),
                "checked_at": (
                    time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(checked[1])
                    )
                    if checked
                    else None
                ),
            }
        )
    return out


def warm_cache() -> None:
    """Best-effort liveness check on startup for any configured provider."""
    for provider in SUPPORTED:
        if is_configured(provider):
            try:
                check_liveness(provider, force=True)
            except Exception:
                pass


# ---- circuit breaker ----
# A provider having a bad moment shouldn't mean every request to it pays the
# full timeout before failing — after enough consecutive upstream-fault
# failures, fail fast for a cool-down period instead of attempting the real
# call. "Upstream fault" means a transport failure (never reached the
# provider) or the provider's own 5xx (their overload/outage, not our
# request) — a 4xx never counts, since that's this specific request being
# wrong (bad params, bad key, rate limited), not the provider being down.
# gateway.require_live_ready checks breaker_allows() as the final gate
# before a live call; gateway's run_completion/run_native_completion/
# run_native_completion_raw/stream_native_passthrough record the outcome
# via breaker_record_success/_failure in the same except blocks that
# already distinguish HTTPStatusError from a transport failure (the H1
# work). Same process-local caveat as the rate limiter and liveness cache.
_BREAKER_THRESHOLD = 5  # consecutive upstream-fault failures before opening
_BREAKER_COOLDOWN_SECONDS = 30.0

_breaker: dict[str, dict] = {}  # provider -> {"failures": int, "opened_at": float | None}


def breaker_allows(provider: str) -> bool:
    """False when this provider's circuit is open and still cooling down."""
    state = _breaker.get(provider)
    if not state or state["opened_at"] is None:
        return True
    if time.time() - state["opened_at"] >= _BREAKER_COOLDOWN_SECONDS:
        return True  # cooldown elapsed -> let one trial call through (half-open)
    return False


def breaker_record_success(provider: str) -> None:
    _breaker.pop(provider, None)


def breaker_record_failure(provider: str, *, is_upstream_fault: bool) -> None:
    if not is_upstream_fault:
        return
    state = _breaker.setdefault(provider, {"failures": 0, "opened_at": None})
    state["failures"] += 1
    if state["failures"] >= _BREAKER_THRESHOLD:
        state["opened_at"] = time.time()


def breaker_status() -> dict[str, dict]:
    """For observability (GET /api/providers)."""
    now = time.time()
    out = {}
    for provider, state in _breaker.items():
        opened_at = state["opened_at"]
        out[provider] = {
            "failures": state["failures"],
            "open": opened_at is not None and now - opened_at < _BREAKER_COOLDOWN_SECONDS,
        }
    return out


# ---- real upstream calls ----
def call_provider(
    provider: str, model: str, prompt: str, api_key: str | None = None
) -> tuple[str, int, int, float | None]:
    """Returns (text, prompt_tokens, completion_tokens, actual_cost_usd).

    `api_key` overrides the resolved key (used for an account's own key).
    actual_cost is the real amount the provider charged when it reports one
    (OpenRouter does, via `usage.cost`); it is None for OpenAI/Anthropic/Gemini,
    whose APIs return token counts only — the caller then estimates from the price
    table. Raises on transport/HTTP failure."""
    headers = _auth_headers(provider, api_key)

    if provider == "anthropic":
        data = send_request(
            _CHAT_URLS[provider],
            headers,
            {
                "model": model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        text = "".join(block.get("text", "") for block in data.get("content", []))
        usage = data.get("usage", {})
        return (
            text,
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
            None,  # Anthropic returns no per-request cost
        )

    # openai, openrouter and gemini share the OpenAI chat-completions shape
    body: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if provider == "openrouter":
        headers = {
            **headers,
            "HTTP-Referer": "https://github.com/dkdhilip8/llm-usage-tracker",
            "X-Title": "LLM Usage Tracker",
        }
        body["usage"] = {"include": True}  # ask OpenRouter to return the real cost

    data = send_request(_CHAT_URLS[provider], headers, body)
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    actual_cost = usage.get("cost")  # present for OpenRouter, absent for OpenAI
    return (
        text,
        int(usage.get("prompt_tokens", 0)),
        int(usage.get("completion_tokens", 0)),
        float(actual_cost) if actual_cost is not None else None,
    )


def stream_openai_compatible(
    provider: str, model: str, prompt: str, api_key: str | None = None
) -> Iterator[tuple[str, object]]:
    """True SSE passthrough for OpenAI-shaped providers (openai, openrouter, gemini).

    Yields ("delta", text) for each content delta, then a final
    ("done", {"prompt_tokens", "completion_tokens", "cost"}). Raises on transport
    or HTTP error (the caller surfaces it as a 502)."""
    headers = _auth_headers(provider, api_key)
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    if provider == "openrouter":
        headers = {
            **headers,
            "HTTP-Referer": "https://github.com/dkdhilip8/llm-usage-tracker",
            "X-Title": "LLM Usage Tracker",
        }
        body["usage"] = {"include": True}
    else:  # openai, gemini
        body["stream_options"] = {"include_usage": True}

    pt = ct = 0
    cost: float | None = None
    for line in stream_request(_CHAT_URLS[provider], headers, body):
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices", []) or []:
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                yield ("delta", delta)
        u = chunk.get("usage")
        if u:
            pt = int(u.get("prompt_tokens", pt) or pt)
            ct = int(u.get("completion_tokens", ct) or ct)
            if u.get("cost") is not None:
                cost = float(u["cost"])
    yield ("done", {"prompt_tokens": pt, "completion_tokens": ct, "cost": cost})
