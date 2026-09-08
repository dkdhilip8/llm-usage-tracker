"""Workspace membership — the auth dependencies that gate every workspace-scoped
route, plus a couple of small shared helpers.

`require_membership`  -> signed in AND in a workspace (Team Member or Admin).
`require_workspace_admin` -> the above AND role == "admin".
"""

import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException

from app.models import User
from app.security import require_user

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


@dataclass
class Membership:
    workspace_id: int
    role: str  # "admin" | "member"
    user_id: int

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


def require_membership(user: User = Depends(require_user)) -> Membership:
    if user.workspace_id is None or user.workspace_role is None:
        raise HTTPException(status_code=403, detail="join or create a workspace first")
    return Membership(user.workspace_id, user.workspace_role, user.id)


def require_workspace_admin(m: Membership = Depends(require_membership)) -> Membership:
    if not m.is_admin:
        raise HTTPException(status_code=403, detail="workspace admin only")
    return m


def new_invite_code() -> str:
    return secrets.token_urlsafe(9)
