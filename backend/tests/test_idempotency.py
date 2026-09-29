"""Idempotency (Phase 4): a client-supplied Idempotency-Key lets a retried
billable request replay the stored response instead of calling — and
re-billing — the provider again.

Two layers are covered:
  * HTTP-level, through the real app + the conftest provider mock, proving the
    end-to-end behavior on a representative native endpoint (/v1/messages), the
    run_completion path (/v1/proxy/chat), and the error/oversize/encryption
    edges.
  * Module-level, exercising app.idempotency.claim / claim_streaming directly
    under real thread concurrency against the real Postgres row lock — the same
    style as test_ratelimit's atomicity test.

Everything runs against the conftest provider mock; no live network calls.
"""

import threading
import uuid

import httpx
import pytest
from sqlalchemy import select

from app import idempotency
from app.config import settings
from app.db import SessionLocal
from app.idempotency import Idem
from app.models import IdempotencyRecord, VirtualKey, Workspace


def _hdr(key: str, idem: str | None = None) -> dict:
    h = {"x-api-key": key}
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


_BODY = {
    "model": "claude-3-5-haiku-20241022",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "hi"}],
}


def _msg(client, key, idem=None, body=None):
    return client.post("/v1/messages", json=body or _BODY, headers=_hdr(key, idem))


# ---------------------------------------------------------------- HTTP: replay


def test_no_idempotency_key_is_unchanged_behavior(client, make_live_key, mock_provider):
    """Without the header, nothing is stored and every request hits the provider —
    exactly as before the feature existed."""
    k = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k["key"])
    r2 = _msg(client, k["key"])
    assert r1.status_code == r2.status_code == 200
    assert len(mock_provider) == 2  # provider called both times
    assert r1.json()["id"] != r2.json()["id"]  # two distinct upstream responses


def test_same_key_same_request_replays_without_recalling(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k["key"], idem="abc-123")
    r2 = _msg(client, k["key"], idem="abc-123")
    assert r1.status_code == r2.status_code == 200
    assert len(mock_provider) == 1  # provider called exactly once
    assert r1.json() == r2.json()  # byte-identical replay (same upstream id)
    assert r1.headers.get("Idempotent-Replayed") is None
    assert r2.headers.get("Idempotent-Replayed") == "true"


def test_replay_records_only_one_usage_row(client, admin_client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    _msg(client, k["key"], idem="one-row")
    _msg(client, k["key"], idem="one-row")
    items = admin_client.get("/api/requests").json()["items"]
    assert len(items) == 1  # the replay did not create a second usage row


def test_same_key_different_body_is_422(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k["key"], idem="dup")
    other = {**_BODY, "messages": [{"role": "user", "content": "DIFFERENT"}]}
    r2 = _msg(client, k["key"], idem="dup", body=other)
    assert r1.status_code == 200
    assert r2.status_code == 422
    assert r2.json()["detail"]["type"] == "idempotency_key_reused"
    assert len(mock_provider) == 1  # the mismatched retry never reached the provider


def test_key_is_scoped_per_virtual_key(client, make_live_key, mock_provider):
    """The same Idempotency-Key string under a different virtual key is a
    different namespace — no cross-key retrieval, provider called for each."""
    k1 = make_live_key(allowed_providers=["anthropic"])
    k2 = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k1["key"], idem="shared-string")
    r2 = _msg(client, k2["key"], idem="shared-string")
    assert r1.status_code == r2.status_code == 200
    assert len(mock_provider) == 2
    assert r1.json()["id"] != r2.json()["id"]


def test_response_body_is_encrypted_at_rest(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["anthropic"])
    r = _msg(client, k["key"], idem="enc")
    plaintext_marker = r.json()["content"][0]["text"]  # "reply to: hi"
    with SessionLocal() as s:
        row = s.scalar(select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "enc"))
        assert row.status == "completed" and row.replayable is True
        assert row.response_body_encrypted is not None
        assert plaintext_marker.encode() not in row.response_body_encrypted.encode()
        # …and it round-trips back to the exact bytes.
        assert idempotency._decrypt_body(row.response_body_encrypted) == r.content


# ---------------------------------------------------------------- HTTP: errors


def _raise(status: int, body: bytes = b'{"error":"x"}'):
    req = httpx.Request("POST", "https://example.test/x")
    resp = httpx.Response(status, content=body, headers={"content-type": "application/json"}, request=req)

    def boom(*a, **kw):
        raise httpx.HTTPStatusError(f"HTTP {status}", request=req, response=resp)

    return boom


def test_provider_4xx_is_cached_terminal_and_replayed(client, make_live_key, monkeypatch):
    body = b'{"type":"error","error":{"message":"bad request"}}'
    monkeypatch.setattr("app.providers.send_request", _raise(400, body))
    calls = {"n": 0}
    orig = idempotency.claim

    def counting_claim(idem):
        calls["n"] += 1
        return orig(idem)

    monkeypatch.setattr(idempotency, "claim", counting_claim)
    k = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k["key"], idem="det-4xx")
    r2 = _msg(client, k["key"], idem="det-4xx")
    assert r1.status_code == r2.status_code == 400
    assert r1.content == r2.content == body
    # the second call replayed (short-circuited in begin(), never re-claimed)
    assert calls["n"] == 1


def test_provider_5xx_releases_claim_so_retry_reexecutes(client, make_live_key, monkeypatch):
    """A transient 5xx must NOT poison the key: the claim is released and a
    genuine retry with the same key reaches the provider again."""
    k = make_live_key(allowed_providers=["anthropic"])

    # first attempt: 503 -> the claim must be released, not left dangling
    monkeypatch.setattr("app.providers.send_request", _raise(503))
    r1 = _msg(client, k["key"], idem="transient")
    assert r1.status_code == 503
    with SessionLocal() as s:
        assert s.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "transient")
        ) is None

    # second attempt: same key, provider recovered — reaches the provider again
    def ok(url, headers, json_body, *, timeout=60.0):
        return {
            "id": "msg_recovered",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "model": json_body.get("model"),
            "usage": {"input_tokens": 5, "output_tokens": 7},
        }

    monkeypatch.setattr("app.providers.send_request", ok)
    r2 = _msg(client, k["key"], idem="transient")
    assert r2.status_code == 200
    assert r2.json()["id"] == "msg_recovered"


def test_oversize_response_is_terminal_but_not_replayable(client, make_live_key, mock_provider, monkeypatch):
    monkeypatch.setattr(settings, "IDEMPOTENCY_MAX_REPLAY_BYTES", 5)  # smaller than any real body
    k = make_live_key(allowed_providers=["anthropic"])
    r1 = _msg(client, k["key"], idem="big")
    assert r1.status_code == 200
    r2 = _msg(client, k["key"], idem="big")
    assert r2.status_code == 409
    assert r2.json()["detail"]["type"] == "idempotency_not_replayable"
    assert len(mock_provider) == 1  # still no second billable call


# ------------------------------------------------------------- HTTP: validation


@pytest.mark.parametrize("bad", ["", "   ", "x" * 256])
def test_invalid_idempotency_key_is_400(client, make_live_key, mock_provider, bad):
    k = make_live_key(allowed_providers=["anthropic"])
    r = _msg(client, k["key"], idem=bad)
    assert r.status_code == 400
    assert r.json()["detail"]["type"] == "invalid_idempotency_key"
    assert len(mock_provider) == 0  # rejected before any provider call


# ------------------------------------------------- HTTP: run_completion path


def test_proxy_chat_replays(client, make_live_key, mock_provider):
    k = make_live_key(allowed_providers=["openrouter"])
    body = {"provider": "openrouter", "model": "x/y", "prompt": "hello"}
    r1 = client.post("/v1/proxy/chat", json=body, headers=_hdr(k["key"], "px-1"))
    r2 = client.post("/v1/proxy/chat", json=body, headers=_hdr(k["key"], "px-1"))
    assert r1.status_code == r2.status_code == 200
    assert len(mock_provider) == 1
    assert r1.json()["request_id"] == r2.json()["request_id"]


def test_governance_rejection_does_not_consume_claim(client, make_key):
    """A key rejected by governance (here: paused => 403, before the provider
    call) must NOT leave an idempotency claim — a later, fixed retry has to be
    able to run. Regression test for the run_completion path where the live-
    readiness check must sit before the claim."""
    k = make_key(allowed_providers=["openrouter"])  # allow_live defaults False -> 403 key_paused
    body = {"provider": "openrouter", "model": "x/y", "prompt": "hi"}
    r = client.post("/v1/proxy/chat", json=body, headers=_hdr(k["key"], "gov-reject"))
    assert r.status_code == 403
    with SessionLocal() as s:
        assert s.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "gov-reject")
        ) is None  # no claim consumed


def test_run_completion_provider_4xx_is_cached_terminal(client, make_live_key, monkeypatch):
    """The run_completion path (proxy/chat, legacy chat/completions) must cache a
    deterministic provider 4xx as terminal and replay it, same as the native
    funnels — and not re-call the provider."""
    calls = {"n": 0}

    def boom(provider, model, prompt, api_key=None):
        calls["n"] += 1
        _raise(400)()

    monkeypatch.setattr("app.providers.call_provider", boom)
    k = make_live_key(allowed_providers=["openrouter"])
    body = {"provider": "openrouter", "model": "x/y", "prompt": "hi"}
    r1 = client.post("/v1/proxy/chat", json=body, headers=_hdr(k["key"], "rc-4xx"))
    r2 = client.post("/v1/proxy/chat", json=body, headers=_hdr(k["key"], "rc-4xx"))
    assert r1.status_code == r2.status_code == 400
    assert r1.json() == r2.json()  # replayed byte-for-byte
    assert calls["n"] == 1  # provider called once; the retry replayed


# ---------------------------------------------------- module-level: concurrency


def _seed_vk() -> tuple[int, int]:
    with SessionLocal() as s:
        ws = Workspace(name="idem-ws")
        s.add(ws)
        s.flush()
        vk = VirtualKey(
            workspace_id=ws.id,
            label="t",
            key_hash=f"hash-{uuid.uuid4().hex}",
            key_prefix="vk_test",
            allow_live=True,
        )
        s.add(vk)
        s.commit()
        return ws.id, vk.id


def _idem(ws_id: int, vk_id: int, db, *, key="k", fp="fp") -> Idem:
    return Idem(
        enabled=True, db=db, key_id=vk_id, workspace_id=ws_id,
        key=key, fingerprint=fp, endpoint="/x", provider="openai", model="m",
    )


def test_concurrent_claims_elect_exactly_one_leader():
    """N threads race the same key at once (real Postgres ON CONFLICT). Exactly
    one becomes the leader; the rest get 409; exactly one row exists."""
    ws_id, vk_id = _seed_vk()
    n = 20
    start = threading.Barrier(n)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker():
        start.wait()
        with SessionLocal() as db:
            idem = _idem(ws_id, vk_id, db, key="race")
            try:
                idempotency.claim(idem)
                result = "leader" if idem.claimed else "no-claim"
            except idempotency.HTTPException as e:
                result = f"http:{e.status_code}"
            except Exception as e:  # noqa: BLE001
                result = f"err:{type(e).__name__}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("leader") == 1, outcomes
    assert outcomes.count("http:409") == n - 1, outcomes
    with SessionLocal() as s:
        rows = s.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == "race")
        ).all()
        assert len(rows) == 1


def test_streaming_guard_blocks_then_releases():
    ws_id, vk_id = _seed_vk()

    class _VK:  # claim_streaming reads only .id and .workspace_id
        id = vk_id
        workspace_id = ws_id

    with SessionLocal() as db:
        i1 = idempotency.claim_streaming(
            db, _VK(), header="s1", endpoint="/x:stream", provider="openai", model="m", body_material={"a": 1}
        )
        assert i1.claimed

    with SessionLocal() as db, pytest.raises(idempotency.HTTPException) as ei:
        idempotency.claim_streaming(
            db, _VK(), header="s1", endpoint="/x:stream", provider="openai", model="m", body_material={"a": 1}
        )
    assert ei.value.status_code == 409

    idempotency.release_detached(i1.record_id)

    with SessionLocal() as db:
        i3 = idempotency.claim_streaming(
            db, _VK(), header="s1", endpoint="/x:stream", provider="openai", model="m", body_material={"a": 1}
        )
        assert i3.claimed  # lock was freed


def test_stale_in_progress_is_reclaimed():
    """A claim stuck in_progress past the inflight timeout (a crashed leader) is
    reclaimed by the next request rather than blocking it forever."""
    ws_id, vk_id = _seed_vk()
    with SessionLocal() as db:
        first = _idem(ws_id, vk_id, db, key="stale")
        idempotency.claim(first)
        assert first.claimed
        # backdate it well past the reclaim window
        from datetime import UTC, datetime, timedelta

        old = datetime.now(UTC) - timedelta(seconds=settings.IDEMPOTENCY_INFLIGHT_TIMEOUT_SECONDS + 60)
        db.execute(
            IdempotencyRecord.__table__.update()
            .where(IdempotencyRecord.id == first.record_id)
            .values(created_at=old)
        )
        db.commit()

    with SessionLocal() as db:
        second = _idem(ws_id, vk_id, db, key="stale")
        idempotency.claim(second)  # must NOT raise 409
        assert second.claimed
