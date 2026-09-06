"""Symmetric encryption for provider credentials stored in the DB.

Fernet = AES-128-CBC + HMAC-SHA256 (authenticated). The key comes from
ENCRYPTION_KEY when set, otherwise it is derived deterministically from
SECRET_KEY so the feature works with zero extra config. Rotating either value
makes existing ciphertexts undecryptable — the admin just re-enters the keys.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY.strip()
    if not key:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key)  # raises ValueError if ENCRYPTION_KEY is malformed


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
