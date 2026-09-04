"""Admin-only demo-data controls for the shared public deployment."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import demo
from app.db import get_db
from app.security import require_admin

router = APIRouter(
    prefix="/api/demo", tags=["demo"], dependencies=[Depends(require_admin)]
)


@router.post("/reset")
def reset(db: Session = Depends(get_db)) -> dict:
    """Wipe every key + usage row and rebuild the deterministic demo dataset."""
    return demo.reset_demo_data(db)
