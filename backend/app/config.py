from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. All values have demo-safe defaults so the app boots
    with zero env vars for local experimentation."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres. Render/Neon hand out `postgres://` or `postgresql://`; `sqlalchemy_url`
    # rewrites the scheme to the psycopg3 driver.
    DATABASE_URL: str = "postgresql+psycopg://llm:llm@localhost:5432/llmtracker"

    # Shared secret for the admin API (create/list/revoke keys). Sent as `X-Admin-Token`.
    ADMIN_TOKEN: str = "dev-admin-token"

    # HMAC pepper for virtual-key hashing. Never stored alongside the hash.
    SECRET_KEY: str = "dev-secret"

    # Comma-separated origins for local dev (Vite on :5173). Empty in prod (same origin).
    CORS_ORIGINS: str = ""

    # ---- provider credentials (server-side only; never in the DB or UI) ----
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # Master switch for live upstream calls. Default off keeps the public demo $0.
    # Even when on, a request also needs key.allow_live + a configured & valid provider.
    ENABLE_LIVE: bool = False

    # Seconds to cache a provider liveness check.
    PROVIDER_CHECK_TTL: int = 300

    # Store truncated prompt/response text on usage_logs for the Requests log view.
    # Off by default — request bodies can contain sensitive data.
    LOG_BODIES: bool = False

    # Simulator sleeps to mimic real latency. Tests set this false for speed.
    SIMULATE_LATENCY_SLEEP: bool = True

    VERSION: str = "0.6.0"

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
        }.get(provider, "")


settings = Settings()
