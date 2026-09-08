"""The signed-in user's own account — identity and self-deletion. Everything
workspace-scoped (keys, provider credentials, usage) lives under /api/workspace
and /api/keys."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, Workspace
from app.schemas import AccountOut, WorkspaceRef
from app.security import SESSION_COOKIE, require_user

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("", response_model=AccountOut)
def get_account(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> AccountOut:
    ws = None
    if user.workspace_id is not None:
        w = db.get(Workspace, user.workspace_id)
        if w is not None:
            ws = WorkspaceRef(id=w.id, name=w.name, role=user.workspace_role or "member")
    return AccountOut(
        username=user.username,
        workspace=ws,
        live_cap_default_usd=float(settings.LIVE_CAP_DEFAULT_USD),
    )


@router.delete("")
def delete_my_account(
    resp: Response, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.workspace_id is not None and user.workspace_role == "admin":
        others = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.workspace_id == user.workspace_id, User.id != user.id)
        )
        if (others or 0) > 0:
            raise HTTPException(
                400, "remove all members / delete the workspace before deleting your account"
            )
        # sole admin — take the (now empty) workspace with you
        ws = db.get(Workspace, user.workspace_id)
        user.workspace_id = None
        db.flush()
        if ws is not None:
            db.delete(ws)  # cascades the admin's keys + usage + credentials + invites
    db.delete(user)
    db.commit()
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}
