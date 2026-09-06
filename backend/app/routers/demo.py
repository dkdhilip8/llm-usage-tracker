"""Demo-data controls: admin rebuilds the shared demo dataset; any user
regenerates / clears their own sample data or deletes their account."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import demo
from app.db import get_db
from app.models import User
from app.security import SESSION_COOKIE, require_admin, require_user

router = APIRouter(tags=["demo"])


@router.post("/api/demo/reset", dependencies=[Depends(require_admin)])
def reset(db: Session = Depends(get_db)) -> dict:
    """Rebuild the shared demo-user dataset (the logged-out view). Admin only."""
    return demo.reset_demo_data(db)


@router.post("/api/account/sample")
def regenerate_sample(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    """(Re)build the caller's own starter dataset."""
    if user.is_demo:
        raise HTTPException(400, "not for the demo account")
    return demo.seed_user_sample(db, user.id)


@router.delete("/api/account/data")
def clear_my_data(
    db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    """Wipe the caller's keys + usage (keeps the account)."""
    demo._wipe_user(db, user.id)
    db.commit()
    return {"cleared": True}


@router.delete("/api/account")
def delete_my_account(
    resp: Response, db: Session = Depends(get_db), user: User = Depends(require_user)
) -> dict:
    if user.is_admin or user.is_demo:
        raise HTTPException(400, "this account cannot be deleted here")
    db.delete(user)  # cascades keys + usage
    db.commit()
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return {"deleted": True}
