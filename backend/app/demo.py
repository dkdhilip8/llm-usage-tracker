"""Shared demo dataset — deterministic keys + ~30 days of simulated usage.

The public deployment is a single shared demo: every visitor sees the same
pre-populated data. This module (re)builds it. It is admin-only at the API layer
(`POST /api/demo/reset`) and can also run once on boot (`SEED_DEMO_DATA=true`)
so a fresh database self-populates.

Everything here is `simulated` / `configured` pricing — no provider calls. The
demo keys get random unusable hashes (no raw key is produced), so nothing here
can be used to send real traffic.
"""

import random
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AllowedProvider, UsageLog, VirtualKey
from app.pricing import estimate_cost

SEED = 20260904
HISTORY_DAYS = 30

# label -> (allowed providers, monthly budget or None, base requests/day,
#           [(provider, model, weight, tier), ...])
_SMALL, _MID, _LARGE = "small", "mid", "large"

_KEYS: list[tuple[str, list[str], float | None, int, list[tuple[str, str, float, str]]]] = [
    (
        "Engineering",
        ["openai", "anthropic", "openrouter"],
        50.0,
        14,
        [
            ("openai", "gpt-4o-mini", 0.40, _SMALL),
            ("openai", "gpt-4o", 0.25, _LARGE),
            ("openai", "o4-mini", 0.15, _MID),
            ("anthropic", "claude-3-5-sonnet", 0.10, _LARGE),
            ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.10, _MID),
        ],
    ),
    (
        "Data Science",
        ["openai", "anthropic"],
        30.0,
        9,
        [
            ("openai", "gpt-4o", 0.30, _LARGE),
            ("anthropic", "claude-3-5-sonnet", 0.30, _LARGE),
            ("anthropic", "claude-3-7-sonnet", 0.20, _LARGE),
            ("openai", "gpt-4o-mini", 0.20, _SMALL),
        ],
    ),
    (
        "Support Bot",
        ["openrouter"],
        10.0,
        20,
        [
            ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.60, _MID),
            ("openrouter", "google/gemini-2.5-flash-lite", 0.30, _SMALL),
            ("openrouter", "deepseek/deepseek-chat-v3.1", 0.10, _MID),
        ],
    ),
    (
        "Content Team",
        ["anthropic", "openrouter"],
        None,
        7,
        [
            ("anthropic", "claude-3-5-haiku", 0.50, _SMALL),
            ("anthropic", "claude-3-5-sonnet", 0.20, _LARGE),
            ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.30, _MID),
        ],
    ),
    (
        "Mobile App",
        ["openai"],
        20.0,
        11,
        [
            ("openai", "gpt-4o-mini", 0.70, _SMALL),
            ("openai", "o4-mini", 0.30, _MID),
        ],
    ),
]

_TOKENS = {
    _SMALL: ((150, 600), (80, 400)),
    _MID: ((300, 1200), (200, 900)),
    _LARGE: ((500, 2000), (400, 1500)),
}
_LATENCY = {_SMALL: (700, 250), _MID: (1400, 500), _LARGE: (2600, 900)}
_ERROR_RATE = 0.015


def _mk_key(label: str, providers: list[str], budget: float | None) -> VirtualKey:
    return VirtualKey(
        label=label,
        key_hash=secrets.token_hex(32),  # random & unusable — no raw key exists
        key_prefix="vk_demo_" + secrets.token_hex(2),
        allow_live=False,
        default_provider=providers[0] if len(providers) == 1 else None,
        monthly_budget_usd=budget,
        budget_period="month",
        allowed_providers=[AllowedProvider(provider=p) for p in providers],
    )


def _row(
    rng: random.Random,
    key: VirtualKey,
    provider: str,
    model: str,
    tier: str,
    ts: datetime,
) -> UsageLog:
    (p_lo, p_hi), (c_lo, c_hi) = _TOKENS[tier]
    pt = rng.randint(p_lo, p_hi)
    ct = rng.randint(c_lo, c_hi)
    errored = rng.random() < _ERROR_RATE
    if errored:
        ct = 0
    mu, sigma = _LATENCY[tier]
    latency = int(min(8000, max(120, rng.gauss(mu, sigma))))
    return UsageLog(
        key_id=key.id,
        request_id=secrets.token_hex(16),
        provider=provider,
        model=model,
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=pt + ct,
        cost=estimate_cost(provider, model, pt, ct),
        cost_source="configured",
        simulated=True,
        mode="simulated",
        latency_ms=0 if errored else latency,
        status="error" if errored else "success",
        ts=ts,
    )


def _generate(db: Session) -> int:
    rng = random.Random(SEED)
    now = datetime.now(UTC)
    rows: list[UsageLog] = []

    keys: list[tuple[VirtualKey, int, list[tuple[str, str, str]], list[float]]] = []
    for label, providers, budget, per_day, mix in _KEYS:
        vk = _mk_key(label, providers, budget)
        db.add(vk)
        models = [(m[0], m[1], m[3]) for m in mix]
        weights = [m[2] for m in mix]
        keys.append((vk, per_day, models, weights))
    db.flush()  # assign ids

    for d in range(HISTORY_DAYS, 0, -1):
        day = now - timedelta(days=d)
        weekend = day.weekday() >= 5
        for vk, per_day, models, weights in keys:
            factor = (0.35 if weekend else 1.0) * rng.uniform(0.7, 1.3)
            count = max(0, round(per_day * factor))
            for _ in range(count):
                provider, model, tier = rng.choices(models, weights=weights, k=1)[0]
                ts = day + timedelta(seconds=rng.uniform(0, 86_400))
                if ts >= now:
                    ts = now - timedelta(minutes=rng.uniform(1, 90))
                rows.append(_row(rng, vk, provider, model, tier, ts))

    # A deliberate last-~20h anomaly: Engineering hammers gpt-4o with big prompts.
    eng = keys[0][0]
    for _ in range(32):
        ts = now - timedelta(minutes=rng.uniform(15, 1200))
        pt = rng.randint(1500, 3000)
        ct = rng.randint(1200, 2200)
        rows.append(
            UsageLog(
                key_id=eng.id,
                request_id=secrets.token_hex(16),
                provider="openai",
                model="gpt-4o",
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=pt + ct,
                cost=estimate_cost("openai", "gpt-4o", pt, ct),
                cost_source="configured",
                simulated=True,
                mode="simulated",
                latency_ms=int(min(9000, max(600, rng.gauss(3200, 1100)))),
                status="success",
                ts=ts,
            )
        )

    db.add_all(rows)
    return len(rows)


def reset_demo_data(db: Session) -> dict:
    """Wipe all keys + usage and rebuild the shared demo dataset. Deterministic:
    the same seed produces the same ~30-day history and the same recent spike."""
    db.query(UsageLog).delete(synchronize_session=False)
    db.query(AllowedProvider).delete(synchronize_session=False)
    db.query(VirtualKey).delete(synchronize_session=False)
    db.flush()
    inserted = _generate(db)
    db.commit()
    return {"keys": len(_KEYS), "usage_rows": inserted, "days": HISTORY_DAYS}


def seed_if_empty(db: Session) -> dict:
    """Boot hook for SEED_DEMO_DATA=true — build the dataset only when there is
    no usage yet, so restarts and redeploys don't clobber a curated database."""
    existing = db.scalar(select(func.count()).select_from(UsageLog)) or 0
    if existing:
        return {"skipped": True, "reason": "usage_logs not empty", "existing": int(existing)}
    return {"skipped": False, **reset_demo_data(db)}
