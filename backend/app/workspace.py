"""Workspace helpers — membership, monthly spend, and the encrypted provider key
a workspace shares with its members' live calls."""

import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ProviderCredential, UsageLog, User, VirtualKey, Workspace


def new_join_code() -> str:
    return secrets.token_urlsafe(6)


def member_ids(db: Session, workspace_id: int) -> list[int]:
    return list(db.scalars(select(User.id).where(User.workspace_id == workspace_id)))


def spend_this_month(db: Session, workspace_id: int) -> float:
    """Sum of `mode='live'` cost for the workspace's keys since the 1st of the month."""
    val = db.scalar(
        select(func.coalesce(func.sum(UsageLog.cost), 0)).where(
            UsageLog.mode == "live",
            UsageLog.ts >= func.date_trunc("month", func.now()),
            UsageLog.key_id.in_(
                select(VirtualKey.id).where(
                    VirtualKey.user_id.in_(
                        select(User.id).where(User.workspace_id == workspace_id)
                    )
                )
            ),
        )
    )
    return float(val or 0)


def provider_key(db: Session, workspace_id: int, provider: str) -> str | None:
    from app.crypto import decrypt

    row = db.scalar(
        select(ProviderCredential).where(
            ProviderCredential.workspace_id == workspace_id,
            ProviderCredential.provider == provider,
        )
    )
    return decrypt(row.ciphertext) if row else None


def workspace_for_key(db: Session, vk: VirtualKey) -> Workspace | None:
    if vk.user_id is None:
        return None
    u = db.get(User, vk.user_id)
    if u is None or u.workspace_id is None:
        return None
    return db.get(Workspace, u.workspace_id)
