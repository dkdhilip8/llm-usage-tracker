from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.db import get_db
from app.models import ProviderCredential
from app.schemas import ProviderKeyIn, ProviderStatus
from app.security import require_admin

router = APIRouter(
    prefix="/api", tags=["providers"], dependencies=[Depends(require_admin)]
)


@router.get("/providers", response_model=list[ProviderStatus])
def list_providers(refresh: bool = False, db: Session = Depends(get_db)) -> list[dict]:
    """Admin-only. Which provider keys are configured (env var or, when
    ALLOW_DB_PROVIDER_KEYS, an encrypted DB entry) and whether a cheap liveness
    check succeeds. Never returns key values."""
    providers.load_db_keys(db)
    rows = providers.status(force=refresh)
    last4 = {}
    if settings.ALLOW_DB_PROVIDER_KEYS:
        last4 = {r.provider: r.last4 for r in db.scalars(select(ProviderCredential))}
    for r in rows:
        r["last4"] = last4.get(r["provider"]) if r["source"] == "db" else None
    return rows


@router.get("/providers/live-enabled")
def live_enabled() -> dict:
    return {
        "enable_live": settings.ENABLE_LIVE,
        "db_keys_enabled": settings.ALLOW_DB_PROVIDER_KEYS,
    }


def _require_db_keys() -> None:
    if not settings.ALLOW_DB_PROVIDER_KEYS:
        raise HTTPException(404, "DB-stored provider keys are disabled on this deployment")


def _check_provider(provider: str) -> None:
    if provider not in providers.SUPPORTED:
        raise HTTPException(422, f"provider must be one of {list(providers.SUPPORTED)}")


@router.put("/providers/{provider}/key")
def set_provider_key(
    provider: str, body: ProviderKeyIn, db: Session = Depends(get_db)
) -> dict:
    _require_db_keys()
    _check_provider(provider)
    key = body.api_key.strip()
    if not key:
        raise HTTPException(422, "api_key is required")
    if settings.provider_api_key(provider):
        raise HTTPException(
            409, f"{provider} is set via a server env var; that takes precedence"
        )
    last4 = providers.set_db_key(db, provider, key)
    return {
        "provider": provider,
        "source": providers.key_source(provider),
        "last4": last4,
        "valid": providers.check_liveness(provider, force=True),
    }


@router.delete("/providers/{provider}/key")
def clear_provider_key(provider: str, db: Session = Depends(get_db)) -> dict:
    _require_db_keys()
    _check_provider(provider)
    providers.clear_db_key(db, provider)
    return {"provider": provider, "source": providers.key_source(provider)}
