"""Configured token pricing — a hand-maintained price table used to estimate
cost when the provider doesn't report one. Editable in one place, surfaced
read-only at GET /api/models, and extensible at runtime via
MODEL_PRICING_OVERRIDES_PATH (a JSON file of extra entries merged on top at
import time) so a newly released model can be priced without a code deploy.
USD per 1,000,000 tokens.

When a (provider, model) pair isn't registered anywhere, `estimate_cost`
returns None rather than guessing — record that gap honestly
(cost_source="unknown") instead of inventing a number."""

import json
import os

Pricing = dict[str, float]

CONFIGURED_PRICING: dict[tuple[str, str], Pricing] = {
    ("openai", "gpt-4o-mini"): {"input": 0.15, "output": 0.60},
    ("openai", "gpt-4o"): {"input": 2.50, "output": 10.00},
    ("openai", "o4-mini"): {"input": 1.10, "output": 4.40},
    ("anthropic", "claude-3-5-haiku"): {"input": 0.80, "output": 4.00},
    ("anthropic", "claude-3-5-sonnet"): {"input": 3.00, "output": 15.00},
    ("anthropic", "claude-3-7-sonnet"): {"input": 3.00, "output": 15.00},
    # OpenRouter is itself a gateway; models keep their real vendor-prefixed slugs.
    # Through the OpenAI-compatible endpoint these are addressed as
    # "openrouter/<slug>", e.g. "openrouter/meta-llama/llama-3.3-70b-instruct".
    # ":free" slugs make a genuine OpenRouter call at $0 (rate-limited -> graceful sim fallback).
    ("openrouter", "meta-llama/llama-3.3-70b-instruct"): {"input": 0.10, "output": 0.32},
    ("openrouter", "google/gemma-4-31b-it:free"): {"input": 0.0, "output": 0.0},
    ("openrouter", "google/gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40},
    ("openrouter", "deepseek/deepseek-chat-v3.1"): {"input": 0.55, "output": 1.65},
    # Google Gemini via its OpenAI-compatible endpoint (addressed as "gemini/<model>").
    ("gemini", "gemini-2.5-flash"): {"input": 0.30, "output": 2.50},
    ("gemini", "gemini-2.5-flash-lite"): {"input": 0.10, "output": 0.40},
    ("gemini", "gemini-2.5-pro"): {"input": 1.25, "output": 10.00},
    # Embeddings bill input tokens only — output is always 0.
    ("openai", "text-embedding-3-small"): {"input": 0.02, "output": 0.0},
    ("openai", "text-embedding-3-large"): {"input": 0.13, "output": 0.0},
}


def _load_overrides() -> dict[tuple[str, str], Pricing]:
    """Optional extra (provider, model) -> {input, output} entries from a JSON
    file (a list of {"provider", "model", "input", "output"} objects), so an
    operator can price a newly released model without a code deploy. A missing
    or malformed file is silently ignored — it must never break startup."""
    path = os.environ.get("MODEL_PRICING_OVERRIDES_PATH", "").strip()
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return {
            (entry["provider"], entry["model"]): {
                "input": float(entry["input"]),
                "output": float(entry["output"]),
            }
            for entry in raw
        }
    except Exception:
        return {}


CONFIGURED_PRICING.update(_load_overrides())

PROVIDERS = ("openai", "anthropic", "openrouter", "gemini")


def price_for(provider: str, model: str) -> Pricing | None:
    """None => this (provider, model) isn't registered. Callers must not guess."""
    return CONFIGURED_PRICING.get((provider, model))


def estimate_cost(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """None => the model isn't registered and the provider didn't report a real
    charge either; the caller records cost_source="unknown" rather than a
    fabricated number."""
    p = price_for(provider, model)
    if p is None:
        return None
    cost = prompt_tokens / 1_000_000 * p["input"] + completion_tokens / 1_000_000 * p["output"]
    return round(cost, 6)


def models_catalog() -> list[dict]:
    return [
        {
            "provider": provider,
            "model": model,
            "input_per_1m": p["input"],
            "output_per_1m": p["output"],
        }
        for (provider, model), p in CONFIGURED_PRICING.items()
    ]
