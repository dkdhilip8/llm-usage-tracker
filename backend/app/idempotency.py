"""Postgres-backed, fail-closed idempotency for billable provider calls.

A client sends ``Idempotency-Key: <token>`` on a POST. The gateway remembers,
per virtual key, the (key -> completed response) mapping, so a retried request
replays the stored response instead of calling — and re-billing — the provider
again.

Design (see the approved audit):

* **Postgres only, fail CLOSED.** This is spend protection, the opposite of the
  rate limiter's fail-open Redis. The claim is durable, is transactional with
  the ``usage_logs`` write, and elects a single leader via
  ``UNIQUE(key_id, idempotency_key)``.
* **Response bodies are Fernet-encrypted at rest**, never plaintext, and only up
  to ``settings.IDEMPOTENCY_MAX_REPLAY_BYTES``. A larger response is recorded
  terminal-but-not-replayable so the key still can't drive a second billable
  call, but a retry gets an honest 409 rather than a silently-wrong empty body.
* **Claim placement.** ``begin()`` is a read that runs *after* auth + provider/
  model authorization but *before* the quota/budget/cap gates, so a free replay
  is never blocked by a since-exhausted budget. ``claim()`` is the write, run
  *after* all governance and immediately before the provider call, so a
  governance-rejected request never leaves a claim that would block a genuine
  later retry.
* **Transient vs deterministic failure.** A deterministic provider 4xx is cached
  terminal and replayed; a transient 429/5xx/transport failure RELEASES the
  claim so a genuine retry can run — a transient failure never poisons a key.
* **Streaming: concurrency guard only** (``claim_streaming`` / ``release``). We
  never buffer or replay a stream; a second identical stream while one is in
  flight gets 409.
* **Upstream forwarding is intentionally NOT done here.** Gateway-side
  idempotency closes the client->gateway retry window. It cannot by itself
  resolve an ambiguous gateway->provider *transport* failure: when no response
  comes back we can't know whether the provider billed, so we release and allow
  a retry, accepting that a retry could double-charge in that one narrow window.
  Forwarding the key to providers that honor it (unverified for Anthropic/
  Gemini) is a deliberately deferred later slice.
"""

import base64
import hashlib
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app import crypto
from app.config import settings
from app.models import IdempotencyRecord, VirtualKey

logger = logging.getLogger("app.idempotency")

# A generous upper bound on the header value. Long enough for a UUID/ULID or a
# provider SDK's own key, short enough to reject junk. Not configurable on
# purpose — it's an input-validation guard, not a tuning knob.
_MAX_KEY_LEN = 255


class IdempotencyReplay(Exception):
    """Short-circuit signal carrying a previously stored response. Rendered by a
    dedicated handler in app.main into the exact stored status/body/content-type,
    so a replay is byte-identical to the original regardless of which funnel
    produced it."""

    def __init__(self, status_code: int, content: bytes, content_type: str | None) -> None:
        self.status_code = status_code
        self.content = content
        self.content_type = content_type or "application/json"
        super().__init__("idempotent replay")


@dataclass
class Idem:
    """Per-request idempotency context threaded through a funnel. ``enabled`` is
    False when the request sent no Idempotency-Key, in which case every function
    here is a no-op and behavior is exactly as before this feature existed."""

    enabled: bool = False
    db: Session | None = None
    key_id: int | None = None
    workspace_id: int | None = None
    key: str | None = None
    fingerprint: str | None = None
    endpoint: str = ""
    provider: str = ""
    model: str = ""
    record_id: int | None = None
    claimed: bool = False


# ---- time helpers ----
def _now() -> datetime:
    return datetime.now(UTC)


def _ttl() -> timedelta:
    return timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS)


def _stale_cutoff() -> datetime:
    return _now() - timedelta(seconds=settings.IDEMPOTENCY_INFLIGHT_TIMEOUT_SECONDS)


# ---- fingerprinting ----
def _canonical(material) -> str:
    """A stable JSON encoding of the request payload — key order and whitespace
    can't change the fingerprint."""
    return json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(endpoint: str, provider: str, model: str, body_material, *, extra: bytes = b"") -> str:
    """SHA-256 over the full semantic request: endpoint + provider + model +
    canonical body, plus ``extra`` raw bytes for a multipart file upload."""
    h = hashlib.sha256()
    for part in (endpoint, provider, model):
        h.update(part.encode())
        h.update(b"\0")
    h.update(_canonical(body_material).encode())
    if extra:
        h.update(b"\0")
        h.update(extra)
    return h.hexdigest()


# ---- key validation ----
def _validate_key(raw: str) -> str:
    key = (raw or "").strip()
    if not key:
        raise HTTPException(
            status_code=400,
            detail={"message": "Idempotency-Key must not be empty", "type": "invalid_idempotency_key"},
        )
    if len(key) > _MAX_KEY_LEN:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Idempotency-Key exceeds the {_MAX_KEY_LEN}-character maximum",
                "type": "invalid_idempotency_key",
            },
        )
    return key


# ---- body encryption ----
def _encrypt_body(body: bytes) -> str:
    return crypto.encrypt(base64.b64encode(body).decode())


def _decrypt_body(enc: str) -> bytes | None:
    s = crypto.decrypt(enc)
    if s is None:
        return None
    try:
        return base64.b64decode(s)
    except (ValueError, TypeError):
        return None


# ---- row helpers ----
def _lookup(db: Session, key_id: int, key: str) -> IdempotencyRecord | None:
    return db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.key_id == key_id,
            IdempotencyRecord.idempotency_key == key,
        )
    )


def _expired(row: IdempotencyRecord) -> bool:
    return row.expires_at <= _now()


def _stale_inprogress(row: IdempotencyRecord) -> bool:
    return row.status == "in_progress" and row.created_at <= _stale_cutoff()


def _reusable(row: IdempotencyRecord) -> bool:
    """A row that no longer represents a live request — safe to overwrite."""
    return _expired(row) or _stale_inprogress(row)


def _check_fingerprint(row: IdempotencyRecord, fp: str) -> None:
    if row.request_fingerprint != fp:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Idempotency-Key was already used with a different request payload",
                "type": "idempotency_key_reused",
            },
        )


def _in_progress_409() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": "a request with this Idempotency-Key is already in progress",
            "type": "idempotency_in_progress",
        },
    )


def _replay(row: IdempotencyRecord) -> IdempotencyReplay:
    """Build the replay signal for a terminal row, or raise 409 when the original
    response was too large to store (never a silent empty replay)."""
    if not row.replayable or row.response_body_encrypted is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "the original response exceeded the idempotency replay size limit and "
                    "cannot be replayed; this key will not be retried against the provider"
                ),
                "type": "idempotency_not_replayable",
            },
        )
    body = _decrypt_body(row.response_body_encrypted)
    if body is None:
        # Encryption key rotated since storage, or corrupt token — can't replay,
        # and re-calling would double-charge, so refuse rather than guess.
        raise HTTPException(
            status_code=409,
            detail={
                "message": "the stored idempotent response is unavailable (encryption key rotated?)",
                "type": "idempotency_replay_unavailable",
            },
        )
    return _replay_signal(row, body)


def _replay_signal(row: IdempotencyRecord, body: bytes) -> IdempotencyReplay:
    return IdempotencyReplay(row.response_status or 200, body, row.response_content_type)


# ---- public API ----
def begin(
    db: Session,
    vk: VirtualKey,
    *,
    header: str | None,
    endpoint: str,
    provider: str,
    model: str,
    body_material,
    extra: bytes = b"",
) -> Idem:
    """Read-only pre-governance check. Returns a disabled Idem when no header was
    sent (behavior unchanged). Otherwise raises IdempotencyReplay for a stored
    terminal result, 422 for a fingerprint mismatch, or 409 for a live in-flight
    duplicate; returns an enabled (unclaimed) Idem when the request is new."""
    if header is None:
        return Idem(enabled=False)
    key = _validate_key(header)
    fp = fingerprint(endpoint, provider, model, body_material, extra=extra)

    if random.random() < 0.02:  # opportunistic, best-effort TTL cleanup off the hot path
        _cleanup_expired(db)

    row = _lookup(db, vk.id, key)
    if row is not None and not _reusable(row):
        _check_fingerprint(row, fp)
        if row.status in ("completed", "failed"):
            raise _replay(row)
        raise _in_progress_409()  # live in_progress

    return Idem(
        enabled=True,
        db=db,
        key_id=vk.id,
        workspace_id=vk.workspace_id,
        key=key,
        fingerprint=fp,
        endpoint=endpoint,
        provider=provider,
        model=model,
    )


def claim(idem: Idem) -> None:
    """Elect this request as the single leader for its key, immediately before
    the provider call. First-writer-wins via ON CONFLICT DO NOTHING; a loser that
    finds a terminal row replays it, a live in-flight duplicate gets 409, and an
    expired/stale row is atomically reclaimed."""
    if not idem.enabled:
        return
    _claim(idem, replay_terminal=True)


def claim_streaming(
    db: Session,
    vk: VirtualKey,
    *,
    header: str | None,
    endpoint: str,
    provider: str,
    model: str,
    body_material,
) -> Idem:
    """Concurrency guard for streaming endpoints — no replay, ever. A second
    identical request while one is in flight gets 409; terminal/expired/stale
    rows are reclaimed (a completed record must not block a fresh stream). Call
    release() when the stream ends."""
    if header is None:
        return Idem(enabled=False)
    key = _validate_key(header)
    fp = fingerprint(endpoint, provider, model, body_material)
    idem = Idem(
        enabled=True,
        db=db,
        key_id=vk.id,
        workspace_id=vk.workspace_id,
        key=key,
        fingerprint=fp,
        endpoint=endpoint,
        provider=provider,
        model=model,
    )
    _claim(idem, replay_terminal=False)
    return idem


def _claim(idem: Idem, *, replay_terminal: bool) -> None:
    db = idem.db
    now = _now()
    exp = now + _ttl()
    insert_stmt = (
        pg_insert(IdempotencyRecord)
        .values(
            key_id=idem.key_id,
            workspace_id=idem.workspace_id,
            idempotency_key=idem.key,
            request_fingerprint=idem.fingerprint,
            endpoint=idem.endpoint,
            provider=idem.provider,
            model=idem.model,
            status="in_progress",
            replayable=False,
            created_at=now,
            updated_at=now,
            expires_at=exp,
        )
        .on_conflict_do_nothing(index_elements=["key_id", "idempotency_key"])
        .returning(IdempotencyRecord.id)
    )

    for _attempt in range(2):
        new_id = db.execute(insert_stmt).scalar()
        db.commit()
        if new_id is not None:
            idem.record_id = new_id
            idem.claimed = True
            return

        row = _lookup(db, idem.key_id, idem.key)
        if row is None:
            continue  # deleted between conflict and read — retry the insert once

        # A live row with a different payload is always a reuse error first.
        if not _reusable(row):
            _check_fingerprint(row, idem.fingerprint)
            if row.status in ("completed", "failed"):
                if replay_terminal:
                    raise _replay(row)
                # streaming: a terminal record doesn't block a new stream — reclaim
            else:
                raise _in_progress_409()

        # Reclaim an expired / stale / (streaming) terminal row atomically.
        conditions = [
            IdempotencyRecord.expires_at <= now,
            and_(IdempotencyRecord.status == "in_progress", IdempotencyRecord.created_at <= _stale_cutoff()),
        ]
        if not replay_terminal:
            conditions.append(IdempotencyRecord.status.in_(("completed", "failed")))
        stolen = db.execute(
            update(IdempotencyRecord)
            .where(IdempotencyRecord.id == row.id, or_(*conditions))
            .values(
                status="in_progress",
                replayable=False,
                request_fingerprint=idem.fingerprint,
                endpoint=idem.endpoint,
                provider=idem.provider,
                model=idem.model,
                response_status=None,
                response_content_type=None,
                response_body_encrypted=None,
                usage_log_id=None,
                created_at=now,
                updated_at=now,
                expires_at=exp,
            )
        )
        db.commit()
        if stolen.rowcount == 1:
            idem.record_id = row.id
            idem.claimed = True
            return
        # Lost the reclaim race — loop re-reads and resolves against the winner.

    raise _in_progress_409()


def finalize(
    idem: Idem,
    *,
    status_code: int,
    content_type: str | None,
    body: bytes,
    terminal_status: str = "completed",
    usage_log_id: int | None = None,
) -> None:
    """Record the terminal outcome for a claimed key. A body within the size cap
    is encrypted and made replayable; a larger one is stored terminal-but-not-
    replayable (body NULL) so the key can't re-bill but a retry gets an honest
    409 instead of an empty replay."""
    if not idem.enabled or not idem.claimed:
        return
    replayable = len(body) <= settings.IDEMPOTENCY_MAX_REPLAY_BYTES
    enc = _encrypt_body(body) if replayable else None
    idem.db.execute(
        update(IdempotencyRecord)
        .where(IdempotencyRecord.id == idem.record_id)
        .values(
            status=terminal_status,
            replayable=replayable,
            response_status=status_code,
            response_content_type=content_type,
            response_body_encrypted=enc,
            usage_log_id=usage_log_id,
            updated_at=_now(),
        )
    )
    idem.db.commit()


def finalize_json(idem: Idem, obj, *, usage_log_id: int | None = None, status_code: int = 200) -> None:
    """finalize() for a JSON response object. Serialized to match Starlette's
    JSONResponse byte-for-byte (compact separators, non-ASCII preserved), so a
    replay is identical to the original response the client would have received."""
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    finalize(
        idem,
        status_code=status_code,
        content_type="application/json",
        body=body,
        terminal_status="completed",
        usage_log_id=usage_log_id,
    )


def finalize_or_release_http_error(idem: Idem, exc: HTTPException) -> None:
    """For the run_completion-based routers (/v1/proxy/chat and the legacy
    translated /v1/chat/completions), whose provider errors surface as a
    gateway-shaped HTTPException rather than a raw UpstreamHTTPError.

    Caches ONLY a deterministic provider 4xx (``type == "upstream_error"``, non-
    429) as a terminal replayable result, reproducing FastAPI's ``{"detail": …}``
    envelope. Everything else releases the claim so a genuine retry can run: a
    transient 429/5xx/transport failure, and — critically — any gateway
    governance rejection that could still surface here in a rare race (paused
    key, no provider key, open breaker), which must never be cached (decision 6
    and 10). The routers already run require_live_ready before the claim, so this
    is defense-in-depth for that race, not the primary guard."""
    if not idem.enabled or not idem.claimed:
        return
    status = exc.status_code
    detail = exc.detail
    dtype = detail.get("type") if isinstance(detail, dict) else None
    is_provider_error = dtype == "upstream_error"
    if is_provider_error and 400 <= status < 500 and status != 429:
        finalize(
            idem,
            status_code=status,
            content_type="application/json",
            body=json.dumps({"detail": detail}, default=str).encode(),
            terminal_status="failed",
        )
    else:
        release(idem)


def release(idem: Idem) -> None:
    """Drop a claim so a genuine retry can run — for a transient provider failure
    (429/5xx/transport) or the end of a streamed request."""
    if not idem.enabled or not idem.claimed:
        return
    idem.db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == idem.record_id))
    idem.db.commit()
    idem.claimed = False


def release_detached(record_id: int | None) -> None:
    """release() from a context whose request session is already gone (the
    streaming path), opening its own short-lived session."""
    if record_id is None:
        return
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id == record_id))
            db.commit()
    except Exception:  # noqa: BLE001 — best-effort cleanup; TTL/stale reclaim is the backstop
        logger.warning("idempotency: detached release failed for record %s", record_id, exc_info=True)


# ---- TTL cleanup ----
def _cleanup_expired(db: Session, limit: int = 500) -> int:
    try:
        ids = db.scalars(
            select(IdempotencyRecord.id).where(IdempotencyRecord.expires_at <= _now()).limit(limit)
        ).all()
        if not ids:
            return 0
        db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.id.in_(ids)))
        db.commit()
        return len(ids)
    except Exception:  # noqa: BLE001 — cleanup must never break a request
        db.rollback()
        return 0


def cleanup_expired_startup() -> None:
    """Sweep expired rows once at boot. Best-effort; never blocks startup."""
    from app.db import SessionLocal

    try:
        with SessionLocal() as db:
            removed = 0
            while True:
                n = _cleanup_expired(db, limit=1000)
                removed += n
                if n < 1000:
                    break
            if removed:
                logger.info("idempotency: cleaned up %d expired record(s) at startup", removed)
    except Exception:  # noqa: BLE001
        logger.warning("idempotency: startup cleanup failed", exc_info=True)
