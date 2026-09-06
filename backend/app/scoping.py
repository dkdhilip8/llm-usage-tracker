"""Who-can-see-what for the read endpoints. Sign-in is required.

Signed in -> only your own keys + usage.
Admin     -> everything (`user_id` is None).
"""

from dataclasses import dataclass

from fastapi import Depends, HTTPException
from sqlalchemy import Select, select

from app.models import UsageLog, User, VirtualKey
from app.security import current_user


@dataclass
class Viewer:
    user_id: int | None  # scope to this owner; None = all (admin)
    is_admin: bool


def viewer(user: User | None = Depends(current_user)) -> Viewer:
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    if user.is_admin:
        return Viewer(None, True)
    return Viewer(user.id, False)


def scope_usage(stmt: Select, user_id: int | None) -> Select:
    if user_id is None:
        return stmt
    return stmt.where(
        UsageLog.key_id.in_(
            select(VirtualKey.id).where(VirtualKey.user_id == user_id)
        )
    )
