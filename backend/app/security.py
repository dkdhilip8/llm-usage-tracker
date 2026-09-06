import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import VirtualKey

_KEY_PREFIX_LEN = 11  # "vk_" + 8 chars

SESSION_COOKIE = "llmt_session"


def new_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix). The raw key is shown once, never stored."""
    raw = "vk_" + secrets.token_urlsafe(24)
    return raw, key_hash(raw), raw[:_KEY_PREFIX_LEN]


def key_hash(raw: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


# ---- admin session cookie (stateless, HMAC-signed with SECRET_KEY) ----
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())


def issue_session(username: str) -> str:
    """`<b64(username|expiry)>.<b64(hmac)>` — no server-side session store."""
    payload = f"{username}|{int(time.time()) + settings.SESSION_TTL_HOURS * 3600}"
    token = _b64(payload.encode())
    return f"{token}.{_sign(token)}"


def read_session(cookie: str) -> str | None:
    """Returns the username if the cookie is a valid, unexpired session, else None."""
    if not cookie or "." not in cookie:
        return None
    token, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(token)):
        return None
    try:
        username, exp = _unb64(token).decode().rsplit("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    if not exp.isdigit() or int(exp) < int(time.time()):
        return None
    return username


def _token_ok(x_admin_token: str) -> bool:
    return bool(x_admin_token) and secrets.compare_digest(x_admin_token, settings.ADMIN_TOKEN)


def require_admin(
    x_admin_token: str = Header(default=""),
    session: str = Cookie(default="", alias=SESSION_COOKIE),
) -> None:
    """Admin access via either the X-Admin-Token header or a valid login session."""
    if _token_ok(x_admin_token) or read_session(session) is not None:
        return
    raise HTTPException(status_code=403, detail="admin authentication required")


def current_admin(
    x_admin_token: str = Header(default=""),
    session: str = Cookie(default="", alias=SESSION_COOKIE),
) -> str | None:
    """The signed-in admin username (or 'token' for header auth), else None. Not a gate."""
    if read_session(session) is not None:
        return read_session(session)
    if _token_ok(x_admin_token):
        return "token"
    return None


def require_virtual_key(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> VirtualKey:
    """A valid, non-revoked virtual key is mandatory — this is a gateway."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing 'Authorization: Bearer vk_...'")
    raw = authorization.split(" ", 1)[1].strip()
    vk = db.scalar(
        select(VirtualKey).where(
            VirtualKey.key_hash == key_hash(raw),
            VirtualKey.revoked_at.is_(None),
        )
    )
    if vk is None:
        raise HTTPException(401, "invalid or revoked virtual key")
    return vk
