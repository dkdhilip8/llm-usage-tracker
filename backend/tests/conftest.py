"""Test setup. Requires Postgres (the app SQL uses date_trunc / percentile_cont).

Local:  TEST_DATABASE_URL=postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test
CI:     the workflow starts a postgres service and sets the same var.
The test database is created if missing and its schema is dropped/recreated per session.
"""

import os

# Force test values — override anything the container/.env already set.
os.environ["ADMIN_TOKEN"] = "test-admin"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ENABLE_LIVE"] = "false"
os.environ["SEED_DEMO_DATA"] = "false"
os.environ["SIMULATE_LATENCY_SLEEP"] = "false"
os.environ["LOG_BODIES"] = "true"

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
    with SessionLocal() as s:
        s.execute(
            text(
                "TRUNCATE usage_logs, allowed_providers, virtual_keys "
                "RESTART IDENTITY CASCADE"
            )
        )
        s.commit()
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
