"""Per-account live mode.

A signed-in user attaches their own provider API key(s) here (encrypted at rest,
shown back only as ``····last4``) and sets a monthly live-spend cap. Their
virtual keys can then be flagged *allow live* and will make real upstream calls
billed to that key, stopping at the cap. They hand the raw ``vk_...`` strings to
whoever needs them — recipients need no account.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.crypto import encrypt
from app.db import get_db
from app.gateway import live_spend_this_month
from app.models import ProviderCredential, User
from app.schemas import AccountOut, AccountPatch, AccountProviderStatus, ProviderKeyIn
from app.security import require_user

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
            )
        )
    return out


def _account_out(db: Session, user: User) -> AccountOut:
    return AccountOut(
        email=user.email,
        is_admin=user.is_admin,
        can_live=user.is_admin or _has_any_key(db, user.id),
        live_cap_usd=float(user.live_cap_usd) if user.live_cap_usd is not None else None,
        live_cap_default_usd=float(settings.LIVE_CAP_DEFAULT_USD),
        live_cap_max_usd=float(settings.LIVE_CAP_MAX_USD),
        live_spend_this_month=round(live_spend_this_month(db, user.id), 4),
        providers=_provider_view(db, user.id),
    )


@router.get("", response_model=AccountOut)
def get_account(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> AccountOut:
    return _account_out(db, user)


@router.patch("", response_model=AccountOut)
def patch_account(
    body: AccountPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> AccountOut:
    if body.live_cap_usd is not None:
        user.live_cap_usd = min(body.live_cap_usd, settings.LIVE_CAP_MAX_USD)
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
