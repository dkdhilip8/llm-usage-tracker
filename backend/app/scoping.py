"""Who-can-see-what for the read endpoints.

Logged out       -> the demo account's data.
Member (or solo) -> only their own keys + usage.
Workspace owner  -> the whole workspace (owner + all members).
Admin            -> everything (`user_ids` is None).
"""

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.bootstrap import demo_user_id
from app.db import get_db
from app.models import UsageLog, User, VirtualKey, Workspace
from app.security import current_user
from app.workspace import member_ids


@dataclass
class Viewer:
    user_ids: list[int] | None  # scope to these owners; None = all (admin)
    authenticated: bool
    is_admin: bool
    is_owner: bool
    email: str | None


def viewer(
    user: User | None = Depends(current_user), db: Session = Depends(get_db)
) -> Viewer:
    if user is None:
        return Viewer([demo_user_id(db)], False, False, False, None)
    if user.is_admin:
        return Viewer(None, True, True, False, user.email)
    ws = db.get(Workspace, user.workspace_id) if user.workspace_id else None
    if ws is not None and ws.owner_id == user.id:
        return Viewer(member_ids(db, ws.id) or [user.id], True, False, True, user.email)
    return Viewer([user.id], True, False, False, user.email)


def scope_usage(stmt: Select, user_ids: list[int] | None) -> Select:
    if user_ids is None:
        return stmt
    return stmt.where(
        UsageLog.key_id.in_(
            select(VirtualKey.id).where(VirtualKey.user_id.in_(user_ids))
        )
    )
