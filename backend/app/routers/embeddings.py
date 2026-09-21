"""OpenAI-native passthrough: `POST /v1/embeddings`.

Point the real OpenAI SDK's base_url at the gateway and it works unmodified:

    from openai import OpenAI
    client = OpenAI(base_url="https://<host>/v1", api_key="vk_...")
    client.embeddings.create(model="text-embedding-3-small", input="hello")

Every request hits the real OpenAI API — the client's JSON body is forwarded
almost verbatim (only `model` is validated by the gateway). No streaming —
the Embeddings API doesn't support it.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
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
)
from app.models import VirtualKey
from app.security import require_virtual_key

router = APIRouter(prefix="/v1", tags=["openai-native"])

_PROVIDER = "openai"


@router.post("/embeddings")
def create_embeddings(
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

    response = run_native_completion(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=openai_adapter.call_embeddings,
        extract_usage_fn=openai_adapter.extract_embeddings_usage,
        text_preview_fn=openai_adapter.embeddings_preview,
        prompt_preview=openai_adapter.prompt_preview(body),
        api_key=api_key,
        body=body,
    )
    return JSONResponse(response)
