"""Who-can-see-what for the read endpoints.

Logged out  -> the demo account's data.
Regular user -> only their own keys + usage.
Admin        -> everything (user_id filter is None).
"""

from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.bootstrap import demo_user_id
from app.db import get_db
from app.models import UsageLog, User, VirtualKey
from app.security import current_user


@dataclass
class Viewer:
    user_id: int | None  # scope usage/keys to this user; None = all (admin)
    authenticated: bool
    is_admin: bool
    email: str | None


def viewer(
    user: User | None = Depends(current_user), db: Session = Depends(get_db)
) -> Viewer:
    if user is None:
        return Viewer(demo_user_id(db), False, False, None)
    if user.is_admin:
        return Viewer(None, True, True, user.email)
    return Viewer(user.id, True, False, user.email)


def scope_usage(stmt: Select, user_id: int | None) -> Select:
    if user_id is None:
        return stmt
    return stmt.where(
        UsageLog.key_id.in_(select(VirtualKey.id).where(VirtualKey.user_id == user_id))
    )
