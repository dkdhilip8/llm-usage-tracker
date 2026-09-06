"""Fail closed: a deployed ENVIRONMENT must not run on the built-in dev secrets."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _mk(**overrides) -> Settings:
    # Explicit kwargs are the highest-priority settings source, so this is
    # isolated from whatever the test process has in os.environ / .env.
    base = {
        "ENVIRONMENT": "development",
        "ADMIN_TOKEN": "dev-admin-token",
        "SECRET_KEY": "dev-secret",
    }
    return Settings(**{**base, **overrides})


def test_development_allows_dev_defaults():
    s = _mk(ENVIRONMENT="development")
    assert s.ADMIN_TOKEN == "dev-admin-token" and s.SECRET_KEY == "dev-secret"


@pytest.mark.parametrize("env", ["production", "Production", "PROD", " staging "])
def test_deployed_env_rejects_default_admin_token(env):
    with pytest.raises(ValidationError, match="ADMIN_TOKEN"):
        _mk(ENVIRONMENT=env, SECRET_KEY="a-genuine-pepper-value")


def test_deployed_env_rejects_default_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _mk(ENVIRONMENT="production", ADMIN_TOKEN="a-genuine-admin-value")


def test_deployed_env_rejects_both_defaults():
    with pytest.raises(ValidationError) as exc:
        _mk(ENVIRONMENT="production")
    msg = str(exc.value)
    assert "ADMIN_TOKEN" in msg and "SECRET_KEY" in msg


def test_deployed_env_accepts_real_secrets():
    s = _mk(
        ENVIRONMENT="production",
        ADMIN_TOKEN="real-admin-token-value",
        SECRET_KEY="real-secret-pepper-value",
    )
    assert s.ENVIRONMENT == "production"
