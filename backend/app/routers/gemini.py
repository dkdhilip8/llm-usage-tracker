"""Gemini-native passthrough: `POST /v1beta/models/{model}:generateContent`,
`:streamGenerateContent`, `:embedContent`, and `:batchEmbedContents`.

Point the Google GenAI SDK's base_url at this gateway and it works
unmodified — the SDK's own default path shape is
`{base_url}/v1beta/models/{model}:generateContent`:

    from google import genai
    client = genai.Client(
        api_key="vk_...",
        http_options={"base_url": "https://<host>"},
    )
    client.models.generate_content(model="gemini-2.0-flash", contents="hi")

Every request hits the real Gemini API — the client's JSON body is forwarded
almost verbatim (only `model`, taken from the URL path per Gemini's own REST
shape, is validated by the gateway). `contents`/`parts` (incl. multimodal
`inline_data`/`file_data`), `systemInstruction`, `generationConfig`, `tools`
(function declarations, code execution, Google Search grounding), and
`toolConfig` all ride through untouched — the gateway never executes a tool,
it only proxies the call and the model's tool-call request.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.adapters import gemini_adapter
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

router = APIRouter(prefix="/v1beta", tags=["gemini-native"])

_PROVIDER = "gemini"


def _authorize(db: Session, vk: VirtualKey, model: str) -> str:
    """Shared governance preamble for every native Gemini endpoint below.
    Returns the live API key, or raises the appropriate HTTPException."""
    if not model:
        raise HTTPException(422, "model is required")
    authorize_provider(vk, _PROVIDER)
    authorize_model(vk, _PROVIDER, model)
    enforce_workspace_quota(db, vk)
    enforce_budget(db, vk)
    enforce_workspace_cap(db, vk, _PROVIDER)
    return require_live_ready(db, vk, _PROVIDER)  # 403 paused / 402 no key


@router.post("/models/{model}:generateContent")
def generate_content(
    model: str,
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    api_key = _authorize(db, vk, model)

    response = run_native_completion(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=lambda b, k: gemini_adapter.call(model, b, k),
        extract_usage_fn=gemini_adapter.extract_usage,
        text_preview_fn=gemini_adapter.response_text_preview,
        prompt_preview=gemini_adapter.prompt_preview(body),
        api_key=api_key,
        body=body,
    )
    return JSONResponse(response)


@router.post("/models/{model}:streamGenerateContent")
def stream_generate_content(
    model: str,
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    api_key = _authorize(db, vk, model)

    return StreamingResponse(
        stream_native_passthrough(
            vk.id,
            _PROVIDER,
            model,
            gemini_adapter.prompt_preview(body),
            stream_fn=lambda b, k: gemini_adapter.stream(model, b, k),
            accumulator_cls=gemini_adapter.GeminiStreamUsageAccumulator,
            api_key=api_key,
            body=body,
        ),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post("/models/{model}:embedContent")
def embed_content(
    model: str,
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    api_key = _authorize(db, vk, model)

    response = run_native_completion(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=lambda b, k: gemini_adapter.embed_content(model, b, k),
        extract_usage_fn=gemini_adapter.extract_embed_usage,
        text_preview_fn=gemini_adapter.embed_preview,
        prompt_preview=gemini_adapter.embed_prompt_preview(body),
        api_key=api_key,
        body=body,
    )
    return JSONResponse(response)


@router.post("/models/{model}:batchEmbedContents")
def batch_embed_contents(
    model: str,
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    api_key = _authorize(db, vk, model)

    response = run_native_completion(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=lambda b, k: gemini_adapter.batch_embed_contents(model, b, k),
        extract_usage_fn=gemini_adapter.extract_embed_usage,
        text_preview_fn=gemini_adapter.embed_preview,
        prompt_preview=gemini_adapter.batch_embed_prompt_preview(body),
        api_key=api_key,
        body=body,
    )
    return JSONResponse(response)
