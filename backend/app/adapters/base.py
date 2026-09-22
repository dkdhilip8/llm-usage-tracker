"""Shared contract every provider adapter follows.

An adapter owns its own request validation/construction and response parsing —
the governance layer above it (auth, budgets, workspace caps) and the HTTP
layer below it (app.providers.send_request / stream_request) are shared; the
provider-specific request/response shape in between is not collapsed into one
common format. The response returned to the client is the provider's own JSON,
untouched — UsageInfo below is only what governance/logging need, pulled out
of that real response, never reconstructed from it.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageInfo:
    # Token-billed operations (chat, embeddings, most image gen) set these;
    # a non-token-billed operation (audio) leaves both 0 and sets
    # duration_seconds or characters below instead — never approximated as
    # tokens just to fit this shape.
    prompt_tokens: int
    completion_tokens: int
    # Non-None only when the provider itself reports a real per-request charge
    # (e.g. OpenRouter's usage.cost). None => the caller falls back to
    # price-table estimation, which itself can honestly come back None for an
    # unregistered model — never a guessed number.
    cost: float | None = None
    # Whatever extra usage detail the provider exposed (cached/reasoning
    # tokens, cache-write tokens, per-modality breakdown, ...) — kept in its
    # provider-native shape and stored verbatim in usage_logs.usage_raw rather
    # than forced into new typed columns every time a provider's usage object
    # grows a field.
    raw: dict[str, Any] = field(default_factory=dict)
    # Non-token billing dimensions — set at most one, only by an adapter whose
    # provider actually bills this way (audio transcription: input duration;
    # TTS: input character count). See gateway.completion_result_from_usage
    # for how these map to cost, and pricing.py for the duration/character
    # price tables.
    duration_seconds: float | None = None
    characters: int | None = None
