from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.gateway import period_spend
from app.models import AllowedProvider, UsageLog, VirtualKey
from app.providers import SUPPORTED
from app.schemas import KeyCreate, KeyCreated, KeyOut, KeyUpdate
from app.security import new_key, require_admin

# Whole router is admin-only. The public dashboard never touches these endpoints;
# it reads /api/usage/* instead.
router = APIRouter(
    prefix="/api/keys", tags=["keys"], dependencies=[Depends(require_admin)]
)


def _validate_providers(names: list[str]) -> list[str]:
    providers = sorted(set(names))
    unknown = [p for p in providers if p not in SUPPORTED]
    if unknown:
        raise HTTPException(422, f"unknown providers: {unknown}")
    return providers


def _budget(v) -> float | None:
    return float(v) if v is not None else None


@router.post("", response_model=KeyCreated)
def create_key(body: KeyCreate, db: Session = Depends(get_db)) -> KeyCreated:
    label = body.label.strip()
    if not label:
        raise HTTPException(422, "label is required")
    providers = _validate_providers(body.allowed_providers)
    if body.default_provider and body.default_provider not in providers:
        raise HTTPException(422, "default_provider must be one of allowed_providers")

    raw, hashed, prefix = new_key()
    vk = VirtualKey(
        label=label,
        key_hash=hashed,
        key_prefix=prefix,
        allow_live=body.allow_live,
        default_provider=body.default_provider,
        monthly_budget_usd=body.monthly_budget_usd,
        budget_period=body.budget_period,
        rpm_limit=body.rpm_limit,
        expires_at=(
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days
            else None
        ),
        allowed_providers=[AllowedProvider(provider=p) for p in providers],
    )
    db.add(vk)
    db.commit()
    db.refresh(vk)
    return KeyCreated(
        id=vk.id,
        label=vk.label,
        key=raw,  # shown exactly once
        key_prefix=vk.key_prefix,
        allowed_providers=vk.provider_names(),
        allow_live=vk.allow_live,
        default_provider=vk.default_provider,
        monthly_budget_usd=_budget(vk.monthly_budget_usd),
        budget_period=vk.budget_period,
        rpm_limit=vk.rpm_limit,
        expires_at=vk.expires_at,
        created_at=vk.created_at,
    )


@router.get("", response_model=list[KeyOut])
def list_keys(db: Session = Depends(get_db)) -> list[KeyOut]:
    rollup = {
        row[0]: (row[1], int(row[2]), float(row[3]))
        for row in db.execute(
            select(
                UsageLog.key_id,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0),
            ).group_by(UsageLog.key_id)
        ).all()
    }
    keys = db.scalars(
        select(VirtualKey).order_by(VirtualKey.created_at.desc())
    ).all()
    out: list[KeyOut] = []
    for k in keys:
        requests, total_tokens, cost = rollup.get(k.id, (0, 0, 0.0))
        out.append(
            KeyOut(
                id=k.id,
                label=k.label,
                key_prefix=k.key_prefix,
                allowed_providers=k.provider_names(),
                allow_live=k.allow_live,
                default_provider=k.default_provider,
                monthly_budget_usd=_budget(k.monthly_budget_usd),
                budget_period=k.budget_period,
                rpm_limit=k.rpm_limit,
                expires_at=k.expires_at,
                spend_period=period_spend(db, k),
                created_at=k.created_at,
                last_used_at=k.last_used_at,
                revoked_at=k.revoked_at,
                requests=requests,
                total_tokens=total_tokens,
                cost=cost,
            )
        )
    return out


@router.patch("/{key_id}")
def update_key(key_id: int, body: KeyUpdate, db: Session = Depends(get_db)) -> dict:
    vk = db.get(VirtualKey, key_id)
    if vk is None:
        raise HTTPException(404, "key not found")
    if body.allow_live is not None:
        vk.allow_live = body.allow_live
    if body.default_provider is not None:
        if body.default_provider and body.default_provider not in vk.provider_names():
            raise HTTPException(422, "default_provider must be one of allowed_providers")
        vk.default_provider = body.default_provider or None
    if body.budget_period is not None:
        vk.budget_period = body.budget_period
    if body.clear_budget:
        vk.monthly_budget_usd = None
    elif body.monthly_budget_usd is not None:
        vk.monthly_budget_usd = body.monthly_budget_usd
    if body.clear_rpm_limit:
        vk.rpm_limit = None
    elif body.rpm_limit is not None:
        vk.rpm_limit = body.rpm_limit
    if body.clear_expiry:
        vk.expires_at = None
    elif body.expires_in_days is not None:
        vk.expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)
    db.commit()
    return {
        "id": vk.id,
        "allow_live": vk.allow_live,
        "default_provider": vk.default_provider,
        "monthly_budget_usd": _budget(vk.monthly_budget_usd),
        "budget_period": vk.budget_period,
        "rpm_limit": vk.rpm_limit,
        "expires_at": vk.expires_at.isoformat() if vk.expires_at else None,
    }


@router.delete("/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_db)) -> dict:
    vk = db.get(VirtualKey, key_id)
    if vk is None:
        raise HTTPException(404, "key not found")
    if vk.revoked_at is None:
        vk.revoked_at = datetime.now(UTC)
        db.commit()
    return {"id": vk.id, "revoked_at": vk.revoked_at}
