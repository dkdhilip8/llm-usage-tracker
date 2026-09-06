"""Usage-anomaly detection + attribution over `usage_logs`.

Rule-based, no LLM. Compares a 24h "current" window against a trailing baseline
(7-day daily average for cost, the prior single day for tokens). Each detector
yields self-describing alert dicts; `investigate()` decomposes a fired alert into
the key / model / request-volume that drove the change and writes a plain-English
`summary`.
"""

import random
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AllowedProvider, UsageLog, VirtualKey
from app.pricing import estimate_cost
from app.security import new_key

SPIKE_RATIO = 1.5
CRITICAL_RATIO = 3.0
MIN_ABS_COST = 0.0005
MIN_ABS_TOKENS = 500
CURRENT_HOURS = 24
BASELINE_DAYS = 7


# ---------- window + aggregation helpers ----------
def _windows(now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    cur_start = now - timedelta(hours=CURRENT_HOURS)
    return {
        "now": now,
        "cur_start": cur_start,
        "cur_end": now,
        "base_start": now - timedelta(days=BASELINE_DAYS, hours=CURRENT_HOURS),
        "base_end": cur_start,
        "prev_start": now - timedelta(hours=2 * CURRENT_HOURS),
        "prev_end": cur_start,
    }


def _scoped(user_ids: list[int] | None):
    """A `where` term restricting to these owners' keys, or None for all."""
    if user_ids is None:
        return None
    return UsageLog.key_id.in_(
        select(VirtualKey.id).where(VirtualKey.user_id.in_(user_ids))
    )


def _totals(db: Session, start: datetime, end: datetime, user_ids: list[int] | None = None) -> dict:
    stmt = select(
        func.coalesce(func.sum(UsageLog.cost), 0),
        func.coalesce(func.sum(UsageLog.total_tokens), 0),
        func.count(),
    ).where(UsageLog.ts >= start, UsageLog.ts < end)
    scoped = _scoped(user_ids)
    if scoped is not None:
        stmt = stmt.where(scoped)
    row = db.execute(stmt).one()
    return {"cost": float(row[0]), "tokens": int(row[1]), "requests": int(row[2])}


def _by(db: Session, start: datetime, end: datetime, *cols, user_ids: list[int] | None = None) -> dict:
    stmt = (
        select(
            *cols,
            func.coalesce(func.sum(UsageLog.cost), 0),
            func.coalesce(func.sum(UsageLog.total_tokens), 0),
            func.count(),
        )
        .where(UsageLog.ts >= start, UsageLog.ts < end)
        .group_by(*cols)
    )
    scoped = _scoped(user_ids)
    if scoped is not None:
        stmt = stmt.where(scoped)
    out: dict = {}
    for r in db.execute(stmt):
        k = r[0] if len(cols) == 1 else tuple(r[: len(cols)])
        out[k] = {"cost": float(r[-3]), "tokens": int(r[-2]), "requests": int(r[-1])}
    return out


def _key_labels(db: Session, user_ids: list[int] | None = None) -> dict[int, str]:
    stmt = select(VirtualKey.id, VirtualKey.label)
    if user_ids is not None:
        stmt = stmt.where(VirtualKey.user_id.in_(user_ids))
    return {row[0]: row[1] for row in db.execute(stmt)}


def _pct_change(current: float, baseline: float) -> int:
    if baseline <= 0:
        return 0
    return round((current / baseline - 1) * 100)


def _usd(n: float) -> str:
    if n >= 1:
        return f"${n:.2f}"
    if n >= 0.01:
        return f"${n:.4f}"
    return f"${n:.6f}"


def _severity(current: float, baseline: float) -> str:
    return "critical" if baseline > 0 and current >= baseline * CRITICAL_RATIO else "warning"


# ---------- alert builders ----------
def _key_cost_alert(key_id: int, label: str, current: float, baseline_avg: float, w: dict) -> dict:
    pct = _pct_change(current, baseline_avg)
    return {
        "id": f"cost_spike:key:{key_id}",
        "type": "cost_spike",
        "severity": _severity(current, baseline_avg),
        "title": "Cost spike",
        "detail": f"{label} usage is {pct}% above its {BASELINE_DAYS}-day daily average.",
        "metric": "cost",
        "current": round(current, 6),
        "baseline": round(baseline_avg, 6),
        "pct_change": pct,
        "scope": {"kind": "key", "key_id": key_id, "key_label": label},
        "window": {
            "current_start": w["cur_start"].isoformat(),
            "current_end": w["cur_end"].isoformat(),
            "baseline_start": w["base_start"].isoformat(),
            "baseline_end": w["base_end"].isoformat(),
        },
    }


def _global_cost_alert(current: float, baseline_avg: float, w: dict) -> dict:
    pct = _pct_change(current, baseline_avg)
    return {
        "id": "cost_spike:global:all",
        "type": "cost_spike",
        "severity": _severity(current, baseline_avg),
        "title": "Cost spike",
        "detail": f"Total LLM cost is {pct}% above its {BASELINE_DAYS}-day daily average.",
        "metric": "cost",
        "current": round(current, 6),
        "baseline": round(baseline_avg, 6),
        "pct_change": pct,
        "scope": {"kind": "global"},
        "window": {
            "current_start": w["cur_start"].isoformat(),
            "current_end": w["cur_end"].isoformat(),
            "baseline_start": w["base_start"].isoformat(),
            "baseline_end": w["base_end"].isoformat(),
        },
    }


def _global_token_alert(current: int, prev: int, w: dict) -> dict:
    pct = _pct_change(current, prev)
    return {
        "id": "token_spike:global:all",
        "type": "token_spike",
        "severity": _severity(current, prev),
        "title": "Token spike",
        "detail": f"Total token consumption is {pct}% above yesterday.",
        "metric": "tokens",
        "current": current,
        "baseline": prev,
        "pct_change": pct,
        "scope": {"kind": "global"},
        "window": {
            "current_start": w["cur_start"].isoformat(),
            "current_end": w["cur_end"].isoformat(),
            "baseline_start": w["prev_start"].isoformat(),
            "baseline_end": w["prev_end"].isoformat(),
        },
    }


# ---------- detection ----------
def list_alerts(db: Session, user_ids: list[int] | None = None) -> list[dict]:
    w = _windows()
    labels = _key_labels(db, user_ids)
    alerts: list[dict] = []

    # Per-key cost spikes are the headline "who". Per-model spikes are almost always
    # a re-slice of the same event, so they surface as investigation *contributors*
    # rather than their own cards.
    cur_key = _by(db, w["cur_start"], w["cur_end"], UsageLog.key_id, user_ids=user_ids)
    base_key = _by(db, w["base_start"], w["base_end"], UsageLog.key_id, user_ids=user_ids)
    for key_id, cur in cur_key.items():
        base_avg = base_key.get(key_id, {}).get("cost", 0.0) / BASELINE_DAYS
        if base_avg > 0 and cur["cost"] >= base_avg * SPIKE_RATIO and cur["cost"] >= MIN_ABS_COST:
            alerts.append(
                _key_cost_alert(key_id, labels.get(key_id, f"key {key_id}"), cur["cost"], base_avg, w)
            )

    cur_tot = _totals(db, w["cur_start"], w["cur_end"], user_ids)
    # Fallback: a global cost spike not attributable to any single key.
    if not any(a["type"] == "cost_spike" for a in alerts):
        base_avg_cost = _totals(db, w["base_start"], w["base_end"], user_ids)["cost"] / BASELINE_DAYS
        if (
            base_avg_cost > 0
            and cur_tot["cost"] >= base_avg_cost * SPIKE_RATIO
            and cur_tot["cost"] >= MIN_ABS_COST
        ):
            alerts.append(_global_cost_alert(cur_tot["cost"], base_avg_cost, w))

    prev_tot = _totals(db, w["prev_start"], w["prev_end"], user_ids)
    if (
        prev_tot["tokens"] > 0
        and cur_tot["tokens"] >= prev_tot["tokens"] * SPIKE_RATIO
        and cur_tot["tokens"] >= MIN_ABS_TOKENS
    ):
        alerts.append(_global_token_alert(cur_tot["tokens"], prev_tot["tokens"], w))

    order = {"critical": 0, "warning": 1}
    alerts.sort(key=lambda a: (order[a["severity"]], -a["pct_change"]))
    return alerts


# ---------- investigation ----------
def _narrative(metric: str, baseline: float, current: float, pct: int, contributors: list[dict]) -> str:
    unit = "cost" if metric == "cost" else "token usage"
    lo = _usd(baseline) if metric == "cost" else f"{int(baseline):,} tokens"
    hi = _usd(current) if metric == "cost" else f"{int(current):,} tokens"
    parts = [f"The {unit} rose from {lo} to {hi} (+{pct}%)"]
    top = [c for c in contributors if c["kind"] != "volume"][:2]
    if top:
        joined = " and ".join(f"{c['label']} ({c['pct']}% of the increase)" for c in top)
        parts.append(f", driven mainly by {joined}")
    vol = next((c for c in contributors if c["kind"] == "volume"), None)
    if vol:
        parts.append(f". Request volume is {'up' if vol['pct'] >= 0 else 'down'} "
                     f"{abs(vol['pct'])}% versus the baseline")
    return "".join(parts) + "."


def investigate(db: Session, alert_id: str, user_ids: list[int] | None = None) -> dict | None:
    """Decompose the 24h change (for the alert's metric) into the key, model, and
    request-volume that drove it. Contributors are each a *share of the increase*
    and deliberately overlap (top key and top model often coincide), so they do
    not sum to 100 — matching the product mock."""
    try:
        atype, kind, sid = alert_id.split(":", 2)
    except ValueError:
        return None
    w = _windows()

    if atype == "cost_spike":
        metric, field, div = "cost", "cost", BASELINE_DAYS
        base_start, base_end = w["base_start"], w["base_end"]
    elif atype == "token_spike":
        metric, field, div = "tokens", "tokens", 1
        base_start, base_end = w["prev_start"], w["prev_end"]
    else:
        return None

    cur_tot = _totals(db, w["cur_start"], w["cur_end"], user_ids)
    base_tot = _totals(db, base_start, base_end, user_ids)
    cur_val, base_val = cur_tot[field], base_tot[field] / div
    base_reqs = base_tot["requests"] / div
    delta = cur_val - base_val
    if delta <= 0:
        return None

    cur_key = _by(db, w["cur_start"], w["cur_end"], UsageLog.key_id, user_ids=user_ids)
    base_key = _by(db, base_start, base_end, UsageLog.key_id, user_ids=user_ids)
    cur_model = _by(db, w["cur_start"], w["cur_end"], UsageLog.provider, UsageLog.model, user_ids=user_ids)
    base_model = _by(db, base_start, base_end, UsageLog.provider, UsageLog.model, user_ids=user_ids)
    labels = _key_labels(db, user_ids)

    contributors: list[dict] = []

    kid, kd = max(
        ((k, c[field] - base_key.get(k, {}).get(field, 0.0) / div) for k, c in cur_key.items()),
        key=lambda t: t[1],
        default=(None, 0.0),
    )
    if kid is not None and kd > 0:
        contributors.append({
            "label": f"{labels.get(kid, f'key {kid}')} key", "kind": "key",
            "pct": round(kd / delta * 100), "detail": _fmt(metric, kd), "key_id": kid,
        })

    pm, md = max(
        ((k, c[field] - base_model.get(k, {}).get(field, 0.0) / div) for k, c in cur_model.items()),
        key=lambda t: t[1],
        default=(None, 0.0),
    )
    if pm is not None and md > 0:
        contributors.append({
            "label": f"{pm[1]} model", "kind": "model",
            "pct": round(md / delta * 100), "detail": _fmt(metric, md),
            "provider": pm[0], "model": pm[1],
        })

    # volume effect: the extra requests vs a baseline day, priced at the baseline
    # average cost/tokens per request — the share of the rise explained by "more calls".
    per_req = (base_tot[field] / base_tot["requests"]) if base_tot["requests"] else 0.0
    vol_effect = max(cur_tot["requests"] - base_reqs, 0.0) * per_req
    contributors.append({
        "label": "Higher request volume", "kind": "volume",
        "pct": round(vol_effect / delta * 100),
        "detail": f"{cur_tot['requests']} vs {base_reqs:.0f}/day",
    })

    contributors.sort(key=lambda c: -c["pct"])
    pct = _pct_change(cur_val, base_val)

    related: dict = {"start": w["cur_start"].isoformat(), "end": w["cur_end"].isoformat()}
    if kind == "key" and sid.isdigit():
        related["key_id"] = int(sid)
    elif kind == "model" and "/" in sid:
        related["provider"], related["model"] = sid.split("/", 1)

    return {
        "alert_id": alert_id,
        "metric": metric,
        "headline": {
            "metric": metric,
            "baseline": round(base_val, 6),
            "current": round(cur_val, 6),
            "pct_change": pct,
        },
        "contributors": contributors,
        "summary": _narrative(metric, base_val, cur_val, pct, contributors),
        "related_query": related,
        "analyzed": ["key", "provider", "model", "tokens", "cost"],
    }


def _fmt(metric: str, v: float) -> str:
    return f"+{_usd(v)}" if metric == "cost" else f"+{int(round(v)):,} tokens"


# ---------- demo helper ----------
_DEMO_ENG = "Engineering (demo)"
_DEMO_PROD = "Product (demo)"


def _ensure_key(db: Session, user_id: int, label: str, providers: list[str]) -> VirtualKey:
    vk = db.scalar(
        select(VirtualKey).where(
            VirtualKey.label == label,
            VirtualKey.user_id == user_id,
            VirtualKey.revoked_at.is_(None),
        )
    )
    if vk:
        return vk
    _, hashed, prefix = new_key()
    vk = VirtualKey(
        user_id=user_id,
        label=label,
        key_hash=hashed,
        key_prefix=prefix,
        allowed_providers=[AllowedProvider(provider=p) for p in providers],
    )
    db.add(vk)
    db.flush()
    return vk


def _row(key: VirtualKey, provider: str, model: str, ts: datetime, pt: int, ct: int) -> UsageLog:
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
        latency_ms=random.randint(300, 2200),
        status="success",
        ts=ts,
    )


def inject_demo_spike(db: Session) -> dict:
    """Replace the two demo keys' usage with 10 quiet baseline days plus a
    deliberate last-24h spike on the Engineering key (more requests, larger
    prompts, a shift toward gpt-4o). Deterministic and idempotent — running it
    again produces the same ~3x cost / ~1.9x token anomaly."""
    from app.bootstrap import demo_user_id

    rng = random.Random(42)
    now = datetime.now(UTC)
    uid = demo_user_id(db)
    eng = _ensure_key(db, uid, _DEMO_ENG, ["openai", "openrouter"])
    prod = _ensure_key(db, uid, _DEMO_PROD, ["openrouter"])
    db.query(UsageLog).filter(UsageLog.key_id.in_([eng.id, prod.id])).delete(
        synchronize_session=False
    )

    rows: list[UsageLog] = []
    for d in range(BASELINE_DAYS + 3, 0, -1):
        day = now - timedelta(days=d)
        for _ in range(5):
            ts = day + timedelta(minutes=rng.uniform(0, 1440))
            rows.append(_row(eng, "openai", "gpt-4o-mini", ts,
                             rng.randint(300, 500), rng.randint(120, 260)))
        for _ in range(3):
            ts = day + timedelta(minutes=rng.uniform(0, 1440))
            rows.append(_row(eng, "openai", "gpt-4o", ts,
                             rng.randint(300, 500), rng.randint(150, 260)))
        for _ in range(2):
            ts = day + timedelta(minutes=rng.uniform(0, 1440))
            rows.append(_row(eng, "openai", "o4-mini", ts,
                             rng.randint(300, 500), rng.randint(150, 260)))
        for _ in range(3):
            ts = day + timedelta(minutes=rng.uniform(0, 1440))
            rows.append(_row(prod, "openrouter", "meta-llama/llama-3.3-70b-instruct", ts,
                             rng.randint(150, 400), rng.randint(60, 220)))

    # last 24h — Engineering spikes
    for _ in range(6):
        ts = now - timedelta(minutes=rng.uniform(10, 1400))
        rows.append(_row(eng, "openai", "gpt-4o", ts,
                         rng.randint(500, 800), rng.randint(300, 550)))
    for _ in range(3):
        ts = now - timedelta(minutes=rng.uniform(10, 1400))
        rows.append(_row(eng, "openai", "o4-mini", ts,
                         rng.randint(450, 700), rng.randint(220, 420)))
    for _ in range(4):
        ts = now - timedelta(minutes=rng.uniform(10, 1400))
        rows.append(_row(eng, "openai", "gpt-4o-mini", ts,
                         rng.randint(400, 650), rng.randint(200, 350)))
    for _ in range(3):
        ts = now - timedelta(minutes=rng.uniform(10, 1400))
        rows.append(_row(prod, "openrouter", "meta-llama/llama-3.3-70b-instruct", ts,
                         rng.randint(150, 400), rng.randint(60, 220)))

    db.add_all(rows)
    db.commit()
    return {"inserted": len(rows), "key_label": eng.label}
