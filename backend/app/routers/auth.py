"""Auth: signup + login, session cookie, logout, and `/me`. Username + password —
no email, no password reset yet."""

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, Workspace
from app.security import (
    SESSION_COOKIE,
    current_user,
    hash_password,
    issue_session,
    verify_password,
)

# 3–32 chars, alphanumeric plus . _ - inside, must start and end alphanumeric.
_USERNAME_RE = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{1,30}[A-Za-z0-9])$"

router = APIRouter(prefix="/api/auth", tags=["auth"])

# in-process signup throttle (per client IP) — best-effort, single instance
_signups: dict[str, deque[float]] = defaultdict(deque)


class Credentials(BaseModel):
    username: str = Field(pattern=_USERNAME_RE, max_length=40)
    password: str = Field(min_length=8, max_length=200)


class LoginBody(BaseModel):
    username: str
    password: str


def _set_cookie(resp: Response, user_id: int) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        issue_session(user_id),
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.ENVIRONMENT.strip().lower() != "development",
        path="/",
    )


def _me(db: Session, user: User | None) -> dict:
    if user is None:
        return {"authenticated": False, "user": None}
    workspace = None
    if user.workspace_id is not None:
        ws = db.get(Workspace, user.workspace_id)
        if ws is not None:
            workspace = {
                "id": ws.id,
                "name": ws.name,
                "role": user.workspace_role or "member",
            }
    return {
        "authenticated": True,
        "user": {
            "id": user.id,
            "username": user.username,
            "workspace": workspace,
        },
    }


def _throttle_signup(ip: str) -> None:
    now = time.time()
    q = _signups[ip]
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= settings.SIGNUPS_PER_IP_PER_HOUR:
        raise HTTPException(429, "too many signups from this address — try later")
    q.append(now)


@router.post("/signup")
def signup(body: Credentials, request: Request, resp: Response, db: Session = Depends(get_db)) -> dict:
    if not settings.ALLOW_SIGNUP:
        raise HTTPException(403, "signup is disabled")
    username = body.username.strip().lower()
    _throttle_signup(request.client.host if request.client else "?")

    account_count = db.scalar(select(func.count()).select_from(User))
    if (account_count or 0) >= settings.MAX_USERS:
        raise HTTPException(503, "sign-ups are currently closed — try again later")
    if db.scalar(select(User.id).where(func.lower(User.username) == username)):
        raise HTTPException(409, "that username is taken")

    user = User(username=username, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    _set_cookie(resp, user.id)
    return _me(db, user)


@router.post("/login")
def login(body: LoginBody, resp: Response, db: Session = Depends(get_db)) -> dict:
    ident = body.username.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.username) == ident))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid username or password")
    _set_cookie(resp, user.id)
    return _me(db, user)


@router.post("/logout")
def logout(resp: Response) -> dict:
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False, "user": None}


@router.get("/me")
def me(
    user: User | None = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    return _me(db, user)
