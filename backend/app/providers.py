"""Provider credentials, liveness checks (cached), and real upstream calls.

Credentials resolve as: server env var wins; otherwise, only when
ALLOW_DB_PROVIDER_KEYS is on, an admin-entered key decrypted from the DB. Live
calls are dormant unless ENABLE_LIVE is set AND the calling virtual key has
allow_live AND the provider is configured & reachable."""

import json
import time
from collections.abc import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

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
# provider -> decrypted admin-entered key (only populated when ALLOW_DB_PROVIDER_KEYS)
_db_keys: dict[str, str] = {}


def resolved_key(provider: str) -> str:
    """Server env var wins; otherwise the DB key (if that feature is on)."""
    env = settings.provider_api_key(provider)
    if env:
        return env
    if settings.ALLOW_DB_PROVIDER_KEYS:
        return _db_keys.get(provider, "")
    return ""


def key_source(provider: str) -> str:
    if settings.provider_api_key(provider):
        return "env"
    if settings.ALLOW_DB_PROVIDER_KEYS and _db_keys.get(provider):
        return "db"
    return "none"


def is_configured(provider: str) -> bool:
    return bool(resolved_key(provider))


def _auth_headers(provider: str, key: str | None = None) -> dict[str, str]:
    key = resolved_key(provider) if key is None else key
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


# ---- admin-global DB-stored keys (encrypted; user_id NULL) ----
def load_db_keys(db: Session) -> None:
    """Refresh the in-process decrypted-key cache from the admin-global rows."""
    _db_keys.clear()
    if not settings.ALLOW_DB_PROVIDER_KEYS:
        return
    from app.crypto import decrypt
    from app.models import ProviderCredential

    for row in db.scalars(
        select(ProviderCredential).where(ProviderCredential.user_id.is_(None))
    ):
        plain = decrypt(row.ciphertext)
        if plain:
            _db_keys[row.provider] = plain


def _global_row(db: Session, provider: str):
    from app.models import ProviderCredential

    return db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.user_id.is_(None),
            ProviderCredential.provider == provider,
        )
    )


def set_db_key(db: Session, provider: str, api_key: str) -> str:
    """Encrypt + upsert the admin-global key. Returns its last 4 chars."""
    from app.crypto import encrypt
    from app.models import ProviderCredential

    last4 = api_key[-4:]
    row = _global_row(db, provider)
    if row is None:
        db.add(
            ProviderCredential(
                user_id=None, provider=provider, ciphertext=encrypt(api_key), last4=last4
            )
        )
    else:
        row.ciphertext = encrypt(api_key)
        row.last4 = last4
    db.commit()
    _db_keys[provider] = api_key
    _cache.pop(provider, None)  # force a fresh liveness check
    return last4


def clear_db_key(db: Session, provider: str) -> None:
    row = _global_row(db, provider)
    if row is not None:
        db.delete(row)
        db.commit()
    _db_keys.pop(provider, None)
    _cache.pop(provider, None)


def check_key(provider: str, api_key: str) -> bool:
    """One-off liveness check for an explicit key (an account's own). Not cached."""
    if not api_key:
        return False
    try:
        resp = httpx.get(
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
        resp = httpx.get(
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


def live_available(provider: str) -> bool:
    return settings.ENABLE_LIVE and is_configured(provider) and check_liveness(provider)


# ---- real upstream calls (only reached when live gate passes) ----
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
        r = httpx.post(
            _CHAT_URLS[provider],
            headers=headers,
            json={
                "model": model,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60.0,
        )
        r.raise_for_status()
        data = r.json()
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

    r = httpx.post(_CHAT_URLS[provider], headers=headers, json=body, timeout=60.0)
    r.raise_for_status()
    data = r.json()
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
    or HTTP error (caller falls back to simulated)."""
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
    with httpx.Client(timeout=60.0) as client:
        with client.stream(
            "POST", _CHAT_URLS[provider], headers=headers, json=body
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
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
