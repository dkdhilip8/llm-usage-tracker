from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---- keys ----
class KeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    allowed_providers: list[str] = Field(min_length=1)
    allow_live: bool = False
    default_provider: str | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)


class KeyUpdate(BaseModel):
    allow_live: bool | None = None
    default_provider: str | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    clear_budget: bool = False  # explicit "remove the cap" (monthly_budget_usd=None)


class KeyCreated(BaseModel):
    id: int
    label: str
    key: str  # full raw key — returned exactly once
    key_prefix: str
    allowed_providers: list[str]
    allow_live: bool
    default_provider: str | None
    monthly_budget_usd: float | None
    created_at: datetime


class KeyOut(BaseModel):
    id: int
    label: str
    key_prefix: str
    allowed_providers: list[str]
    allow_live: bool
    default_provider: str | None
    monthly_budget_usd: float | None
    spend_month: float
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    requests: int
    total_tokens: int
    cost: float


# ---- providers ----
class ProviderStatus(BaseModel):
    provider: str
    env_var: str
    configured: bool
    valid: bool
    checked_at: str | None = None


class KeyInspect(BaseModel):
    label: str
    allow_live: bool
    providers: list[dict]  # {provider, configured, valid, mode}


# ---- proxy ----
class ChatRequest(BaseModel):
    provider: str
    model: str
    prompt: str | None = None
    messages: list[dict] | None = None


class OpenAIChatRequest(BaseModel):
    """Subset of the OpenAI chat-completions request. Extra fields are accepted
    and ignored so the OpenAI SDK works unmodified."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatResponse(BaseModel):
    request_id: str
    provider: str
    model: str
    mode: str  # "simulated" | "live"
    simulated: bool
    response: str
    usage: Usage
    cost: float
    cost_source: str  # "provider" (real charge) | "configured" (tokens x price table)
    pricing: dict
    latency_ms: int
