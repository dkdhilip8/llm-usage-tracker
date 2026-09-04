from fastapi import APIRouter, Depends

from app import providers
from app.config import settings
from app.schemas import ProviderStatus
from app.security import require_admin

router = APIRouter(prefix="/api", tags=["providers"])


@router.get(
    "/providers",
    response_model=list[ProviderStatus],
    dependencies=[Depends(require_admin)],
)
def list_providers(refresh: bool = False) -> list[dict]:
    """Admin-only. Reports which provider env keys are configured and whether a
    cheap liveness check against the provider succeeds. Never returns key values."""
    return providers.status(force=refresh)


@router.get("/providers/live-enabled", dependencies=[Depends(require_admin)])
def live_enabled() -> dict:
    return {"enable_live": settings.ENABLE_LIVE}
