"""OpenAI Responses API passthrough: `POST /v1/responses`.

    from openai import OpenAI
    client = OpenAI(base_url="https://<host>/v1", api_key="vk_...")
    client.responses.create(model="gpt-4o", input="hi", tools=[...])

Every request hits the real OpenAI Responses API — the client's JSON body is
forwarded almost verbatim (only `model` is validated by the gateway). Tools
(including built-ins), structured outputs (`text.format`), reasoning config,
and multi-turn `input` items all ride through untouched.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.adapters import openai_adapter
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

router = APIRouter(prefix="/v1", tags=["openai-native"])

_PROVIDER = "openai"


@router.post("/responses")
def responses(
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
            call_fn=openai_adapter.call_responses,
            extract_usage_fn=openai_adapter.extract_responses_usage,
            text_preview_fn=openai_adapter.responses_text_preview,
            prompt_preview=openai_adapter.prompt_preview(body),
            api_key=api_key,
            body=body,
        )
        return JSONResponse(response)

    return StreamingResponse(
        stream_native_passthrough(
            vk.id,
            _PROVIDER,
            model,
            openai_adapter.prompt_preview(body),
            stream_fn=openai_adapter.stream_responses,
            accumulator_cls=openai_adapter.ResponsesStreamUsageAccumulator,
            api_key=api_key,
            body=body,
        ),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
