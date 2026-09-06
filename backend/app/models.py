from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    """A signed-up account. `is_admin` users (the env ADMIN_USERNAME row, created
    on boot) see everything; regular users only see their own keys + usage."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)  # login id
    # "<salt_hex>$<scrypt_hex>", or "" for unusable (the demo account).
    password_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when the user creates or joins a workspace (owner or member).
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="SET NULL", use_alter=True, name="fk_users_workspace"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Workspace(Base):
    """A team gateway: one owner's encrypted provider key(s), shared by members'
    virtual keys for live calls, under a monthly spend cap."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    join_code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    monthly_cap_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class VirtualKey(Base):
    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Owning account. Nullable for a smooth migration; backfilled to the demo user on boot.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    # HMAC-SHA256(SECRET_KEY, raw_key), hex. The raw key is shown once and never stored.
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # "vk_" + first 8 chars of the raw key — display only.
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)
    # If true (and ENABLE_LIVE + provider configured & valid), requests hit real providers.
    allow_live: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Provider used when an OpenAI-compatible request sends a bare model name (no "provider/" prefix).
    default_provider: Mapped[str | None] = mapped_column(String)
    # Spend cap (USD) per budget window. Null = unlimited. Over budget -> 402 budget_exceeded.
    monthly_budget_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    # Budget window: "day" | "week" | "month" (rolling, calendar-aligned) or "custom".
    budget_period: Mapped[str] = mapped_column(String, nullable=False, default="month")
    # For budget_period == "custom": the fixed [start, end) the cap applies to.
    budget_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    allowed_providers: Mapped[list["AllowedProvider"]] = relationship(
        back_populates="virtual_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def provider_names(self) -> list[str]:
        return sorted(ap.provider for ap in self.allowed_providers)


class AllowedProvider(Base):
    __tablename__ = "allowed_providers"
    __table_args__ = (
        UniqueConstraint("virtual_key_id", "provider", name="uq_allowed_provider"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    virtual_key_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("virtual_keys.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)

    virtual_key: Mapped[VirtualKey] = relationship(back_populates="allowed_providers")


class ProviderCredential(Base):
    """A provider API key, encrypted at rest. `workspace_id` set = that workspace's
    key (used by its members' live calls). `workspace_id` NULL = the admin-global
    key (only when ALLOW_DB_PROVIDER_KEYS, local/non-prod). An env var always wins."""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_provcred_ws_provider"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)  # openai|anthropic|openrouter
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet token
    last4: Mapped[str] = mapped_column(String(8), nullable=False)  # display only
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("virtual_keys.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)  # openai | anthropic | openrouter
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # USD. When cost_source == "provider" this is the amount the provider actually
    # charged (OpenRouter's usage.cost); when "configured" it's tokens x price table.
    cost: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False)
    cost_source: Mapped[str] = mapped_column(String, nullable=False, default="configured")
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="success")
    # Mode of this request: "simulated" | "live". (simulated bool kept for back-compat.)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="simulated")
    # Truncated request/response text — populated only when LOG_BODIES=true.
    prompt_preview: Mapped[str | None] = mapped_column(Text)
    response_preview: Mapped[str | None] = mapped_column(Text)
    # spec's "timestamp"; named `ts` to dodge the SQL type-name clash.
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        Index("ix_usage_logs_key_id", "key_id"),
        Index("ix_usage_logs_provider", "provider"),
        Index("ix_usage_logs_model", "model"),
    )
