"""Workspaces — a team gateway. The owner sets the shared provider key(s), a
monthly spend cap, and a join code; members create their own virtual keys that
make live calls on the owner's key without ever seeing it."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.crypto import encrypt
from app.db import get_db
from app.models import ProviderCredential, UsageLog, User, VirtualKey, Workspace
from app.security import require_user
from app.workspace import member_ids, new_join_code, spend_this_month

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class JoinBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class PatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    monthly_cap_usd: float | None = Field(default=None, ge=0)


class KeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=400)


def _ws(db: Session, user: User) -> Workspace | None:
    return db.get(Workspace, user.workspace_id) if user.workspace_id else None


def _require_owner(db: Session, user: User) -> Workspace:
    ws = _ws(db, user)
    if ws is None or ws.owner_id != user.id:
        raise HTTPException(403, "workspace owner only")
    return ws


def _detach(db: Session, user_id: int) -> None:
    """Remove a member: drop their keys+usage and clear their workspace."""
    db.query(VirtualKey).filter(VirtualKey.user_id == user_id).delete(synchronize_session=False)
    u = db.get(User, user_id)
    if u is not None:
        u.workspace_id = None
    db.flush()


def _provider_view(db: Session, ws: Workspace, *, owner: bool) -> list[dict]:
    rows = {
        r.provider: r
        for r in db.scalars(
            select(ProviderCredential).where(ProviderCredential.workspace_id == ws.id)
        )
    }
    out = []
    for p in providers.SUPPORTED:
        r = rows.get(p)
        out.append(
            {
                "provider": p,
                "configured": r is not None or bool(settings.provider_api_key(p)),
                "source": "env" if settings.provider_api_key(p) else ("db" if r else "none"),
                "last4": (r.last4 if (r and owner) else None),
            }
        )
    return out


@router.get("")
def get_workspace(db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    ws = _ws(db, user)
    if ws is None:
        return {"workspace": None}
    owner = ws.owner_id == user.id
    ids = member_ids(db, ws.id)
    body = {
        "name": ws.name,
        "is_owner": owner,
        "member_count": len(ids),
        "monthly_cap_usd": float(ws.monthly_cap_usd),
        "cap_max_usd": settings.WORKSPACE_CAP_MAX_USD,
        "spend_this_month": round(spend_this_month(db, ws.id), 4),
        "providers": _provider_view(db, ws, owner=owner),
    }
    if owner:
        body["join_code"] = ws.join_code
        by_user = {
            row[0]: (row[1], float(row[2]))
            for row in db.execute(
                select(UsageLog.key_id, func.count(), func.coalesce(func.sum(UsageLog.cost), 0))
                .join(VirtualKey, VirtualKey.id == UsageLog.key_id)
                .where(VirtualKey.user_id.in_(ids))
                .group_by(UsageLog.key_id)
            )
        }
        key_owner = {k.id: k.user_id for k in db.scalars(select(VirtualKey).where(VirtualKey.user_id.in_(ids)))}
        agg: dict[int, list[float]] = {i: [0, 0.0] for i in ids}
        for key_id, (reqs, cost) in by_user.items():
            uid = key_owner.get(key_id)
            if uid in agg:
                agg[uid][0] += reqs
                agg[uid][1] += cost
        emails = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(ids)))}
        body["members"] = [
            {
                "user_id": i,
                "email": emails.get(i, "?"),
                "is_owner": i == ws.owner_id,
                "requests": agg[i][0],
                "cost": round(agg[i][1], 4),
            }
            for i in ids
        ]
    return body


@router.post("")
def create_workspace(
    body: CreateBody, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if not settings.ALLOW_WORKSPACES:
        raise HTTPException(403, "workspaces are disabled")
    if user.workspace_id is not None:
        raise HTTPException(409, "you are already in a workspace")
    if user.is_demo:
        raise HTTPException(400, "not for the demo account")
    count = db.scalar(select(func.count()).select_from(Workspace))
    if (count or 0) >= settings.MAX_WORKSPACES:
        raise HTTPException(503, "the demo is at its workspace limit")
    ws = Workspace(
        owner_id=user.id,
        name=body.name.strip(),
        join_code=new_join_code(),
        monthly_cap_usd=settings.WORKSPACE_DEFAULT_CAP_USD,
    )
    db.add(ws)
    db.flush()
    user.workspace_id = ws.id
    db.commit()
    return get_workspace(db, user)


@router.post("/join")
def join_workspace(
    body: JoinBody, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.workspace_id is not None:
        raise HTTPException(409, "you are already in a workspace")
    if user.is_demo or user.is_admin:
        raise HTTPException(400, "this account cannot join a workspace")
    ws = db.scalar(select(Workspace).where(Workspace.join_code == body.code.strip()))
    if ws is None:
        raise HTTPException(404, "invalid join code")
    if len(member_ids(db, ws.id)) >= settings.MAX_WORKSPACE_MEMBERS + 1:
        raise HTTPException(409, "this workspace is full")
    user.workspace_id = ws.id
    db.commit()
    return get_workspace(db, user)


@router.post("/leave")
def leave_workspace(db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    ws = _ws(db, user)
    if ws is None:
        raise HTTPException(400, "you are not in a workspace")
    if ws.owner_id == user.id:
        raise HTTPException(400, "the owner must delete the workspace instead")
    _detach(db, user.id)
    db.commit()
    return {"left": True}


@router.patch("")
def patch_workspace(
    body: PatchBody, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    ws = _require_owner(db, user)
    if body.name is not None:
        ws.name = body.name.strip()
    if body.monthly_cap_usd is not None:
        ws.monthly_cap_usd = max(0.0, min(body.monthly_cap_usd, settings.WORKSPACE_CAP_MAX_USD))
    db.commit()
    return get_workspace(db, user)


@router.delete("")
def delete_workspace(db: Session = Depends(get_db), user: User = Depends(require_user)) -> dict:
    ws = _require_owner(db, user)
    for uid in list(member_ids(db, ws.id)):
        if uid != user.id:
            _detach(db, uid)
    user.workspace_id = None
    db.flush()
    db.delete(ws)  # cascades provider_credentials
    db.commit()
    return {"deleted": True}


@router.delete("/members/{member_id}")
def remove_member(
    member_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    ws = _require_owner(db, user)
    m = db.get(User, member_id)
    if m is None or m.workspace_id != ws.id or m.id == ws.owner_id:
        raise HTTPException(404, "member not found")
    _detach(db, member_id)
    db.commit()
    return {"removed": True}


@router.put("/providers/{provider}/key")
def set_key(
    provider: str, body: KeyBody, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    ws = _require_owner(db, user)
    if provider not in providers.SUPPORTED:
        raise HTTPException(422, f"provider must be one of {list(providers.SUPPORTED)}")
    key = body.api_key.strip()
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == ws.id, ProviderCredential.provider == provider
        )
    )
    if row is None:
        db.add(
            ProviderCredential(
                workspace_id=ws.id, provider=provider, ciphertext=encrypt(key), last4=key[-4:]
            )
        )
    else:
        row.ciphertext, row.last4 = encrypt(key), key[-4:]
    db.commit()
    return {"provider": provider, "last4": key[-4:], "valid": providers.check_key(provider, key)}


@router.delete("/providers/{provider}/key")
def clear_key(
    provider: str, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    ws = _require_owner(db, user)
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == ws.id, ProviderCredential.provider == provider
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return {"provider": provider}
