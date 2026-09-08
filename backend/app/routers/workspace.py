"""Workspace lifecycle: create / rename / delete, single-use invites, member
management, and the workspace's shared (encrypted) provider keys + spend caps.

Every route here is workspace-scoped. Reads that list members / invites /
provider status are Workspace Admin only; a Team Member only ever gets
``{id, name, role}``.
"""

import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import providers
from app.config import settings
from app.crypto import encrypt
from app.db import get_db
from app.gateway import live_spend_this_month
from app.models import (
    ProviderCredential,
    UsageLog,
    User,
    VirtualKey,
    Workspace,
    WorkspaceInvite,
)
from app.security import require_user
from app.workspace import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    Membership,
    new_invite_code,
    require_membership,
    require_workspace_admin,
)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# in-process per-IP join throttle (best-effort, single instance)
_joins: dict[str, deque[float]] = defaultdict(deque)


def _throttle_join(ip: str) -> None:
    now = time.time()
    q = _joins[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= settings.JOINS_PER_IP_PER_HOUR:
        raise HTTPException(429, "too many join attempts from this address — try later")
    q.append(now)


# ---- bodies ----
class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class JoinBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class InviteBody(BaseModel):
    label: str | None = Field(default=None, max_length=80)


class ProviderKeyBody(BaseModel):
    api_key: str = Field(min_length=8, max_length=400)


class ProviderCapBody(BaseModel):
    monthly_cap_usd: float | None = Field(default=None, ge=0)  # null => the default


# ---- views ----
def _provider_view(db: Session, workspace_id: int) -> list[dict]:
    rows = {
        r.provider: r
        for r in db.scalars(
            select(ProviderCredential).where(
                ProviderCredential.workspace_id == workspace_id
            )
        )
    }
    out = []
    for p in providers.SUPPORTED:
        env = bool(settings.provider_api_key(p))
        row = rows.get(p)
        out.append(
            {
                "provider": p,
                "configured": env or row is not None,
                "source": "env" if env else ("workspace" if row else "none"),
                "last4": row.last4 if (row and not env) else None,
                "monthly_cap_usd": (
                    float(row.monthly_cap_usd)
                    if (row and row.monthly_cap_usd is not None)
                    else None
                ),
                "spend_this_month": round(live_spend_this_month(db, workspace_id, p), 4),
            }
        )
    return out


def _members_view(db: Session, workspace_id: int) -> list[dict]:
    members = list(
        db.scalars(
            select(User)
            .where(User.workspace_id == workspace_id)
            .order_by(User.created_at)
        )
    )
    usage = {
        row[0]: (row[1], float(row[2]))
        for row in db.execute(
            select(
                VirtualKey.assigned_user_id,
                func.count(UsageLog.id),
                func.coalesce(func.sum(UsageLog.cost), 0),
            )
            .join(UsageLog, UsageLog.key_id == VirtualKey.id)
            .where(
                VirtualKey.workspace_id == workspace_id,
                VirtualKey.assigned_user_id.isnot(None),
            )
            .group_by(VirtualKey.assigned_user_id)
        )
    }
    key_counts = {
        row[0]: row[1]
        for row in db.execute(
            select(VirtualKey.assigned_user_id, func.count())
            .where(
                VirtualKey.workspace_id == workspace_id,
                VirtualKey.assigned_user_id.isnot(None),
            )
            .group_by(VirtualKey.assigned_user_id)
        )
    }
    out = []
    for u in members:
        reqs, cost = usage.get(u.id, (0, 0.0))
        out.append(
            {
                "user_id": u.id,
                "username": u.username,
                "role": u.workspace_role,
                "assigned_keys": key_counts.get(u.id, 0),
                "requests": reqs,
                "cost": round(cost, 4),
            }
        )
    return out


def _open_invites(db: Session, workspace_id: int) -> list[dict]:
    now = datetime.now(UTC)
    rows = db.scalars(
        select(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == workspace_id,
            WorkspaceInvite.accepted_at.is_(None),
            WorkspaceInvite.expires_at > now,
        )
        .order_by(WorkspaceInvite.created_at.desc())
    )
    return [
        {
            "id": r.id,
            "code": r.code,
            "label": r.label,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
        }
        for r in rows
    ]


def _payload(db: Session, m: Membership) -> dict:
    ws = db.get(Workspace, m.workspace_id)
    body = {"id": ws.id, "name": ws.name, "role": m.role}
    if m.is_admin:
        body["members"] = _members_view(db, ws.id)
        body["invites"] = _open_invites(db, ws.id)
        body["providers"] = _provider_view(db, ws.id)
    return body


def _member_count(db: Session, workspace_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.workspace_id == workspace_id)
    ) or 0


# ---- lifecycle ----
@router.get("")
def get_workspace(
    db: Session = Depends(get_db), m: Membership = Depends(require_membership)
) -> dict:
    return _payload(db, m)


@router.post("")
def create_workspace(
    body: CreateBody, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.workspace_id is not None:
        raise HTTPException(409, "you are already in a workspace")
    count = db.scalar(select(func.count()).select_from(Workspace)) or 0
    if count >= settings.MAX_WORKSPACES:
        raise HTTPException(503, "workspace limit reached — try again later")
    ws = Workspace(name=body.name.strip())
    db.add(ws)
    db.flush()
    user.workspace_id = ws.id
    user.workspace_role = ROLE_ADMIN
    db.commit()
    return _payload(db, Membership(ws.id, ROLE_ADMIN, user.id))


@router.patch("")
def rename_workspace(
    body: RenameBody, db: Session = Depends(get_db), m: Membership = Depends(require_workspace_admin)
) -> dict:
    ws = db.get(Workspace, m.workspace_id)
    ws.name = body.name.strip()
    db.commit()
    return _payload(db, m)


@router.delete("")
def delete_workspace(
    db: Session = Depends(get_db), m: Membership = Depends(require_workspace_admin)
) -> dict:
    if _member_count(db, m.workspace_id) > 1:
        raise HTTPException(409, "remove all other members before deleting the workspace")
    user = db.get(User, m.user_id)
    user.workspace_id = None
    user.workspace_role = None
    db.flush()
    ws = db.get(Workspace, m.workspace_id)
    db.delete(ws)  # cascades virtual_keys -> usage_logs, provider_credentials, invites
    db.commit()
    return {"deleted": True}


@router.post("/leave")
def leave_workspace(
    db: Session = Depends(get_db), m: Membership = Depends(require_membership)
) -> dict:
    if m.is_admin:
        raise HTTPException(400, "the admin must delete the workspace instead of leaving")
    db.execute(
        update(VirtualKey)
        .where(
            VirtualKey.workspace_id == m.workspace_id,
            VirtualKey.assigned_user_id == m.user_id,
        )
        .values(assigned_user_id=None)
    )
    user = db.get(User, m.user_id)
    user.workspace_id = None
    user.workspace_role = None
    db.commit()
    return {"left": True}


# ---- invites ----
@router.post("/invites")
def create_invite(
    body: InviteBody,
    db: Session = Depends(get_db),
    m: Membership = Depends(require_workspace_admin),
) -> dict:
    open_count = db.scalar(
        select(func.count())
        .select_from(WorkspaceInvite)
        .where(
            WorkspaceInvite.workspace_id == m.workspace_id,
            WorkspaceInvite.accepted_at.is_(None),
            WorkspaceInvite.expires_at > datetime.now(UTC),
        )
    ) or 0
    if open_count >= settings.MAX_OPEN_INVITES_PER_WORKSPACE:
        raise HTTPException(409, "too many open invites — revoke some first")
    inv = WorkspaceInvite(
        workspace_id=m.workspace_id,
        code=new_invite_code(),
        label=(body.label or None),
        expires_at=datetime.now(UTC) + timedelta(days=settings.INVITE_TTL_DAYS),
    )
    db.add(inv)
    db.commit()
    return {
        "id": inv.id,
        "code": inv.code,
        "label": inv.label,
        "expires_at": inv.expires_at.isoformat(),
    }


@router.delete("/invites/{invite_id}")
def revoke_invite(
    invite_id: int,
    db: Session = Depends(get_db),
    m: Membership = Depends(require_workspace_admin),
) -> dict:
    inv = db.get(WorkspaceInvite, invite_id)
    if inv is None or inv.workspace_id != m.workspace_id or inv.accepted_at is not None:
        raise HTTPException(404, "invite not found")
    db.delete(inv)
    db.commit()
    return {"revoked": True}


@router.post("/join")
def join_workspace(
    body: JoinBody,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
) -> dict:
    if user.workspace_id is not None:
        raise HTTPException(409, "you are already in a workspace")
    _throttle_join(request.client.host if request.client else "?")

    inv = db.scalar(
        select(WorkspaceInvite).where(WorkspaceInvite.code == body.code.strip())
    )
    if inv is None or inv.accepted_at is not None or inv.expires_at <= datetime.now(UTC):
        raise HTTPException(404, "invalid or expired invite code")
    if _member_count(db, inv.workspace_id) >= settings.MAX_MEMBERS_PER_WORKSPACE:
        raise HTTPException(409, "this workspace is full")

    # atomic single-use consume — lose the race => the code was just used
    consumed = db.execute(
        update(WorkspaceInvite)
        .where(WorkspaceInvite.id == inv.id, WorkspaceInvite.accepted_at.is_(None))
        .values(accepted_at=datetime.now(UTC), accepted_by_id=user.id)
    )
    if consumed.rowcount != 1:
        raise HTTPException(409, "that invite code was already used")

    user.workspace_id = inv.workspace_id
    user.workspace_role = ROLE_MEMBER
    db.commit()
    return _payload(db, Membership(inv.workspace_id, ROLE_MEMBER, user.id))


# ---- members ----
@router.delete("/members/{member_id}")
def remove_member(
    member_id: int,
    db: Session = Depends(get_db),
    m: Membership = Depends(require_workspace_admin),
) -> dict:
    if member_id == m.user_id:
        raise HTTPException(400, "you cannot remove yourself")
    target = db.get(User, member_id)
    if target is None or target.workspace_id != m.workspace_id:
        raise HTTPException(404, "member not found")
    db.execute(
        update(VirtualKey)
        .where(
            VirtualKey.workspace_id == m.workspace_id,
            VirtualKey.assigned_user_id == member_id,
        )
        .values(assigned_user_id=None)
    )
    target.workspace_id = None
    target.workspace_role = None
    db.commit()
    return {"removed": True}


# ---- shared provider keys + caps (admin only) ----
@router.get("/providers")
def list_providers(
    db: Session = Depends(get_db), m: Membership = Depends(require_workspace_admin)
) -> list[dict]:
    return _provider_view(db, m.workspace_id)


@router.put("/providers/{provider}/key")
def set_provider_key(
    provider: str,
    body: ProviderKeyBody,
    db: Session = Depends(get_db),
    m: Membership = Depends(require_workspace_admin),
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
            ProviderCredential.workspace_id == m.workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    if row is None:
        db.add(
            ProviderCredential(
                workspace_id=m.workspace_id,
                provider=provider,
                ciphertext=encrypt(key),
                last4=key[-4:],
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
    m: Membership = Depends(require_workspace_admin),
) -> dict:
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == m.workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return {"provider": provider}


@router.patch("/providers/{provider}/cap")
def set_provider_cap(
    provider: str,
    body: ProviderCapBody,
    db: Session = Depends(get_db),
    m: Membership = Depends(require_workspace_admin),
) -> list[dict]:
    if provider not in providers.SUPPORTED:
        raise HTTPException(422, f"provider must be one of {list(providers.SUPPORTED)}")
    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == m.workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    if row is None:
        raise HTTPException(404, f"configure a {provider} key before setting its cap")
    row.monthly_cap_usd = body.monthly_cap_usd
    db.commit()
    return _provider_view(db, m.workspace_id)
