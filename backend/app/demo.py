"""Deterministic simulated datasets: the big shared *demo user* dataset (seen by
logged-out visitors) and a small per-user sample seeded on signup.

Everything here is `simulated` / `configured` pricing — no provider calls. Keys
get random unusable hashes (no raw key is produced)."""

import random
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bootstrap import demo_user_id
from app.models import AllowedProvider, UsageLog, VirtualKey
from app.pricing import estimate_cost

SEED = 20260904
_SMALL, _MID, _LARGE = "small", "mid", "large"

# (label, providers, monthly budget or None, base requests/day,
#  [(provider, model, weight, tier), ...])
KeySpec = tuple[str, list[str], float | None, int, list[tuple[str, str, float, str]]]

_DEMO_KEYS: list[KeySpec] = [
    ("Engineering", ["openai", "anthropic", "openrouter"], 50.0, 14, [
        ("openai", "gpt-4o-mini", 0.40, _SMALL),
        ("openai", "gpt-4o", 0.25, _LARGE),
        ("openai", "o4-mini", 0.15, _MID),
        ("anthropic", "claude-3-5-sonnet", 0.10, _LARGE),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.10, _MID),
    ]),
    ("Data Science", ["openai", "anthropic"], 30.0, 9, [
        ("openai", "gpt-4o", 0.30, _LARGE),
        ("anthropic", "claude-3-5-sonnet", 0.30, _LARGE),
        ("anthropic", "claude-3-7-sonnet", 0.20, _LARGE),
        ("openai", "gpt-4o-mini", 0.20, _SMALL),
    ]),
    ("Support Bot", ["openrouter"], 10.0, 20, [
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.60, _MID),
        ("openrouter", "google/gemini-2.5-flash-lite", 0.30, _SMALL),
        ("openrouter", "deepseek/deepseek-chat-v3.1", 0.10, _MID),
    ]),
    ("Content Team", ["anthropic", "openrouter"], None, 7, [
        ("anthropic", "claude-3-5-haiku", 0.50, _SMALL),
        ("anthropic", "claude-3-5-sonnet", 0.20, _LARGE),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.30, _MID),
    ]),
    ("Mobile App", ["openai"], 20.0, 11, [
        ("openai", "gpt-4o-mini", 0.70, _SMALL),
        ("openai", "o4-mini", 0.30, _MID),
    ]),
]

_SAMPLE_KEYS: list[KeySpec] = [
    ("My app", ["openai", "openrouter"], None, 9, [
        ("openai", "gpt-4o-mini", 0.55, _SMALL),
        ("openai", "gpt-4o", 0.25, _LARGE),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.20, _MID),
    ]),
    ("Batch jobs", ["openrouter"], 5.0, 6, [
        ("openrouter", "meta-llama/llama-3.3-70b-instruct", 0.7, _MID),
        ("openrouter", "google/gemini-2.5-flash-lite", 0.3, _SMALL),
    ]),
    ("Chat feature", ["anthropic"], None, 7, [
        ("anthropic", "claude-3-5-haiku", 0.7, _SMALL),
        ("anthropic", "claude-3-5-sonnet", 0.3, _LARGE),
    ]),
]

_TOKENS = {
    _SMALL: ((150, 600), (80, 400)),
    _MID: ((300, 1200), (200, 900)),
    _LARGE: ((500, 2000), (400, 1500)),
}
_LATENCY = {_SMALL: (700, 250), _MID: (1400, 500), _LARGE: (2600, 900)}
_ERROR_RATE = 0.015


def _mk_key(user_id: int, label: str, providers: list[str], budget: float | None) -> VirtualKey:
    return VirtualKey(
        user_id=user_id,
        label=label,
        key_hash=secrets.token_hex(32),  # random & unusable — no raw key exists
        key_prefix="vk_demo_" + secrets.token_hex(2),
        allow_live=False,
        default_provider=providers[0] if len(providers) == 1 else None,
        monthly_budget_usd=budget,
        budget_period="month",
        allowed_providers=[AllowedProvider(provider=p) for p in providers],
    )


def _row(rng: random.Random, key_id: int, provider: str, model: str, tier: str, ts: datetime) -> UsageLog:
    (p_lo, p_hi), (c_lo, c_hi) = _TOKENS[tier]
    pt = rng.randint(p_lo, p_hi)
    ct = rng.randint(c_lo, c_hi)
    errored = rng.random() < _ERROR_RATE
    if errored:
        ct = 0
    mu, sigma = _LATENCY[tier]
    latency = int(min(8000, max(120, rng.gauss(mu, sigma))))
    return UsageLog(
        key_id=key_id,
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


def _wipe_user(db: Session, user_id: int) -> None:
    # ON DELETE CASCADE on virtual_keys drops the user's usage_logs + allowed_providers
    db.query(VirtualKey).filter(VirtualKey.user_id == user_id).delete(synchronize_session=False)
    db.flush()


def _generate(db: Session, user_id: int, specs: list[KeySpec], *, days: int, spike: int, seed: int) -> int:
    rng = random.Random(seed)
    now = datetime.now(UTC)
    rows: list[UsageLog] = []

    keys = []
    for label, providers, budget, per_day, mix in specs:
        vk = _mk_key(user_id, label, providers, budget)
        db.add(vk)
        keys.append((vk, per_day, [(m[0], m[1], m[3]) for m in mix], [m[2] for m in mix]))
    db.flush()  # assign ids

    for d in range(days, 0, -1):
        day = now - timedelta(days=d)
        weekend = day.weekday() >= 5
        for vk, per_day, models, weights in keys:
            count = max(0, round(per_day * (0.35 if weekend else 1.0) * rng.uniform(0.7, 1.3)))
            for _ in range(count):
                provider, model, tier = rng.choices(models, weights=weights, k=1)[0]
                ts = day + timedelta(seconds=rng.uniform(0, 86_400))
                if ts >= now:
                    ts = now - timedelta(minutes=rng.uniform(1, 90))
                rows.append(_row(rng, vk.id, provider, model, tier, ts))

    # a deliberate last-~20h cost spike on the first key -> Insights fires
    eng = keys[0][0]
    for _ in range(spike):
        ts = now - timedelta(minutes=rng.uniform(15, 1200))
        pt, ct = rng.randint(1500, 3000), rng.randint(1200, 2200)
        rows.append(_row(rng, eng.id, "openai", "gpt-4o", _LARGE, ts))
        rows[-1].prompt_tokens, rows[-1].completion_tokens = pt, ct
        rows[-1].total_tokens = pt + ct
        rows[-1].cost = estimate_cost("openai", "gpt-4o", pt, ct)
        rows[-1].status = "success"

    db.add_all(rows)
    return len(rows)


def reset_demo_data(db: Session) -> dict:
    """Rebuild the shared demo-user dataset (logged-out view). Scoped to the demo
    account — never touches real users' data."""
    uid = demo_user_id(db)
    _wipe_user(db, uid)
    inserted = _generate(db, uid, _DEMO_KEYS, days=30, spike=32, seed=SEED)
    db.commit()
    return {"keys": len(_DEMO_KEYS), "usage_rows": inserted, "days": 30}


def seed_user_sample(db: Session, user_id: int) -> dict:
    """(Re)build a small starter dataset for one real user."""
    _wipe_user(db, user_id)
    inserted = _generate(
        db, user_id, _SAMPLE_KEYS, days=12, spike=14, seed=SEED ^ (user_id * 2654435761)
    )
    db.commit()
    return {"keys": len(_SAMPLE_KEYS), "usage_rows": inserted, "days": 12}


def seed_if_empty(db: Session) -> dict:
    """Boot hook for SEED_DEMO_DATA=true — build the demo dataset only when the
    demo account has no usage yet."""
    uid = demo_user_id(db)
    existing = db.scalar(
        select(func.count())
        .select_from(UsageLog)
        .where(UsageLog.key_id.in_(select(VirtualKey.id).where(VirtualKey.user_id == uid)))
    ) or 0
    if existing:
        return {"skipped": True, "existing": int(existing)}
    return {"skipped": False, **reset_demo_data(db)}
