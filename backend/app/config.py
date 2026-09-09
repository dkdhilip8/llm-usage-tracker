import warnings

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Built-in development defaults. Fine locally; refused when ENVIRONMENT is a
# deployed one (see _validate_deployment).
_DEV_SECRET_KEY = "dev-secret"
_DEPLOYED_ENVS = {"production", "prod", "staging"}


class Settings(BaseSettings):
    """Runtime configuration. All values have sensible defaults so the app boots
    with zero env vars for local development."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Deployment context. "development" (default) keeps the dev-default secrets
    # usable; a deployed value (production/staging) makes them a hard startup error.
    ENVIRONMENT: str = "development"

    # Signed session-cookie lifetime.
    SESSION_TTL_HOURS: int = 168

    # ---- signup + workspaces + per-tenant abuse caps ----
    ALLOW_SIGNUP: bool = True
    MAX_USERS: int = 300  # reject signup past this many accounts (whole instance)
    SIGNUPS_PER_IP_PER_HOUR: int = 5
    JOINS_PER_IP_PER_HOUR: int = 10
    MAX_WORKSPACES: int = 200
    MAX_MEMBERS_PER_WORKSPACE: int = 25
    MAX_KEYS_PER_WORKSPACE: int = 25
    MAX_OPEN_INVITES_PER_WORKSPACE: int = 50
    INVITE_TTL_DAYS: int = 14
    MAX_USAGE_ROWS_PER_WORKSPACE: int = 20000  # proxy stops recording past this
    PLAYGROUND_REQUESTS_PER_HOUR: int = 120  # per workspace

    # ---- live mode: a workspace attaches its own provider key(s) + a monthly cap per provider ----
    ALLOW_LIVE_KEYS: bool = True
    # Default monthly live-spend cap for a provider whose key has no explicit cap.
    LIVE_CAP_DEFAULT_USD: float = 5.0

    # ---- provider-credential encryption ----
    # A urlsafe-base64 32-byte Fernet key. Empty => derived from SECRET_KEY (a
    # deployed environment should set this explicitly so rotating SECRET_KEY does
    # not orphan stored provider keys).
    ENCRYPTION_KEY: str = ""

    # Postgres. Render/Neon hand out `postgres://` or `postgresql://`; `sqlalchemy_url`
    # rewrites the scheme to the psycopg3 driver.
    DATABASE_URL: str = "postgresql+psycopg://llm:llm@localhost:5432/llmtracker"

    # HMAC pepper for virtual-key hashing + session-cookie signing. Never stored
    # alongside the hash.
    SECRET_KEY: str = "dev-secret"

    # Comma-separated origins for local dev (Vite on :5173). Empty in prod (same origin).
    CORS_ORIGINS: str = ""

    # ---- provider credentials (server-side; a workspace key can also be attached in-app) ----
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    GEMINI_API_KEY: str = ""  # Google AI Studio key; used via the OpenAI-compatible endpoint

    # Seconds to cache a provider liveness check.
    PROVIDER_CHECK_TTL: int = 300

    # Store truncated prompt/response text on usage_logs for the Requests log view.
    # Off by default — request bodies can contain sensitive data.
    LOG_BODIES: bool = False

    VERSION: str = "0.11.0"

    @model_validator(mode="after")
    def _validate_deployment(self) -> "Settings":
        """Fail closed on unsafe config in a deployed ENVIRONMENT."""
        if self.ENVIRONMENT.strip().lower() not in _DEPLOYED_ENVS:
            return self
        errors = []
        if self.SECRET_KEY == _DEV_SECRET_KEY:
            errors.append("SECRET_KEY is still the built-in development default")
        if errors:
            raise ValueError(
                f"ENVIRONMENT={self.ENVIRONMENT!r}: " + "; ".join(errors)
            )
        if not self.ENCRYPTION_KEY:
            warnings.warn(
                "ENCRYPTION_KEY is not set — encryption falls back to a key derived "
                "from SECRET_KEY. Set ENCRYPTION_KEY explicitly so rotating "
                "SECRET_KEY does not orphan stored provider keys.",
                stacklevel=2,
            )
        return self

    @property
    def sqlalchemy_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def provider_api_key(self, provider: str) -> str:
        return {
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "openrouter": self.OPENROUTER_API_KEY,
            "gemini": self.GEMINI_API_KEY,
        }.get(provider, "")


settings = Settings()
