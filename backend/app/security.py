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
from app.models import User, VirtualKey

_KEY_PREFIX_LEN = 11  # "vk_" + 8 chars

SESSION_COOKIE = "llmt_session"

# scrypt params — ~16 MB, fine for a login form.
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


def new_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix). The raw key is shown once, never stored."""
    raw = "vk_" + secrets.token_urlsafe(24)
    return raw, key_hash(raw), raw[:_KEY_PREFIX_LEN]


def key_hash(raw: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


# ---- password hashing (stdlib scrypt) ----
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, dk_hex = stored.split("$", 1)
    try:
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **_SCRYPT
        )
    except ValueError:
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


# ---- session cookie (stateless, HMAC-signed with SECRET_KEY) ----
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: str) -> str:
    return _b64(hmac.new(settings.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())


def issue_session(user_id: int) -> str:
    """`<b64(user_id|expiry)>.<b64(hmac)>` — no server-side session store."""
    payload = f"{user_id}|{int(time.time()) + settings.SESSION_TTL_HOURS * 3600}"
    token = _b64(payload.encode())
    return f"{token}.{_sign(token)}"


def read_session(cookie: str) -> int | None:
    """Returns the user id if the cookie is a valid, unexpired session, else None."""
    if not cookie or "." not in cookie:
        return None
    token, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(token)):
        return None
    try:
        uid, exp = _unb64(token).decode().split("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    if not (uid.isdigit() and exp.isdigit()) or int(exp) < int(time.time()):
        return None
    return int(uid)


def _token_ok(x_admin_token: str) -> bool:
    return bool(x_admin_token) and secrets.compare_digest(x_admin_token, settings.ADMIN_TOKEN)


# ---- principal resolution ----
def current_user(
    x_admin_token: str = Header(default=""),
    session: str = Cookie(default="", alias=SESSION_COOKIE),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in account, or None. Not a gate. The X-Admin-Token header
    resolves to the admin user row."""
    if _token_ok(x_admin_token):
        return db.scalar(select(User).where(User.is_admin.is_(True)))
    uid = read_session(session)
    if uid is not None:
        return db.get(User, uid)
    return None


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user


def require_admin(user: User | None = Depends(current_user)) -> User:
    if user is None or not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


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
