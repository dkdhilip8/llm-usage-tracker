"""Anthropic-native passthrough: `POST /v1/messages`.

Point the real Anthropic SDK's base_url at this gateway and it works
unmodified (the SDK's own default path is `{base_url}/v1/messages`):

    from anthropic import Anthropic
    client = Anthropic(base_url="https://<host>", api_key="vk_...")
    client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hi"}],
    )

Every request hits the real Anthropic API — the client's JSON body is
forwarded almost verbatim (only `model` is validated by the gateway;
Anthropic validates the rest, including that `max_tokens` is present, and its
own error responses pass through unchanged). No simulator, no field-by-field
reconstruction — system prompts, multi-turn content blocks (text/image/
document/tool_use/tool_result), cache_control, tools/tool_choice all ride
through untouched.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.adapters import anthropic_adapter
from app.db import get_db
from app.gateway import (
    authorize_model,
    authorize_provider,
    enforce_budget,
    enforce_workspace_cap,
    enforce_workspace_quota,
    require_live_ready,
    run_native_completion,
    stream_native_passthrough,
)
from app.models import VirtualKey
from app.security import require_virtual_key

router = APIRouter(prefix="/v1", tags=["anthropic-native"])

_PROVIDER = "anthropic"


@router.post("/messages")
def messages(
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise HTTPException(422, "model is required")
    authorize_provider(vk, _PROVIDER)
    authorize_model(vk, _PROVIDER, model)
    enforce_workspace_quota(db, vk)
    enforce_budget(db, vk)
    enforce_workspace_cap(db, vk, _PROVIDER)
    api_key = require_live_ready(db, vk, _PROVIDER)  # 403 paused / 402 no key

    if not body.get("stream"):
        response = run_native_completion(
            db,
            vk,
            _PROVIDER,
            model,
            call_fn=anthropic_adapter.call,
            extract_usage_fn=anthropic_adapter.extract_usage,
            text_preview_fn=anthropic_adapter.response_text_preview,
            prompt_preview=anthropic_adapter.prompt_preview(body),
            api_key=api_key,
            body=body,
        )
        return JSONResponse(response)

    return StreamingResponse(
        stream_native_passthrough(
            vk.id,
            _PROVIDER,
            model,
            anthropic_adapter.prompt_preview(body),
            stream_fn=anthropic_adapter.stream,
            accumulator_cls=anthropic_adapter.AnthropicStreamUsageAccumulator,
            api_key=api_key,
            body=body,
        ),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
