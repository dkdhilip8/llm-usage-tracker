"""Single-admin login: username + password -> signed session cookie.

The X-Admin-Token header stays valid in parallel (curl / SDK / CI). This just
adds a friendlier front door for the Admin UI. No user table, no registration.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.config import settings
from app.security import SESSION_COOKIE, current_admin, issue_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.ENVIRONMENT.strip().lower() != "development",
        path="/",
    )


@router.post("/login")
def login(body: LoginBody, resp: Response) -> dict:
    if not settings.ADMIN_PASSWORD:
        raise HTTPException(503, "password login is not configured on this server")
    ok_user = secrets.compare_digest(body.username, settings.ADMIN_USERNAME)
    ok_pass = secrets.compare_digest(body.password, settings.ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(401, "invalid username or password")
    _set_cookie(resp, issue_session(settings.ADMIN_USERNAME))
    return {"authenticated": True, "username": settings.ADMIN_USERNAME}


@router.post("/logout")
def logout(resp: Response) -> dict:
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/me")
def me(who: str | None = Depends(current_admin)) -> dict:
    return {"authenticated": who is not None, "username": who}
