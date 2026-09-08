"""Shared request lifecycle for the proxy endpoints.

`routers/proxy.py` (friendly shape, used by the Playground) and
`routers/openai_compat.py` (OpenAI `/v1/chat/completions` shape) both build on these:
target resolution + provider ACL, monthly-budget enforcement, the simulate-vs-live
decision, and the usage-log write."""

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.db import SessionLocal
from app.models import ProviderCredential, UsageLog, VirtualKey
from app.pricing import PROVIDERS, estimate_cost, price_for
from app.simulator import simulate_chat

# in-process per-workspace Playground rate limiter (best-effort, single instance)
_pg_hits: dict[int, deque[float]] = defaultdict(deque)


def enforce_workspace_quota(db: Session, vk: VirtualKey) -> None:
    """Per-workspace request-rate + stored-row caps. Applies to every workspace."""
    ws_id = vk.workspace_id
    now = time.time()
    q = _pg_hits[ws_id]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= settings.PLAYGROUND_REQUESTS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"rate limit: {settings.PLAYGROUND_REQUESTS_PER_HOUR} requests/hour",
                "type": "rate_limited",
            },
        )
    rows = db.scalar(
        select(func.count())
        .select_from(UsageLog)
        .where(
            UsageLog.key_id.in_(
                select(VirtualKey.id).where(VirtualKey.workspace_id == ws_id)
            )
        )
    )
    if (rows or 0) >= settings.MAX_USAGE_ROWS_PER_WORKSPACE:
        raise HTTPException(
            status_code=429,
            detail={
                "message": (
                    f"usage cap reached ({settings.MAX_USAGE_ROWS_PER_WORKSPACE} rows). "
                    "Clear data from the Workspace page."
                ),
                "type": "quota_exceeded",
            },
        )
    q.append(now)


@dataclass
class CompletionResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    cost_source: str  # "provider" | "configured"
    mode: str  # "live" | "simulated"
    latency_ms: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def simulated(self) -> bool:
        return self.mode == "simulated"

    def pricing_block(self, provider: str, model: str) -> dict:
        p = price_for(provider, model)
        return {
            "input_per_1m": p["input"],
            "output_per_1m": p["output"],
            "source": provider if self.cost_source == "provider" else "configured",
        }


def authorize_provider(vk: VirtualKey, provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(422, f"provider must be one of {PROVIDERS}")
    if provider not in vk.provider_names():
        raise HTTPException(
            403, f"this key is not permitted to use provider '{provider}'"
        )


def resolve_target(vk: VirtualKey, model_str: str) -> tuple[str, str]:
    """Split an OpenAI-style `model` into (provider, model). `openrouter/x/y` ->
    ('openrouter', 'x/y'); a bare name falls back to the key's default_provider."""
    model_str = (model_str or "").strip()
    if "/" in model_str:
        provider, model = model_str.split("/", 1)
    else:
        provider, model = (vk.default_provider or ""), model_str
    provider = provider.lower()
    if not model:
        raise HTTPException(422, "model is required")
    if not provider:
        raise HTTPException(
            422,
            "model must be '<provider>/<model>' (e.g. 'openrouter/meta-llama/"
            "llama-3.3-70b-instruct') or the key needs a default_provider",
        )
    authorize_provider(vk, provider)
    return provider, model


_ROLLING_PERIODS = ("day", "week", "month")


def _budget_window(vk: VirtualKey):
    """Returns the SQL condition selecting spend inside the key's active budget
    window, or None when there is no active window (custom range with no dates,
    or a custom range that hasn't started / has ended)."""
    if vk.budget_period == "custom":
        if not (vk.budget_start and vk.budget_end):
            return None
        now = datetime.now(UTC)
        if now < vk.budget_start or now >= vk.budget_end:
            return None
        return (UsageLog.ts >= vk.budget_start) & (UsageLog.ts < vk.budget_end)
    period = vk.budget_period if vk.budget_period in _ROLLING_PERIODS else "month"
    return UsageLog.ts >= func.date_trunc(period, func.now())


def period_spend(db: Session, vk: VirtualKey) -> float:
    cond = _budget_window(vk)
    if cond is None:
        return 0.0
    val = db.scalar(
        select(func.coalesce(func.sum(UsageLog.cost), 0)).where(
            UsageLog.key_id == vk.id, cond
        )
    )
    return float(val or 0)


def _budget_label(vk: VirtualKey) -> str:
    if vk.budget_period == "custom" and vk.budget_start and vk.budget_end:
        last = (vk.budget_end - timedelta(days=1)).date()
        return f"for {vk.budget_start.date()}–{last}"
    return f"per {vk.budget_period}"


def enforce_budget(db: Session, vk: VirtualKey) -> None:
    if vk.monthly_budget_usd is None or _budget_window(vk) is None:
        return
    spent = period_spend(db, vk)
    if spent >= float(vk.monthly_budget_usd):
        raise HTTPException(
            status_code=402,
            detail={
                "message": (
                    f"budget of ${float(vk.monthly_budget_usd):.6f} {_budget_label(vk)} "
                    f"exhausted (spent ${spent:.6f})"
                ),
                "type": "budget_exceeded",
                "code": "402",
            },
        )


def _workspace_provider_key(db: Session, workspace_id: int, provider: str) -> str | None:
    """The workspace's own encrypted key for this provider, decrypted."""
    from app.crypto import decrypt

    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    return decrypt(row.ciphertext) if row else None


def workspace_has_live_provider(
    db: Session, workspace_id: int, provider_names: list[str]
) -> bool:
    """True when a real provider key exists for at least one of `provider_names` —
    a server env var, or one the workspace has attached. A key with no configured
    provider would silently fall back to simulated, so callers use this to refuse
    `allow_live`."""
    if any(providers.is_configured(p) for p in provider_names):
        return True
    attached = set(
        db.scalars(
            select(ProviderCredential.provider).where(
                ProviderCredential.workspace_id == workspace_id
            )
        )
    )
    return any(p in attached for p in provider_names)


def live_key_for(db: Session, vk: VirtualKey, provider: str) -> str | None:
    """The API key a live call for this key + provider would use: a server env var,
    then the workspace's own attached key."""
    envk = settings.provider_api_key(provider)
    if envk:
        return envk
    return _workspace_provider_key(db, vk.workspace_id, provider)


def live_spend_this_month(db: Session, workspace_id: int, provider: str) -> float:
    """A workspace's `mode='live'` cost on one provider since the 1st of the month."""
    val = db.scalar(
        select(func.coalesce(func.sum(UsageLog.cost), 0)).where(
            UsageLog.mode == "live",
            UsageLog.provider == provider,
            UsageLog.ts >= func.date_trunc("month", func.now()),
            UsageLog.key_id.in_(
                select(VirtualKey.id).where(VirtualKey.workspace_id == workspace_id)
            ),
        )
    )
    return float(val or 0)


def provider_cap(db: Session, workspace_id: int, provider: str) -> float:
    """The workspace's monthly live-spend cap for one provider (its own value, or
    the LIVE_CAP_DEFAULT_USD fallback)."""
    row = db.scalar(
        select(ProviderCredential.monthly_cap_usd).where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    return float(row) if row is not None else float(settings.LIVE_CAP_DEFAULT_USD)


def enforce_workspace_cap(db: Session, vk: VirtualKey, provider: str) -> None:
    """A workspace's live spend on a provider can't exceed that provider's monthly cap."""
    if not vk.allow_live:
        return
    cap = provider_cap(db, vk.workspace_id, provider)
    spent = live_spend_this_month(db, vk.workspace_id, provider)
    if spent >= cap:
        raise HTTPException(
            status_code=402,
            detail={
                "message": (
                    f"{provider} live-spend cap of ${cap:.2f}/month reached "
                    f"(spent ${spent:.4f}) — raise it on the Workspace page"
                ),
                "type": "live_cap_exceeded",
                "code": "402",
            },
        )


def run_completion(
    db: Session, vk: VirtualKey, provider: str, model: str, prompt: str
) -> CompletionResult:
    key = live_key_for(db, vk, provider)
    go_live = settings.ENABLE_LIVE and vk.allow_live and key is not None
    actual_cost: float | None = None
    if go_live:
        try:
            t0 = time.perf_counter()
            text, pt, ct, actual_cost = providers.call_provider(
                provider, model, prompt, api_key=key
            )
            latency_ms = int((time.perf_counter() - t0) * 1000)
            mode = "live"
        except Exception as exc:  # fall back to simulated rather than 5xx
            text, pt, ct, latency_ms = simulate_chat(provider, model, prompt)
            text = f"[live call failed, simulated instead: {exc}] {text}"
            mode, actual_cost = "simulated", None
    else:
        text, pt, ct, latency_ms = simulate_chat(provider, model, prompt)
        mode = "simulated"

    if actual_cost is not None:
        cost, cost_source = round(actual_cost, 6), "provider"
    else:
        cost, cost_source = estimate_cost(provider, model, pt, ct), "configured"

    return CompletionResult(text, pt, ct, cost, cost_source, mode, latency_ms)


def _preview(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def record_usage(
    db: Session,
    vk: VirtualKey,
    provider: str,
    model: str,
    prompt: str,
    result: CompletionResult,
    *,
    request_id: str | None = None,
    status: str = "success",
) -> UsageLog:
    row = UsageLog(
        key_id=vk.id,
        request_id=request_id or str(uuid4()),
        provider=provider,
        model=model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        cost=result.cost,
        cost_source=result.cost_source,
        simulated=result.simulated,
        mode=result.mode,
        latency_ms=result.latency_ms,
        status=status,
        prompt_preview=_preview(prompt) if settings.LOG_BODIES else None,
        response_preview=_preview(result.text) if settings.LOG_BODIES else None,
    )
    db.add(row)
    vk.last_used_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


def record_usage_detached(
    vk_id: int,
    provider: str,
    model: str,
    prompt: str,
    result: CompletionResult,
    *,
    request_id: str | None = None,
    status: str = "success",
) -> None:
    """Same as record_usage but opens its own session — for the streaming path,
    which finishes writing after the request's session has been torn down."""
    with SessionLocal() as db:
        vk = db.get(VirtualKey, vk_id)
        if vk is None:
            return
        record_usage(
            db, vk, provider, model, prompt, result,
            request_id=request_id, status=status,
        )
