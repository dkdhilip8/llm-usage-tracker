"""Test setup. Requires Postgres (the app SQL uses date_trunc / percentile_cont).

Local:  TEST_DATABASE_URL=postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test
CI:     the workflow starts a postgres service and sets the same var.
The test database is created if missing and its schema is dropped/recreated per session.
"""

import os

# Force test values — override anything the container/.env already set.
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ENABLE_LIVE"] = "false"
os.environ["SIMULATE_LATENCY_SLEEP"] = "false"
os.environ["LOG_BODIES"] = "true"
os.environ["SIGNUPS_PER_IP_PER_HOUR"] = "1000"  # a throttle test lowers this itself
os.environ["JOINS_PER_IP_PER_HOUR"] = "1000"
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

    with SessionLocal() as s:
        s.execute(
            text(
                "TRUNCATE usage_logs, allowed_providers, virtual_keys, "
                "provider_credentials, workspace_invites, users, workspaces "
                "RESTART IDENTITY CASCADE"
            )
        )
        s.commit()
    _p._cache.clear()
    from app.gateway import _pg_hits
    from app.routers.auth import _signups
    from app.routers.workspace import _joins

    _pg_hits.clear()
    _signups.clear()
    _joins.clear()
    yield


def _signup(c: TestClient, username: str, password: str = "pw-abcdefgh") -> dict:
    r = c.post("/api/auth/signup", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def signup():
    """A signed-in user with NO workspace yet. Returns (TestClient, user dict)."""
    n = {"i": 0}

    def _make(username: str | None = None, password: str = "pw-abcdefgh") -> tuple:
        n["i"] += 1
        c = TestClient(app)
        user = _signup(c, username or f"user{n['i']}", password)
        return c, user

    return _make


@pytest.fixture()
def new_admin():
    """Factory: a fresh signed-in Workspace Admin. Returns (TestClient, workspace dict)."""
    n = {"i": 0}

    def _make(ws_name: str | None = None) -> tuple:
        n["i"] += 1
        c = TestClient(app)
        _signup(c, f"admin{n['i']}")
        r = c.post("/api/workspace", json={"name": ws_name or f"ws{n['i']}"})
        assert r.status_code == 200, r.text
        return c, r.json()

    return _make


@pytest.fixture()
def admin_client(new_admin):
    c, _ = new_admin("Primary")
    return c


@pytest.fixture()
def new_member():
    """Factory: sign up a user and join an admin's workspace as a Team Member.
    Usage: new_member(admin_client) -> (TestClient, user_id)."""
    n = {"i": 0}

    def _make(admin_c: TestClient, username: str | None = None) -> tuple:
        n["i"] += 1
        inv = admin_c.post("/api/workspace/invites", json={}).json()
        assert "code" in inv, inv
        c = TestClient(app)
        _signup(c, username or f"member{n['i']}")
        r = c.post("/api/workspace/join", json={"code": inv["code"]})
        assert r.status_code == 200, r.text
        uid = c.get("/api/auth/me").json()["user"]["id"]
        return c, uid

    return _make


@pytest.fixture()
def make_key(admin_client):
    def _make(**over) -> dict:
        body = {"label": "t", "allowed_providers": ["openrouter"], **over}
        r = admin_client.post("/api/keys", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    return _make
