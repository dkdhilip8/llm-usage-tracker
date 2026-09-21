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
