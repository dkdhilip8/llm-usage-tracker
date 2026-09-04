import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import VirtualKey

_KEY_PREFIX_LEN = 11  # "vk_" + 8 chars


def new_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix). The raw key is shown once, never stored."""
    raw = "vk_" + secrets.token_urlsafe(24)
    return raw, key_hash(raw), raw[:_KEY_PREFIX_LEN]


def key_hash(raw: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()


def require_admin(x_admin_token: str = Header(default="")) -> None:
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="invalid admin token")


def require_virtual_key(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> VirtualKey:
    """A valid, non-revoked, non-expired virtual key is mandatory — this is a gateway."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing 'Authorization: Bearer vk_...'")
    raw = authorization.split(" ", 1)[1].strip()
    vk = db.scalar(
        select(VirtualKey).where(
            VirtualKey.key_hash == key_hash(raw),
            VirtualKey.revoked_at.is_(None),
            or_(VirtualKey.expires_at.is_(None), VirtualKey.expires_at > func.now()),
        )
    )
    if vk is None:
        raise HTTPException(401, "invalid, revoked, or expired virtual key")
    return vk
