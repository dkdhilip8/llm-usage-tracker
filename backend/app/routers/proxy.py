from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.db import get_db
from app.gateway import (
    authorize_provider,
    enforce_account_cap,
    enforce_budget,
    enforce_user_quota,
    record_usage,
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
def inspect(vk: VirtualKey = Depends(require_virtual_key)) -> KeyInspect:
    """Authed by the virtual key itself (no admin token). Lets the Playground show
    only this key's allowed providers and what each would actually do."""
    rows = []
    for provider in vk.provider_names():
        configured = providers.is_configured(provider)
        valid = providers.check_liveness(provider) if configured else False
        would_be_live = (
            settings.ENABLE_LIVE and vk.allow_live and configured and valid
        )
        rows.append(
            {
                "provider": provider,
                "configured": configured,
                "valid": valid,
                "mode": "live" if would_be_live else "simulated",
            }
        )
    return KeyInspect(label=vk.label, allow_live=vk.allow_live, providers=rows)


@router.post("/chat", response_model=ChatResponse)
def proxy_chat(
    body: ChatRequest,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
) -> ChatResponse:
    authorize_provider(vk, body.provider)
    prompt = _extract_prompt(body)
    if not prompt:
        raise HTTPException(422, "prompt or messages is required")
    enforce_user_quota(db, vk)
    enforce_budget(db, vk)
    enforce_account_cap(db, vk)

    result = run_completion(db, vk, body.provider, body.model, prompt)
    row = record_usage(db, vk, body.provider, body.model, prompt, result)

    return ChatResponse(
        request_id=row.request_id,
        provider=body.provider,
        model=body.model,
        mode=result.mode,
        simulated=result.simulated,
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
