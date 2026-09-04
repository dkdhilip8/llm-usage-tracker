"""Configured (demo) token pricing. NOT live provider pricing — these are
illustrative numbers, editable in one place, surfaced read-only at GET /api/models.
USD per 1,000,000 tokens."""

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
}

FALLBACK_PRICING: Pricing = {"input": 1.00, "output": 3.00}

PROVIDERS = ("openai", "anthropic", "openrouter")


def price_for(provider: str, model: str) -> Pricing:
    return CONFIGURED_PRICING.get((provider, model), FALLBACK_PRICING)


def estimate_cost(
    provider: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float:
    p = price_for(provider, model)
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
