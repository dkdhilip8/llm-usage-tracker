"""OpenAI-native passthrough: `POST /v1/audio/transcriptions` and
`POST /v1/audio/speech`.

    from openai import OpenAI
    client = OpenAI(base_url="https://<host>/v1", api_key="vk_...")
    client.audio.transcriptions.create(model="whisper-1", file=open("a.mp3", "rb"))
    client.audio.speech.create(model="tts-1", voice="alloy", input="hi")

Transcription forwards `file` plus the common form fields (`model`,
`language`, `prompt`, `response_format`, `temperature`) as multipart/
form-data, exactly as sent, to OpenAI's real endpoint. Kept as a sync route
like every other endpoint in this codebase (no async route exists anywhere
here) — a handful of rarer fields (`timestamp_granularities`,
`chunking_strategy`, `keywords`, speaker diarization) aren't forwarded this
pass, since reading a fully generic multipart form needs `async def` +
`await request.form()`, and this route also makes blocking DB/HTTP calls
that would then stall the event loop. Documented, not silently dropped.
Translations (audio -> English text) — same request shape, not added yet.

Speech (TTS) forwards its JSON body almost verbatim; the response is the
provider's raw audio bytes, untouched — never JSON, so returned as a raw
`Response`, not `JSONResponse`.

Billing: transcription's unit depends on the model — token usage for
gpt-4o(-mini)-transcribe, duration in seconds for whisper-1 (see
`openai_adapter.extract_transcription_usage`, verified against OpenAI's own
API reference, not assumed). TTS bills by input character count — its
response carries no usage at all, so it's counted from the request instead
(`openai_adapter.extract_speech_usage`). Neither is forced through
prompt_tokens/completion_tokens; see `CompletionResult.duration_seconds` /
`.characters` and the matching `usage_logs` columns (migration v9).
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
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
    run_native_completion_raw,
)
from app.models import VirtualKey
from app.security import require_virtual_key

router = APIRouter(prefix="/v1", tags=["openai-native"])

_PROVIDER = "openai"


def _authorize(db: Session, vk: VirtualKey, model: str) -> str:
    authorize_provider(vk, _PROVIDER)
    authorize_model(vk, _PROVIDER, model)
    enforce_workspace_quota(db, vk)
    enforce_budget(db, vk)
    enforce_workspace_cap(db, vk, _PROVIDER)
    return require_live_ready(db, vk, _PROVIDER)  # 403 paused / 402 no key


@router.post("/audio/transcriptions")
def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str | None = Form(None),
    temperature: float | None = Form(None),
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    if not model:
        raise HTTPException(422, "model is required")
    api_key = _authorize(db, vk, model)

    file_bytes = file.file.read()  # sync read — this is a sync route
    filename = file.filename or "audio"
    file_content_type = file.content_type or "application/octet-stream"
    form_fields = {
        "model": model,
        "language": language,
        "prompt": prompt,
        "response_format": response_format,
        "temperature": temperature,
    }

    content, content_type = run_native_completion_raw(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=lambda k: openai_adapter.call_transcription(
            file_bytes, filename, file_content_type, form_fields, k
        ),
        extract_usage_fn=openai_adapter.extract_transcription_usage,
        preview_fn=openai_adapter.transcription_preview,
        prompt_preview=openai_adapter.transcription_request_preview(form_fields, filename),
        api_key=api_key,
    )
    return Response(content=content, media_type=content_type)


@router.post("/audio/speech")
def create_speech(
    body: dict,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    model = body.get("model")
    if not isinstance(model, str) or not model:
        raise HTTPException(422, "model is required")
    api_key = _authorize(db, vk, model)

    content, content_type = run_native_completion_raw(
        db,
        vk,
        _PROVIDER,
        model,
        call_fn=lambda k: openai_adapter.call_speech(body, k),
        extract_usage_fn=lambda _content, _content_type: openai_adapter.extract_speech_usage(body),
        prompt_preview=openai_adapter.prompt_preview(body),
        api_key=api_key,
    )
    return Response(content=content, media_type=content_type)
