"""Test setup. Requires Postgres (the app SQL uses date_trunc / percentile_cont).

Local:  TEST_DATABASE_URL=postgresql+psycopg://llm:llm@localhost:5432/llmtracker_test
CI:     the workflow starts a postgres service and sets the same var.
The test database is created if missing and its schema is dropped/recreated per session.
"""

import json
import os

# Force test values — override anything the container/.env already set.
os.environ["SECRET_KEY"] = "test-secret"
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


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """No test should make a real HTTP call. Liveness checks return False without
    hitting the network; `mock_provider` (opt-in) stubs the actual completion."""
    monkeypatch.setattr("app.providers.check_key", lambda provider, key: False)
    monkeypatch.setattr(
        "app.providers.check_liveness", lambda provider, **kw: False
    )


def _last_text(json_body: dict) -> str:
    """Best-effort echo text for a canned response, regardless of request shape."""
    messages = json_body.get("messages")
    if isinstance(messages, list) and messages:
        content = messages[-1].get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
    inp = json_body.get("input")
    if isinstance(inp, str):
        return inp
    return ""


@pytest.fixture()
def mock_provider(monkeypatch):
    """Stub every outbound provider call — both the legacy call_provider /
    stream_openai_compatible functions (used by /v1/proxy/chat and the
    anthropic/gemini legacy path of /v1/chat/completions) AND the lower-level
    app.providers.send_request / stream_request seam (used by the new
    adapters: /v1/messages, /v1/responses, and the openai/openrouter
    full-fidelity path of /v1/chat/completions). Returns the list of recorded
    calls — legacy calls append (provider, model, prompt, api_key) tuples,
    adapter calls append (url, headers, json_body) tuples; no single test
    exercises both shapes, so calls[0] is unambiguous per test.

    By default cost is unreported (=> cost_source 'configured'/'unknown');
    monkeypatch app.providers.send_request/stream_request directly for a
    provider-cost variant."""
    calls: list[tuple] = []

    def fake_call(provider, model, prompt, api_key=None):
        calls.append((provider, model, prompt, api_key))
        return (f"reply to: {prompt}", 5, 7, None)

    def fake_stream(provider, model, prompt, api_key=None):
        calls.append((provider, model, prompt, api_key))
        yield ("delta", "hello ")
        yield ("delta", "world")
        yield ("done", {"prompt_tokens": 5, "completion_tokens": 7, "cost": None})

    def fake_send(url, headers, json_body, *, timeout=60.0):
        calls.append((url, headers, json_body))
        text = f"reply to: {_last_text(json_body)}"
        n = len(calls)  # every real provider response has a unique id; so must ours
        if "anthropic.com" in url:
            return {
                "id": f"msg_test_{n}",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "model": json_body.get("model"),
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 7},
            }
        if url.endswith("/responses"):
            return {
                "id": f"resp_test_{n}",
                "object": "response",
                "status": "completed",
                "model": json_body.get("model"),
                "output_text": text,
                "output": [],
                "usage": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
            }
        if "generativelanguage.googleapis.com" in url:
            return {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 7,
                    "totalTokenCount": 12,
                },
                "modelVersion": json_body.get("model", ""),
                "responseId": f"gemini_test_{n}",
            }
        return {
            "id": f"chatcmpl_test_{n}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": json_body.get("model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        }

    def fake_stream_request(url, headers, json_body, *, timeout=60.0):
        calls.append((url, headers, json_body))
        if "anthropic.com" in url:
            yield "event: message_start"
            yield 'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}'
            yield ""
            yield "event: content_block_delta"
            yield (
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"hello "}}'
            )
            yield ""
            yield "event: content_block_delta"
            yield (
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"world"}}'
            )
            yield ""
            yield "event: message_delta"
            yield 'data: {"type":"message_delta","usage":{"output_tokens":7}}'
            yield ""
            yield "event: message_stop"
            yield 'data: {"type":"message_stop"}'
        elif url.endswith("/responses"):
            yield 'data: {"type":"response.output_text.delta","delta":"hello "}'
            yield ""
            yield 'data: {"type":"response.output_text.delta","delta":"world"}'
            yield ""
            yield (
                'data: {"type":"response.completed","response":'
                '{"usage":{"input_tokens":5,"output_tokens":7}}}'
            )
            yield ""
            yield "data: [DONE]"
        elif "generativelanguage.googleapis.com" in url:
            yield "data: " + json.dumps(
                {
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": "hello "}]}, "index": 0}
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
                }
            )
            yield ""
            yield "data: " + json.dumps(
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "world"}]},
                            "finishReason": "STOP",
                            "index": 0,
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 7, "totalTokenCount": 12},
                }
            )
        else:
            base = {
                "id": "chatcmpl_test",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": json_body.get("model"),
            }
            yield "data: " + json.dumps(
                {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "hello "}, "finish_reason": None}]}
            )
            yield ""
            yield "data: " + json.dumps(
                {**base, "choices": [{"index": 0, "delta": {"content": "world"}, "finish_reason": None}]}
            )
            yield ""
            yield "data: " + json.dumps(
                {
                    **base,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12, "cost": None},
                }
            )
            yield ""
            yield "data: [DONE]"

    monkeypatch.setattr("app.providers.call_provider", fake_call)
    monkeypatch.setattr("app.providers.stream_openai_compatible", fake_stream)
    monkeypatch.setattr("app.providers.send_request", fake_send)
    monkeypatch.setattr("app.providers.stream_request", fake_stream_request)
    return calls


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


@pytest.fixture()
def make_live_key(admin_client):
    """Attaches a (fake) key for every provider to the admin's workspace, then
    mints keys that default to allow_live=True. Pair with `mock_provider`."""
    for p in ("openrouter", "openai", "anthropic", "gemini"):
        r = admin_client.put(
            f"/api/workspace/providers/{p}/key", json={"api_key": f"sk-fake-{p}-key-000000"}
        )
        assert r.status_code == 200, r.text

    def _make(**over) -> dict:
        body = {
            "label": "t",
            "allowed_providers": ["openrouter"],
            "allow_live": True,
            **over,
        }
        r = admin_client.post("/api/keys", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    return _make
