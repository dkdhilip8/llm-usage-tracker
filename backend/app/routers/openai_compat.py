"""OpenAI-compatible surface: `POST /v1/chat/completions` (JSON or SSE stream).

Lets any OpenAI SDK point at this gateway:

    from openai import OpenAI
    client = OpenAI(base_url="https://<host>/v1", api_key="vk_...")
    client.chat.completions.create(
        model="openrouter/meta-llama/llama-3.3-70b-instruct",
        messages=[{"role": "user", "content": "hi"}],
    )

Every request hits a real provider — there is no simulator. A key with no
configured provider (402), a paused key (403), or an upstream failure (502)
returns an error.
"""

import json
import time
from collections.abc import Iterator
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app import providers
from app.db import get_db
from app.gateway import (
    CompletionResult,
    enforce_budget,
    enforce_workspace_cap,
    enforce_workspace_quota,
    record_usage,
    record_usage_detached,
    require_live_ready,
    resolve_target,
    run_completion,
)
from app.models import VirtualKey
from app.pricing import estimate_cost
from app.schemas import OpenAIChatRequest
from app.security import require_virtual_key

router = APIRouter(prefix="/v1", tags=["openai-compatible"])

_SSE_PROVIDERS = ("openai", "openrouter", "gemini")


def _prompt_from_messages(messages: list[dict]) -> str:
    parts: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
    return "\n".join(p for p in parts if p).strip()


def _chunk(cid: str, created: int, model: str, delta: dict, finish: str | None = None) -> str:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj)}\n\n"


@router.post("/chat/completions")
def chat_completions(
    body: OpenAIChatRequest,
    vk: VirtualKey = Depends(require_virtual_key),
    db: Session = Depends(get_db),
):
    provider, model = resolve_target(vk, body.model)
    prompt = _prompt_from_messages(body.messages)
    if not prompt:
        raise HTTPException(422, "messages must contain user text content")
    enforce_workspace_quota(db, vk)
    enforce_budget(db, vk)
    enforce_workspace_cap(db, vk, provider)
    live_key = require_live_ready(db, vk, provider)  # 403 paused / 402 no key

    cid = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())

    if not body.stream:
        result = run_completion(db, vk, provider, model, prompt)
        record_usage(db, vk, provider, model, prompt, result, request_id=cid)
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            },
            "x_gateway": {
                "cost": result.cost,
                "cost_source": result.cost_source,
                "latency_ms": result.latency_ms,
            },
        }

    vk_id = vk.id

    def event_stream() -> Iterator[str]:
        t0 = time.perf_counter()
        acc: list[str] = []
        pt = ct = 0
        cost: float | None = None
        cost_source = "configured"
        status = "success"

        try:
            if provider in _SSE_PROVIDERS:
                first = True
                for kind, payload in providers.stream_openai_compatible(
                    provider, model, prompt, api_key=live_key
                ):
                    if kind == "delta":
                        text = str(payload)
                        acc.append(text)
                        delta = (
                            {"role": "assistant", "content": text}
                            if first
                            else {"content": text}
                        )
                        first = False
                        yield _chunk(cid, created, body.model, delta)
                    else:  # done
                        info = payload if isinstance(payload, dict) else {}
                        pt = int(info.get("prompt_tokens", 0) or 0)
                        ct = int(info.get("completion_tokens", 0) or 0)
                        live_cost = info.get("cost")
                        if live_cost is not None:
                            cost, cost_source = round(float(live_cost), 6), "provider"
                        else:
                            cost, cost_source = (
                                estimate_cost(provider, model, pt, ct),
                                "configured",
                            )
            else:  # anthropic — no SSE passthrough; single-shot, then chunk it out
                text, pt, ct, actual = providers.call_provider(
                    provider, model, prompt, api_key=live_key
                )
                if actual is not None:
                    cost, cost_source = round(float(actual), 6), "provider"
                else:
                    cost, cost_source = estimate_cost(provider, model, pt, ct), "configured"
                for i, word in enumerate(text.split(" ")):
                    token = word if i == 0 else " " + word
                    acc.append(token)
                    delta = (
                        {"role": "assistant", "content": token}
                        if i == 0
                        else {"content": token}
                    )
                    yield _chunk(cid, created, body.model, delta)
        except Exception as exc:  # noqa: BLE001
            status = "error"
            note = f"[{provider} call failed: {exc}]"
            acc.append(note)
            yield _chunk(cid, created, body.model, {"role": "assistant", "content": note})

        latency_ms = int((time.perf_counter() - t0) * 1000)
        final = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": body.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": pt + ct,
            },
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

        result = CompletionResult(
            "".join(acc), pt, ct, cost or 0.0, cost_source, latency_ms
        )
        record_usage_detached(
            vk_id, provider, model, prompt, result, request_id=cid, status=status
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
