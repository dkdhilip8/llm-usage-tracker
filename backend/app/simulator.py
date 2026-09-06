"""Deterministic LLM simulator for dry-run mode. Given (provider, model, prompt)
it always returns the same response, token counts and latency, so results are
reproducible. No network, no provider SDKs, no cost."""

import hashlib
import random
import time

from app.config import settings

_SENTENCES = [
    "The virtual key was validated and the request was routed through the proxy.",
    "Token usage is estimated from the prompt length and a seeded response size.",
    "Estimated cost is derived from the configured per-model pricing table.",
    "Every call is written to usage_logs with provider, model, tokens and latency.",
    "This response is generated locally; no external provider was contacted.",
    "The dashboard aggregates these rows into per-user and per-model rollups.",
    "Revoked keys are rejected before any usage is recorded.",
    "Swap the simulator for real provider calls without touching the schema.",
]


def _seeded_rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return random.Random(int(digest, 16))


def estimate_prompt_tokens(prompt: str) -> int:
    words = len(prompt.split())
    if words:
        return max(1, round(words * 1.3))
    return max(1, round(len(prompt) / 4))


def simulate_chat(
    provider: str, model: str, prompt: str
) -> tuple[str, int, int, int]:
    """Returns (response_text, prompt_tokens, completion_tokens, latency_ms)."""
    rng = _seeded_rng(provider, model, prompt)

    prompt_tokens = estimate_prompt_tokens(prompt)
    lo = max(20, int(prompt_tokens * 0.3))
    hi = max(lo + 10, min(1200, int(prompt_tokens * 2.5) + 40))
    completion_tokens = rng.randint(lo, hi)

    latency_ms = rng.randint(300, 2000)

    echo = prompt.strip().replace("\n", " ")
    if len(echo) > 80:
        echo = echo[:77] + "..."
    body = " ".join(rng.sample(_SENTENCES, k=rng.randint(2, 4)))
    response = (
        f"Simulated {provider}/{model} response"
        + (f' to: "{echo}". ' if echo else ". ")
        + body
    )

    # Real sleep so the Playground feels like a live call. Disabled in tests.
    if settings.SIMULATE_LATENCY_SLEEP:
        time.sleep(latency_ms / 1000)

    return response, prompt_tokens, completion_tokens, latency_ms
