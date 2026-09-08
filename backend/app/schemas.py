from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BudgetPeriod = Literal["day", "week", "month", "custom"]


# ---- keys ----
class KeyCreate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    allowed_providers: list[str] = Field(min_length=1)
    allow_live: bool = False
    default_provider: str | None = None
    assigned_user_id: int | None = None  # a Team Member of this workspace, or null
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    budget_period: BudgetPeriod = "month"
    budget_start: date | None = None  # required when budget_period == "custom"
    budget_end: date | None = None  # inclusive end date


class KeyUpdate(BaseModel):
    allow_live: bool | None = None
    default_provider: str | None = None
    assigned_user_id: int | None = None
    clear_assignment: bool = False
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    budget_period: BudgetPeriod | None = None
    budget_start: date | None = None
    budget_end: date | None = None
    clear_budget: bool = False


class KeyCreated(BaseModel):
    id: int
    label: str
    key: str  # full raw key — returned exactly once
    key_prefix: str
    allowed_providers: list[str]
    allow_live: bool
    default_provider: str | None
    monthly_budget_usd: float | None
    budget_period: str
    budget_start: datetime | None
    budget_end: datetime | None
    created_at: datetime


class KeyOut(BaseModel):
    id: int
    label: str
    key_prefix: str
    assigned_username: str | None = None  # the Team Member it's assigned to (admin view)
    allowed_providers: list[str]
    allow_live: bool
    default_provider: str | None
    monthly_budget_usd: float | None
    budget_period: str
    budget_start: datetime | None
    budget_end: datetime | None
    spend_period: float
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
    configured_via_env: bool


# ---- account (personal, workspace-agnostic) ----
class WorkspaceRef(BaseModel):
    id: int
    name: str
    role: str  # "admin" | "member"


class AccountOut(BaseModel):
    username: str
    workspace: WorkspaceRef | None
    live_cap_default_usd: float


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
