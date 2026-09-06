"""Fail closed: a deployed ENVIRONMENT must not run on unsafe defaults."""

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
        "ADMIN_PASSWORD": "",
        "ALLOW_DB_PROVIDER_KEYS": False,
    }
    return Settings(**{**base, **overrides})


# real, deployment-safe values to layer on top of the deployed-env cases
_REAL = {
    "ADMIN_TOKEN": "real-admin-token-value",
    "SECRET_KEY": "real-secret-pepper-value",
    "ADMIN_PASSWORD": "a-strong-admin-password",
}


def test_development_allows_all_defaults():
    s = _mk(ENVIRONMENT="development")
    assert s.ADMIN_TOKEN == "dev-admin-token"
    assert s.SECRET_KEY == "dev-secret"
    assert s.ADMIN_PASSWORD == ""


@pytest.mark.parametrize("env", ["production", "Production", "PROD", " staging "])
def test_deployed_env_rejects_default_admin_token(env):
    with pytest.raises(ValidationError, match="ADMIN_TOKEN"):
        _mk(ENVIRONMENT=env, SECRET_KEY="a-genuine-pepper", ADMIN_PASSWORD="a-strong-admin-password")


def test_deployed_env_rejects_default_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _mk(ENVIRONMENT="production", ADMIN_TOKEN="a-genuine-token", ADMIN_PASSWORD="a-strong-admin-password")


def test_deployed_env_requires_admin_password():
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD must be set"):
        _mk(ENVIRONMENT="production", ADMIN_TOKEN="a-genuine-token", SECRET_KEY="a-genuine-pepper")


def test_deployed_env_rejects_short_admin_password():
    with pytest.raises(ValidationError, match="at least 12"):
        _mk(ENVIRONMENT="production", **{**_REAL, "ADMIN_PASSWORD": "short"})


def test_deployed_env_rejects_db_provider_keys():
    with pytest.raises(ValidationError, match="ALLOW_DB_PROVIDER_KEYS"):
        _mk(ENVIRONMENT="production", ALLOW_DB_PROVIDER_KEYS=True, **_REAL)


def test_deployed_env_accepts_safe_config():
    s = _mk(ENVIRONMENT="production", **_REAL)
    assert s.ENVIRONMENT == "production" and s.ALLOW_DB_PROVIDER_KEYS is False


def test_development_allows_db_provider_keys():
    assert _mk(ENVIRONMENT="development", ALLOW_DB_PROVIDER_KEYS=True).ALLOW_DB_PROVIDER_KEYS is True
