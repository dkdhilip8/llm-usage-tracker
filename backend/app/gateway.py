"""Shared request lifecycle for the proxy endpoints.

`routers/proxy.py` (friendly shape, used by the Playground) and
`routers/openai_compat.py` (OpenAI `/v1/chat/completions` shape) both build on these:
target resolution + provider ACL, budget + workspace-cap enforcement, the real
upstream call, and the usage-log write. There is no simulator — a request either
hits a real provider or returns an error."""

import json
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import providers
from app.adapters.base import UsageInfo
from app.config import settings
from app.db import SessionLocal
from app.models import ProviderCredential, UsageLog, VirtualKey
from app.pricing import PROVIDERS, estimate_cost, price_for

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
    cost: float | None  # None => cost_source "unknown" — never a fabricated number
    cost_source: str  # "provider" (real charge) | "configured" (price table) | "unknown"
    latency_ms: int
    usage_raw: dict | None = None  # provider-native extra usage detail, if any

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def pricing_block(self, provider: str, model: str) -> dict:
        p = price_for(provider, model)
        return {
            "input_per_1m": p["input"] if p else None,
            "output_per_1m": p["output"] if p else None,
            "source": (
                provider
                if self.cost_source == "provider"
                else ("configured" if p else "unknown")
            ),
        }


def authorize_provider(vk: VirtualKey, provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(422, f"provider must be one of {PROVIDERS}")
    if provider not in vk.provider_names():
        raise HTTPException(
            403, f"this key is not permitted to use provider '{provider}'"
        )


def authorize_model(vk: VirtualKey, provider: str, model: str) -> None:
    """Per-model allow-list, if the key has one for this provider. A key with
    zero allowed_models rows for (provider) permits every model of it — the
    default, unchanged from every key created before this existed."""
    allowed = vk.models_for(provider)
    if allowed and model not in allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "message": f"this key is not permitted to use model '{model}' on {provider}",
                "type": "model_not_allowed",
                "code": "403",
            },
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
    authorize_model(vk, provider, model)
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
    a server env var, or one the workspace has attached. Used to refuse `allow_live`
    on a key whose providers have nothing behind them (it would only ever 402)."""
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


def require_live_ready(db: Session, vk: VirtualKey, provider: str) -> str:
    """The API key a live call will use, or an HTTPException explaining why it can't."""
    if not vk.allow_live:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "this key is paused — turn live calls on for it on the Workspace page",
                "type": "key_paused",
                "code": "403",
            },
        )
    key = live_key_for(db, vk, provider)
    if key is None:
        raise HTTPException(
            status_code=402,
            detail={
                "message": (
                    f"no {provider} API key is configured for this workspace — "
                    "add one on the Workspace page"
                ),
                "type": "provider_not_configured",
                "code": "402",
            },
        )
    return key


def _cost_and_source(actual_cost: float | None, provider: str, model: str, pt: int, ct: int) -> tuple[float | None, str]:
    if actual_cost is not None:
        return round(actual_cost, 6), "provider"
    est = estimate_cost(provider, model, pt, ct)
    return (est, "configured") if est is not None else (None, "unknown")


def completion_result_from_usage(
    usage: UsageInfo, provider: str, model: str, latency_ms: int, *, text: str = ""
) -> CompletionResult:
    """Turn an adapter's UsageInfo (pulled from a real provider response) into
    the CompletionResult record_usage expects, applying the same
    provider-cost-wins-else-honest-estimate rule as the legacy call_provider path."""
    cost, cost_source = _cost_and_source(usage.cost, provider, model, usage.prompt_tokens, usage.completion_tokens)
    return CompletionResult(
        text=text,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost=cost,
        cost_source=cost_source,
        latency_ms=latency_ms,
        usage_raw=usage.raw or None,
    )


def run_completion(
    db: Session, vk: VirtualKey, provider: str, model: str, prompt: str
) -> CompletionResult:
    key = require_live_ready(db, vk, provider)
    t0 = time.perf_counter()
    try:
        text, pt, ct, actual_cost = providers.call_provider(
            provider, model, prompt, api_key=key
        )
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        failed = CompletionResult(
            f"[{provider} call failed: {exc}]", 0, 0, 0.0, "configured", latency_ms
        )
        record_usage(db, vk, provider, model, prompt, failed, status="error")
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"{provider} call failed: {exc}",
                "type": "upstream_error",
                "code": "502",
            },
        ) from exc
    latency_ms = int((time.perf_counter() - t0) * 1000)
    cost, cost_source = _cost_and_source(actual_cost, provider, model, pt, ct)

    return CompletionResult(text, pt, ct, cost, cost_source, latency_ms)


def run_native_completion(
    db: Session,
    vk: VirtualKey,
    provider: str,
    model: str,
    *,
    call_fn: Callable[[dict, str], dict],
    extract_usage_fn: Callable[[dict], UsageInfo],
    text_preview_fn: Callable[[dict], str],
    prompt_preview: str,
    api_key: str,
    body: dict,
) -> dict:
    """For the provider-native passthrough endpoints (/v1/messages,
    /v1/responses, and the full-fidelity branch of /v1/chat/completions):
    calls `call_fn(body, api_key)`, records usage exactly like run_completion
    does (including on failure), and returns the RAW provider response —
    unlike run_completion, the caller returns this untouched to the client."""
    t0 = time.perf_counter()
    try:
        response = call_fn(body, api_key)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        failed = CompletionResult(
            f"[{provider} call failed: {exc}]", 0, 0, 0.0, "configured", latency_ms
        )
        record_usage(db, vk, provider, model, prompt_preview, failed, status="error")
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"{provider} call failed: {exc}",
                "type": "upstream_error",
                "code": "502",
            },
        ) from exc
    latency_ms = int((time.perf_counter() - t0) * 1000)
    usage = extract_usage_fn(response)
    result = completion_result_from_usage(
        usage, provider, model, latency_ms, text=text_preview_fn(response)
    )
    record_usage(db, vk, provider, model, prompt_preview, result, request_id=response.get("id"))
    return response


def stream_native_passthrough(
    vk_id: int,
    provider: str,
    model: str,
    prompt_preview: str,
    *,
    stream_fn: Callable[[dict, str], Iterator[str]],
    accumulator_cls: type,
    api_key: str,
    body: dict,
) -> Iterator[str]:
    """For the provider-native streaming endpoints: re-emits every line from
    `stream_fn` verbatim (byte-for-byte SSE passthrough, no buffering) while an
    accumulator extracts usage as it goes; records usage once the stream ends
    (success or mid-stream failure) via record_usage_detached, exactly like the
    legacy streaming path does."""
    t0 = time.perf_counter()
    status = "success"
    acc = accumulator_cls()
    try:
        for line in stream_fn(body, api_key):
            acc.feed(line)
            yield (line + "\n") if line else "\n"
    except Exception as exc:  # noqa: BLE001 — headers are already sent; surface in-band
        status = "error"
        note = json.dumps({"type": "error", "error": {"message": f"{provider} call failed: {exc}"}})
        yield f"event: error\ndata: {note}\n\n"
    latency_ms = int((time.perf_counter() - t0) * 1000)
    result = completion_result_from_usage(acc.usage(), provider, model, latency_ms)
    record_usage_detached(vk_id, provider, model, prompt_preview, result, status=status)


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
        usage_raw=result.usage_raw,
        simulated=False,
        mode="live",
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
