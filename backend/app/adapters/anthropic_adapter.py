"""Native Anthropic Messages API adapter. The gateway validates only `model`;
everything else in the client's JSON body (system, messages with content
blocks — text/image/document/tool_use/tool_result, cache_control, tools,
tool_choice, thinking, ...) is forwarded to Anthropic untouched. Unlike the
legacy /v1/chat/completions translation path, `max_tokens` is NOT defaulted
here — Anthropic requires it, so a client that omits it gets Anthropic's own
422, not a silently guessed value."""

import json

from app import providers
from app.adapters.base import UsageInfo

_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


def _headers(api_key: str) -> dict:
    return {
        "x-api-key": api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def call(body: dict, api_key: str) -> dict:
    payload = {k: v for k, v in body.items() if k != "stream"}
    return providers.send_request(_URL, _headers(api_key), payload)


def stream(body: dict, api_key: str):
    payload = {**body, "stream": True}
    yield from providers.stream_request(_URL, _headers(api_key), payload)


def extract_usage(response: dict) -> UsageInfo:
    usage = response.get("usage") or {}
    raw = {
        k: usage[k]
        for k in ("cache_creation_input_tokens", "cache_read_input_tokens")
        if k in usage
    }
    return UsageInfo(
        prompt_tokens=int(usage.get("input_tokens", 0)),
        completion_tokens=int(usage.get("output_tokens", 0)),
        cost=None,  # the Messages API does not report a per-request charge
        raw=raw,
    )


def response_text_preview(response: dict) -> str:
    blocks = response.get("content") or []
    return "".join(
        b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    )


class AnthropicStreamUsageAccumulator:
    """Anthropic's usage is split across two named SSE events: `message_start`
    carries input_tokens (+ cache fields) inside message.usage, and
    `message_delta` carries the running output_tokens. Feed every line; read
    `.usage()` once the stream ends."""

    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.raw: dict = {}

    def feed(self, line: str) -> None:
        if not line.startswith("data:"):
            return
        try:
            evt = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            return
        evt_type = evt.get("type")
        if evt_type == "message_start":
            usage = (evt.get("message") or {}).get("usage") or {}
            self.prompt_tokens = int(usage.get("input_tokens", self.prompt_tokens))
            for k in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                if k in usage:
                    self.raw[k] = usage[k]
        elif evt_type == "message_delta":
            usage = evt.get("usage") or {}
            if "output_tokens" in usage:
                self.completion_tokens = int(usage["output_tokens"])

    def usage(self) -> UsageInfo:
        return UsageInfo(self.prompt_tokens, self.completion_tokens, cost=None, raw=self.raw)


def prompt_preview(body: dict) -> str:
    """Human-readable stand-in for usage_logs.prompt_preview (only stored when
    LOG_BODIES=true) — never sent upstream, purely for the Requests log."""
    messages = body.get("messages") or []
    if not messages:
        return ""
    content = messages[-1].get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        return " ".join(t for t in texts if t)
    return ""
