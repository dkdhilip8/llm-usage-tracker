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


class Workspace(Base):
    """A tenant boundary. One user creates it and becomes its Workspace Admin;
    others join by single-use invite as Team Members. Every virtual key, provider
    credential, and usage row belongs to exactly one workspace, and no query ever
    crosses that boundary."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # not unique — you join by invite
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class User(Base):
    """A signed-up account. A user belongs to at most one workspace at a time
    (`workspace_id` / `workspace_role`); a fresh signup belongs to none until it
    creates or joins one."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)  # login id
    # "<salt_hex>$<scrypt_hex>". Always set (signup requires a password).
    password_hash: Mapped[str] = mapped_column(String, nullable=False, default="")
    # The one workspace this user is in, or NULL. Role is "admin" | "member".
    workspace_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        index=True,
    )
    workspace_role: Mapped[str | None] = mapped_column(String)  # "admin" | "member"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkspaceInvite(Base):
    """A single-use code a Workspace Admin mints for one person. Consumed on join;
    revocable; expires. "Open" = accepted_at IS NULL AND expires_at > now()."""

    __tablename__ = "workspace_invites"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String)  # e.g. "for Jane"
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VirtualKey(Base):
    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Tenancy anchor — every read/write scopes on this.
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The Team Member this key is assigned to. NULL = workspace-level / unassigned
    # (only the Workspace Admin sees it).
    assigned_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True
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
    """A workspace's provider API key, encrypted at rest. Its live-flagged virtual
    keys call real providers on it, up to `monthly_cap_usd`. A server env var for
    the same provider always wins over this."""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("workspace_id", "provider", name="uq_provcred_ws_provider"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)  # openai|anthropic|openrouter|gemini
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet token
    last4: Mapped[str] = mapped_column(String(8), nullable=False)  # display only
    # Monthly ceiling (USD) on live spend routed through this provider key.
    # NULL => fall back to settings.LIVE_CAP_DEFAULT_USD.
    monthly_cap_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
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
