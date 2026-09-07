"""Per-account live mode.

A signed-in user attaches their own provider API key(s) here (encrypted at rest,
shown back only as ``····last4``), each with its own monthly live-spend cap.
Their virtual keys then make real upstream calls billed to those keys, stopping
at each provider's cap. They hand the raw ``vk_...`` strings to whoever needs
them — recipients need no account.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.crypto import encrypt
from app.db import get_db
from app.gateway import live_spend_this_month
from app.models import ProviderCredential, User, VirtualKey
from app.schemas import AccountOut, AccountProviderStatus, ProviderCapIn, ProviderKeyIn
from app.security import SESSION_COOKIE, require_user

router = APIRouter(prefix="/api/account", tags=["account"])


def _has_any_key(db: Session, user_id: int) -> bool:
    return (
        db.scalar(select(ProviderCredential.id).where(ProviderCredential.user_id == user_id))
        is not None
    )


def _provider_view(db: Session, user_id: int) -> list[AccountProviderStatus]:
    rows = {
        r.provider: r
        for r in db.scalars(
            select(ProviderCredential).where(ProviderCredential.user_id == user_id)
        )
    }
    out: list[AccountProviderStatus] = []
    for p in providers.SUPPORTED:
        env = bool(settings.provider_api_key(p))
        row = rows.get(p)
        out.append(
            AccountProviderStatus(
                provider=p,
                configured=env or row is not None,
                source="env" if env else ("account" if row else "none"),
                last4=row.last4 if (row and not env) else None,
                monthly_cap_usd=(
                    float(row.monthly_cap_usd)
                    if (row and row.monthly_cap_usd is not None)
                    else None
                ),
                spend_this_month=round(live_spend_this_month(db, user_id, p), 4),
            )
        )
    return out


def _account_out(db: Session, user: User) -> AccountOut:
    return AccountOut(
        username=user.username,
        is_admin=user.is_admin,
        can_live=user.is_admin or _has_any_key(db, user.id),
        live_cap_default_usd=float(settings.LIVE_CAP_DEFAULT_USD),
        providers=_provider_view(db, user.id),
    )


@router.get("", response_model=AccountOut)
def get_account(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> AccountOut:
    return _account_out(db, user)


@router.patch("/providers/{provider}/cap", response_model=AccountOut)
def set_provider_cap(
    provider: str,
    body: ProviderCapIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> AccountOut:
    if provider not in providers.SUPPORTED:
        raise HTTPException(422, f"provider must be one of {list(providers.SUPPORTED)}")
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.user_id == user.id,
            ProviderCredential.provider == provider,
        )
    )
    if row is None:
        raise HTTPException(404, f"configure a {provider} key before setting its cap")
    row.monthly_cap_usd = body.monthly_cap_usd  # None clears it -> the default applies
    db.commit()
    return _account_out(db, user)


@router.put("/providers/{provider}/key")
def set_provider_key(
    provider: str,
    body: ProviderKeyIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    if provider not in providers.SUPPORTED:
        raise HTTPException(422, f"provider must be one of {list(providers.SUPPORTED)}")
    if settings.provider_api_key(provider):
        raise HTTPException(
            409, f"{provider} is set via a server env var; that takes precedence"
        )
    key = body.api_key.strip()
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.user_id == user.id,
            ProviderCredential.provider == provider,
        )
    )
    if row is None:
        db.add(
            ProviderCredential(
                user_id=user.id, provider=provider, ciphertext=encrypt(key), last4=key[-4:]
            )
        )
    else:
        row.ciphertext, row.last4 = encrypt(key), key[-4:]
    db.commit()
    return {"provider": provider, "last4": key[-4:], "valid": providers.check_key(provider, key)}


@router.delete("/providers/{provider}/key")
def clear_provider_key(
    provider: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.user_id == user.id,
            ProviderCredential.provider == provider,
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return {"provider": provider}


@router.delete("/data")
def clear_my_data(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    """Wipe the caller's virtual keys + usage (keeps the account)."""
    # ON DELETE CASCADE on virtual_keys drops the user's usage_logs + allowed_providers
    db.query(VirtualKey).filter(VirtualKey.user_id == user.id).delete(
        synchronize_session=False
    )
    db.commit()
    return {"cleared": True}


@router.delete("")
def delete_my_account(
    resp: Response, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.is_admin:
        raise HTTPException(400, "the admin account cannot be deleted here")
    db.delete(user)  # cascades keys + usage
    db.commit()
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}
