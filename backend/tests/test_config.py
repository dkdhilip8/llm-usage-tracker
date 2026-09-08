"""Fail closed: a deployed ENVIRONMENT must not run on the built-in dev secret."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _mk(**overrides) -> Settings:
    base = {"ENVIRONMENT": "development", "SECRET_KEY": "dev-secret"}
    return Settings(**{**base, **overrides})


def test_development_allows_the_default_secret():
    assert _mk().SECRET_KEY == "dev-secret"


@pytest.mark.parametrize("env", ["production", "Production", "PROD", " staging "])
def test_deployed_env_rejects_default_secret_key(env):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _mk(ENVIRONMENT=env)


def test_deployed_env_accepts_a_real_secret():
    s = _mk(ENVIRONMENT="production", SECRET_KEY="a-genuine-pepper-value", ENCRYPTION_KEY="x")
    assert s.ENVIRONMENT == "production"


def test_deployed_env_warns_when_encryption_key_derived(recwarn):
    _mk(ENVIRONMENT="production", SECRET_KEY="a-genuine-pepper-value")
    assert any("ENCRYPTION_KEY" in str(w.message) for w in recwarn.list)
