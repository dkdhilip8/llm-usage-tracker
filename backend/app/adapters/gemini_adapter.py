"""Native Gemini `generateContent` / `streamGenerateContent` adapter. The
gateway validates only `model` (taken from the URL path, matching Google's
own REST shape); everything else in the client's JSON body — `contents`
(with `inline_data`/`file_data` parts for multimodal input), `systemInstruction`,
`generationConfig`, `tools` (function declarations, code execution, Google
Search grounding), `toolConfig`, `safetySettings`, `cachedContent` — is
forwarded to Gemini untouched.

Deliberately NOT covered here: the Interactions API's stateful
(`previous_interaction_id`) and async (`background=true`) modes — see the
plan's "Documented limitations" for why those need their own governance
design before being wired in. `generateContent` is Google's own
recommendation for stable, simple integrations and is what this adapter
targets."""

import json

from app import providers
from app.adapters.base import UsageInfo

_BASE = "https://generativelanguage.googleapis.com/v1beta"

_USAGE_EXTRA_FIELDS = (
    "cachedContentTokenCount",
    "thoughtsTokenCount",
    "toolUsePromptTokenCount",
)


def _headers(api_key: str) -> dict:
    return {"x-goog-api-key": api_key, "content-type": "application/json"}


def _strip(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "stream"}


def call(model: str, body: dict, api_key: str) -> dict:
    url = f"{_BASE}/models/{model}:generateContent"
    return providers.send_request(url, _headers(api_key), _strip(body))


def stream(model: str, body: dict, api_key: str):
    url = f"{_BASE}/models/{model}:streamGenerateContent?alt=sse"
    yield from providers.stream_request(url, _headers(api_key), _strip(body))


def extract_usage(response: dict) -> UsageInfo:
    usage = response.get("usageMetadata") or {}
    raw = {k: usage[k] for k in _USAGE_EXTRA_FIELDS if k in usage}
    return UsageInfo(
        prompt_tokens=int(usage.get("promptTokenCount", 0)),
        completion_tokens=int(usage.get("candidatesTokenCount", 0)),
        cost=None,  # generateContent does not report a per-request charge
        raw=raw,
    )


def response_text_preview(response: dict) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p)


class GeminiStreamUsageAccumulator:
    """Each streamGenerateContent SSE chunk is a full GenerateContentResponse
    JSON; usageMetadata in each chunk is cumulative-to-date, so the last chunk
    that carries it holds the final totals. Feed every line; read `.usage()`
    once the stream ends."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.raw: dict = {}

    def feed(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        try:
            chunk = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return
        usage = chunk.get("usageMetadata") or {}
        if "promptTokenCount" in usage:
            self.prompt_tokens = int(usage["promptTokenCount"])
        if "candidatesTokenCount" in usage:
            self.completion_tokens = int(usage["candidatesTokenCount"])
        for k in _USAGE_EXTRA_FIELDS:
            if k in usage:
                self.raw[k] = usage[k]

    def usage(self) -> UsageInfo:
        return UsageInfo(self.prompt_tokens, self.completion_tokens, cost=None, raw=self.raw)


def prompt_preview(body: dict) -> str:
    """Human-readable stand-in for usage_logs.prompt_preview (only stored when
    LOG_BODIES=true) — never sent upstream, purely for the Requests log."""
    contents = body.get("contents") or []
    if not contents:
        return ""
    parts = contents[-1].get("parts") if isinstance(contents[-1], dict) else None
    if not isinstance(parts, list):
        return ""
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and "text" in p]
    return " ".join(t for t in texts if t)
