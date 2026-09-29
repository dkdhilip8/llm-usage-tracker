from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import idempotency
from app.db import get_db
from app.gateway import (
    authorize_model,
    authorize_provider,
    enforce_budget,
    enforce_workspace_cap,
    enforce_workspace_quota,
    live_key_for,
    record_usage,
    require_live_ready,
    run_completion,
)
from app.models import VirtualKey
from app.schemas import ChatRequest, ChatResponse, KeyInspect, Usage
from app.security import require_virtual_key

router = APIRouter(prefix="/v1/proxy", tags=["proxy"])


def _extract_prompt(body: ChatRequest) -> str:
    if body.prompt:
        return body.prompt
    if body.messages:
        return "\n".join(str(m.get("content", "")) for m in body.messages)
    return ""


@router.get("/inspect", response_model=KeyInspect)
def inspect(
    vk: VirtualKey = Depends(require_virtual_key), db: Session = Depends(get_db)
) -> KeyInspect:
    """Authed by the virtual key itself. Lets the Playground show this key's
    allowed providers and whether each has a real API key behind it."""
    rows = [
        {"provider": p, "ready": live_key_for(db, vk, p) is not None}
        for p in vk.provider_names()
    ]
    return KeyInspect(label=vk.label, allow_live=vk.allow_live, providers=rows)


@router.post("/chat", response_model=ChatResponse)
def proxy_chat(
    body: ChatRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
) -> ChatResponse:
    authorize_provider(vk, body.provider)
    authorize_model(vk, body.provider, body.model)
    prompt = _extract_prompt(body)
    if not prompt:
        raise HTTPException(422, "prompt or messages is required")
    idem = idempotency.begin(
        db, vk, header=idempotency_key,
        endpoint="/v1/proxy/chat", provider=body.provider, model=body.model,
        body_material=body.model_dump(),
    )
    enforce_workspace_quota(db, vk)
    enforce_budget(db, vk)
    enforce_workspace_cap(db, vk, body.provider)
    require_live_ready(db, vk, body.provider)  # 403 paused / 402 no key / 503 breaker — BEFORE the claim

    # run_completion owns the provider call + error recording but not the final
    # response shape, so claim here and finalize after the response is built.
    idempotency.claim(idem)
    try:
        result = run_completion(db, vk, body.provider, body.model, prompt)
    except HTTPException as exc:
        idempotency.finalize_or_release_http_error(idem, exc)
        raise
    row = record_usage(db, vk, body.provider, body.model, prompt, result)

    resp = ChatResponse(
        request_id=row.request_id,
        provider=body.provider,
        model=body.model,
        response=result.text,
        usage=Usage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
        ),
        cost=result.cost,
        cost_source=result.cost_source,
        pricing=result.pricing_block(body.provider, body.model),
        latency_ms=result.latency_ms,
    )
    idempotency.finalize_json(idem, resp.model_dump(), usage_log_id=row.id)
    return resp
