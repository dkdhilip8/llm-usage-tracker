from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.gateway import period_spend
from app.models import AllowedProvider, ProviderCredential, UsageLog, User, VirtualKey
from app.providers import SUPPORTED
from app.schemas import KeyCreate, KeyCreated, KeyOut, KeyUpdate
from app.security import new_key, require_user

# Any signed-in user manages their own keys; admin sees/edits everyone's.
router = APIRouter(prefix="/api/keys", tags=["keys"])


def _visible_user_ids(db: Session, user: User) -> list[int] | None:
    """User ids whose keys `user` may see/manage. None = all (admin)."""
    return None if user.is_admin else [user.id]


def _live_allowed(db: Session, user: User) -> bool:
    """A key may make live calls only if the owner is admin or has attached at
    least one of their own provider API keys."""
    if user.is_admin:
        return True
    return (
        db.scalar(
            select(ProviderCredential.id).where(ProviderCredential.user_id == user.id)
        )
        is not None
    )


def _validate_providers(names: list[str]) -> list[str]:
    providers = sorted(set(names))
    unknown = [p for p in providers if p not in SUPPORTED]
    if unknown:
        raise HTTPException(422, f"unknown providers: {unknown}")
    return providers


def _budget(v) -> float | None:
    return float(v) if v is not None else None


def _custom_window(body) -> tuple[datetime, datetime]:
    """Validate a custom budget range and return (start, exclusive_end) as UTC datetimes.
    The end date is inclusive, so it is stored as start-of-next-day."""
    if not (body.budget_start and body.budget_end):
        raise HTTPException(422, "custom budget period requires budget_start and budget_end")
    if body.budget_end < body.budget_start:
        raise HTTPException(422, "budget_end must be on or after budget_start")
    start = datetime.combine(body.budget_start, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(body.budget_end, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    return start, end


def _owned(db: Session, key_id: int, user: User) -> VirtualKey:
    vk = db.get(VirtualKey, key_id)
    if vk is None:
        raise HTTPException(404, "key not found")
    visible = _visible_user_ids(db, user)
    if visible is not None and vk.user_id not in visible:
        raise HTTPException(404, "key not found")  # don't reveal other users' key ids
    return vk


@router.post("", response_model=KeyCreated)
def create_key(
    body: KeyCreate, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> KeyCreated:
    label = body.label.strip()
    if not label:
        raise HTTPException(422, "label is required")
    if not user.is_admin:
        owned = db.scalar(
            select(func.count()).select_from(VirtualKey).where(VirtualKey.user_id == user.id)
        )
        if (owned or 0) >= settings.MAX_KEYS_PER_USER:
            raise HTTPException(
                409, f"key limit reached ({settings.MAX_KEYS_PER_USER} per account)"
            )
    providers = _validate_providers(body.allowed_providers)
    if body.default_provider and body.default_provider not in providers:
        raise HTTPException(422, "default_provider must be one of allowed_providers")

    b_start = b_end = None
    if body.budget_period == "custom":
        b_start, b_end = _custom_window(body)

    raw, hashed, prefix = new_key()
    vk = VirtualKey(
        user_id=user.id,
        label=label,
        key_hash=hashed,
        key_prefix=prefix,
        # live mode: admin, or an account that has attached its own provider key
        # (call-time still checks the key exists + the account is under its cap)
        allow_live=bool(body.allow_live) and _live_allowed(db, user),
        default_provider=body.default_provider,
        monthly_budget_usd=body.monthly_budget_usd,
        budget_period=body.budget_period,
        budget_start=b_start,
        budget_end=b_end,
        allowed_providers=[AllowedProvider(provider=p) for p in providers],
    )
    db.add(vk)
    db.commit()
    db.refresh(vk)
    return _created(vk, raw)


@router.get("", response_model=list[KeyOut])
def list_keys(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> list[KeyOut]:
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
    visible = _visible_user_ids(db, user)
    stmt = select(VirtualKey).order_by(VirtualKey.created_at.desc())
    if visible is not None:
        stmt = stmt.where(VirtualKey.user_id.in_(visible))
    keys = db.scalars(stmt).all()
    # only admin sees more than one account's keys, so only admin needs owner emails
    multi = user.is_admin
    emails = {u.id: u.email for u in db.scalars(select(User))} if multi else {}
    out: list[KeyOut] = []
    for k in keys:
        requests, total_tokens, cost = rollup.get(k.id, (0, 0, 0.0))
        out.append(
            KeyOut(
                id=k.id,
                label=k.label,
                key_prefix=k.key_prefix,
                owner_email=emails.get(k.user_id) if multi else None,
                allowed_providers=k.provider_names(),
                allow_live=k.allow_live,
                default_provider=k.default_provider,
                monthly_budget_usd=_budget(k.monthly_budget_usd),
                budget_period=k.budget_period,
                budget_start=k.budget_start,
                budget_end=k.budget_end,
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
def update_key(
    key_id: int, body: KeyUpdate, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    vk = _owned(db, key_id, user)
    if body.allow_live is not None:
        vk.allow_live = bool(body.allow_live) and _live_allowed(db, user)
    if body.default_provider is not None:
        if body.default_provider and body.default_provider not in vk.provider_names():
            raise HTTPException(422, "default_provider must be one of allowed_providers")
        vk.default_provider = body.default_provider or None
    if body.budget_period is not None:
        vk.budget_period = body.budget_period
        if body.budget_period == "custom":
            vk.budget_start, vk.budget_end = _custom_window(body)
        else:
            vk.budget_start = vk.budget_end = None
    if body.clear_budget:
        vk.monthly_budget_usd = None
    elif body.monthly_budget_usd is not None:
        vk.monthly_budget_usd = body.monthly_budget_usd
    db.commit()
    return {
        "id": vk.id,
        "allow_live": vk.allow_live,
        "default_provider": vk.default_provider,
        "monthly_budget_usd": _budget(vk.monthly_budget_usd),
        "budget_period": vk.budget_period,
        "budget_start": vk.budget_start.isoformat() if vk.budget_start else None,
        "budget_end": vk.budget_end.isoformat() if vk.budget_end else None,
    }


@router.delete("/{key_id}")
def revoke_key(
    key_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    vk = _owned(db, key_id, user)
    if vk.revoked_at is None:
        vk.revoked_at = datetime.now(UTC)
        db.commit()
    return {"id": vk.id, "revoked_at": vk.revoked_at}


def _created(vk: VirtualKey, raw: str) -> KeyCreated:
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
        budget_start=vk.budget_start,
        budget_end=vk.budget_end,
        created_at=vk.created_at,
    )
