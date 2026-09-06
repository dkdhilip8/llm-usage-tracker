"""Test setup. Requires Postgres (the app SQL uses date_trunc / percentile_cont).

Local:  TEST_DATABASE_URL=postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test
CI:     the workflow starts a postgres service and sets the same var.
The test database is created if missing and its schema is dropped/recreated per session.
"""

import os

# Force test values — override anything the container/.env already set.
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["ADMIN_USERNAME"] = "tester"
os.environ["ADMIN_PASSWORD"] = "test-password-1234"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ENABLE_LIVE"] = "false"
os.environ["SIMULATE_LATENCY_SLEEP"] = "false"
os.environ["LOG_BODIES"] = "true"
os.environ["ALLOW_DB_PROVIDER_KEYS"] = "true"  # dev-only feature, exercised in tests
os.environ["SIGNUPS_PER_IP_PER_HOUR"] = "1000"  # a throttle test lowers this itself
# Never let a developer's real provider keys (from ./.env) bleed into tests.
os.environ["OPENAI_API_KEY"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test",
)
os.environ["DATABASE_URL"] = _TEST_DB

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine.url import make_url  # noqa: E402


def _ensure_database() -> None:
    url = make_url(_TEST_DB)
    server = url.set(database="postgres")
    eng = create_engine(server, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        eng.dispose()


_ensure_database()

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    from app import providers as _p
    from app.bootstrap import bootstrap

    with SessionLocal() as s:
        s.execute(
            text(
                "TRUNCATE usage_logs, allowed_providers, virtual_keys, "
                "provider_credentials, users RESTART IDENTITY CASCADE"
            )
        )
        s.commit()
        bootstrap(s)  # recreate the admin user row
    _p._db_keys.clear()  # drop stale decrypted-key cache between tests
    _p._cache.clear()
    from app.gateway import _pg_hits
    from app.routers.auth import _signups

    _pg_hits.clear()
    _signups.clear()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin() -> dict:
    return {"X-Admin-Token": "test-admin"}


@pytest.fixture()
def make_key(client, admin):
    def _make(**over) -> dict:
        body = {"label": "t", "allowed_providers": ["openrouter"], **over}
        r = client.post("/api/keys", json=body, headers=admin)
        assert r.status_code == 200, r.text
        return r.json()

    return _make


@pytest.fixture()
def signup():
    """Create an isolated logged-in user; returns (its own TestClient, user dict)."""
    n = {"i": 0}

    def _make(email: str | None = None, password: str = "pw-abcdefgh") -> tuple:
        n["i"] += 1
        c = TestClient(app)
        r = c.post(
            "/api/auth/signup",
            json={"email": email or f"u{n['i']}@example.com", "password": password},
        )
        assert r.status_code == 200, r.text
        return c, r.json()["user"]

    return _make
