"""Instance-wide provider status (server env vars). Read-only, Workspace Admin
only. A workspace attaches its *own* provider keys under /api/workspace/providers."""

from fastapi import APIRouter, Depends

from app import providers
from app.config import settings
from app.schemas import ProviderStatus
from app.workspace import require_workspace_admin

router = APIRouter(
    prefix="/api", tags=["providers"], dependencies=[Depends(require_workspace_admin)]
)


@router.get("/providers", response_model=list[ProviderStatus])
def list_providers() -> list[dict]:
    """Which providers are configured instance-wide via a server env var. Never
    returns key values."""
    return [
        {
            "provider": p,
            "env_var": providers.ENV_VARS[p],
            "configured_via_env": bool(settings.provider_api_key(p)),
        }
        for p in providers.SUPPORTED
    ]
