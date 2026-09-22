"""Native OpenAI-shaped adapter — Chat Completions (shared wire format with
OpenRouter) and the Responses API. The gateway validates only `model`; the
rest of the client's JSON body (messages/input, tools, tool_choice,
response_format/text.format, reasoning, multimodal content parts, ...) is
forwarded to the provider untouched."""

import json

from app import providers
from app.adapters.base import UsageInfo

_CHAT_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
_RESPONSES_URL = "https://api.openai.com/v1/responses"
_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"


def _chat_headers(provider: str, api_key: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/dkdhilip8/llm-usage-tracker"
        headers["X-Title"] = "LLM Usage Tracker"
    return headers


def _bearer_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}


def _prep_chat_body(provider: str, body: dict) -> dict:
    payload = {k: v for k, v in body.items() if k != "stream"}
    if provider == "openrouter":
        payload.setdefault("usage", {"include": True})  # ask OpenRouter for the real charge
    return payload


# ---- Chat Completions (openai, openrouter) ----
def call_chat(provider: str, body: dict, api_key: str) -> dict:
    return providers.send_request(
        _CHAT_URLS[provider], _chat_headers(provider, api_key), _prep_chat_body(provider, body)
    )


def stream_chat(provider: str, body: dict, api_key: str):
    payload = {**_prep_chat_body(provider, body), "stream": True}
    yield from providers.stream_request(_CHAT_URLS[provider], _chat_headers(provider, api_key), payload)


def extract_chat_usage(response: dict) -> UsageInfo:
    usage = response.get("usage") or {}
    raw = {
        k: usage[k] for k in ("prompt_tokens_details", "completion_tokens_details") if k in usage
    }
    cost = usage.get("cost")  # present for OpenRouter, absent for OpenAI
    return UsageInfo(
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        cost=float(cost) if cost is not None else None,
        raw=raw,
    )


def chat_text_preview(response: dict) -> str:
    try:
        return response["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


class ChatStreamUsageAccumulator:
    """The Chat Completions stream carries one usage object on its final chunk
    (present when the request sets stream_options.include_usage /
    usage.include, which _prep_chat_body + stream_chat always do)."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost: float | None = None
        self.raw: dict = {}

    def feed(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            return
        usage = chunk.get("usage")
        if usage:
            self.prompt_tokens = int(usage.get("prompt_tokens", self.prompt_tokens))
            self.completion_tokens = int(usage.get("completion_tokens", self.completion_tokens))
            if usage.get("cost") is not None:
                self.cost = float(usage["cost"])

    def usage(self) -> UsageInfo:
        return UsageInfo(self.prompt_tokens, self.completion_tokens, cost=self.cost, raw=self.raw)


# ---- Responses API (openai only) ----
def call_responses(body: dict, api_key: str) -> dict:
    payload = {k: v for k, v in body.items() if k != "stream"}
    return providers.send_request(_RESPONSES_URL, _bearer_headers(api_key), payload)


def stream_responses(body: dict, api_key: str):
    payload = {**body, "stream": True}
    yield from providers.stream_request(_RESPONSES_URL, _bearer_headers(api_key), payload)


def extract_responses_usage(response: dict) -> UsageInfo:
    usage = response.get("usage") or {}
    raw = {k: usage[k] for k in ("input_tokens_details", "output_tokens_details") if k in usage}
    return UsageInfo(
        prompt_tokens=int(usage.get("input_tokens", 0)),
        completion_tokens=int(usage.get("output_tokens", 0)),
        cost=None,  # the Responses API does not report a per-request charge
        raw=raw,
    )


def responses_text_preview(response: dict) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    return ""


class ResponsesStreamUsageAccumulator:
    """The Responses API streams named events; `response.completed` (and other
    response.* events) carry the running `response.usage` object."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.raw: dict = {}

    def feed(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            return
        try:
            evt = json.loads(payload)
        except json.JSONDecodeError:
            return
        resp = evt.get("response")
        usage = resp.get("usage") if isinstance(resp, dict) else None
        if usage:
            self.prompt_tokens = int(usage.get("input_tokens", self.prompt_tokens))
            self.completion_tokens = int(usage.get("output_tokens", self.completion_tokens))

    def usage(self) -> UsageInfo:
        return UsageInfo(self.prompt_tokens, self.completion_tokens, cost=None, raw=self.raw)


# ---- Embeddings (openai only — no streaming) ----
def call_embeddings(body: dict, api_key: str) -> dict:
    return providers.send_request(_EMBEDDINGS_URL, _bearer_headers(api_key), body)


def extract_embeddings_usage(response: dict) -> UsageInfo:
    usage = response.get("usage") or {}
    return UsageInfo(
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=0,  # embeddings bill input tokens only
        cost=None,
        raw={},
    )


def embeddings_preview(response: dict) -> str:
    n = len(response.get("data") or [])
    return f"{n} embedding(s)"


# ---- Images: generation only (openai only — no streaming) ----
# Edits/variations use multipart/form-data (file upload), a genuinely different
# request shape than every other endpoint here (all JSON bodies) — deliberately
# out of scope for now, same as Files/Batches were in Phase 1: documented, not
# silently dropped. Generation (text -> image) is a plain JSON body.
_IMAGES_URL = "https://api.openai.com/v1/images/generations"


def call_images(body: dict, api_key: str) -> dict:
    return providers.send_request(_IMAGES_URL, _bearer_headers(api_key), body, timeout=120.0)


def extract_images_usage(response: dict) -> UsageInfo:
    """Only gpt-image-1 reports usage; dall-e-2/3 report none at all (the
    response has no `usage` key), which honestly resolves to cost_source
    "unknown" downstream — never a guessed number. gpt-image-1's own real
    usage mixes text tokens and image tokens at different per-1M rates
    ($5/$10 in, $40 out, roughly), which doesn't fit this table's single
    input/output rate without fabricating a blended price — left unregistered
    in pricing.py for the same reason; the raw text/image token split is
    still captured below for anyone who wants to compute their own rate (or
    a workspace can register one via the model pricing registry)."""
    usage = response.get("usage")
    if not usage:
        return UsageInfo(prompt_tokens=0, completion_tokens=0, cost=None, raw={})
    raw = {
        k: usage[k] for k in ("input_tokens_details", "output_tokens_details") if k in usage
    }
    return UsageInfo(
        prompt_tokens=int(usage.get("input_tokens", 0)),
        completion_tokens=int(usage.get("output_tokens", 0)),
        cost=None,  # the Images API never reports a per-request $ charge
        raw=raw,
    )


def images_preview(response: dict) -> str:
    n = len(response.get("data") or [])
    return f"{n} image(s)"


# ---- Audio: transcription (multipart upload) + TTS (JSON in, raw audio out) ----
_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
_SPEECH_URL = "https://api.openai.com/v1/audio/speech"


def call_transcription(
    file_bytes: bytes, filename: str, file_content_type: str, form_fields: dict, api_key: str
) -> tuple[bytes, str]:
    # no content-type header here — httpx sets the multipart boundary itself
    # from `files`; a manually-set content-type would break it.
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": (filename, file_bytes, file_content_type or "application/octet-stream")}
    data = {k: str(v) for k, v in form_fields.items() if v is not None}
    return providers.send_multipart(_TRANSCRIPTIONS_URL, headers, data, files)


def extract_transcription_usage(content: bytes, content_type: str) -> UsageInfo:
    """The billing unit depends on the model — verified against OpenAI's
    current API reference, not assumed: gpt-4o(-mini)-transcribe report a
    token usage object (usage.type == "tokens"); whisper-1 reports input
    duration instead (usage.type == "duration", usage.seconds). A
    response_format other than json/verbose_json (text/srt/vtt/diarized_json)
    reports no usage at all — that's honestly empty, not a guess, and not an
    error (transcription still succeeded)."""
    if "json" not in (content_type or ""):
        return UsageInfo(prompt_tokens=0, completion_tokens=0)
    try:
        response = json.loads(content)
    except json.JSONDecodeError:
        return UsageInfo(prompt_tokens=0, completion_tokens=0)
    usage = response.get("usage")
    if not usage:
        return UsageInfo(prompt_tokens=0, completion_tokens=0)
    if usage.get("type") == "duration":
        return UsageInfo(
            prompt_tokens=0, completion_tokens=0, duration_seconds=float(usage.get("seconds", 0))
        )
    if usage.get("type") == "tokens":
        raw = {k: usage[k] for k in ("input_token_details",) if k in usage}
        return UsageInfo(
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            raw=raw,
        )
    return UsageInfo(prompt_tokens=0, completion_tokens=0)


def transcription_preview(content: bytes, content_type: str) -> str:
    if "json" in (content_type or ""):
        try:
            text = json.loads(content).get("text")
            if isinstance(text, str):
                return text
        except json.JSONDecodeError:
            pass
        return ""
    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return ""


def transcription_request_preview(form_fields: dict, filename: str) -> str:
    """Human-readable stand-in for usage_logs.prompt_preview — there's no
    request body to read a prompt from (multipart), so describe the upload."""
    model = form_fields.get("model", "?")
    return f"[audio file: {filename}, model={model}]"


def call_speech(body: dict, api_key: str) -> tuple[bytes, str]:
    return providers.send_request_binary(_SPEECH_URL, _bearer_headers(api_key), body, timeout=90.0)


def extract_speech_usage(body: dict) -> UsageInfo:
    """TTS's response is raw audio with no usage object anywhere (verified:
    the endpoint's entire response body IS the audio, nothing else) — the
    billing dimension is input character count, which only exists in the
    REQUEST, so it's counted from `body` rather than the (binary) response."""
    text = body.get("input")
    characters = len(text) if isinstance(text, str) else None
    return UsageInfo(prompt_tokens=0, completion_tokens=0, characters=characters)


# ---- shared ----
def prompt_preview(body: dict) -> str:
    """Human-readable stand-in for usage_logs.prompt_preview (only stored when
    LOG_BODIES=true) — never sent upstream, purely for the Requests log."""
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        content = messages[-1].get("content")
        if isinstance(content, str):
            return content
    inp = body.get("input")
    if isinstance(inp, str):
        return inp
    if isinstance(inp, list) and inp and isinstance(inp[0], str):
        return " | ".join(inp[:5])
    prompt = body.get("prompt")  # images/generations' field name
    if isinstance(prompt, str):
        return prompt
    return ""
