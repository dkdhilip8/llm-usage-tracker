"""Provider-key encryption roundtrip (Fernet)."""

from app.crypto import decrypt, encrypt


def test_encrypt_roundtrip():
    ct = encrypt("sk-or-v1-secret-value")
    assert ct != "sk-or-v1-secret-value"
    assert decrypt(ct) == "sk-or-v1-secret-value"
    assert decrypt("not-a-valid-token") is None
