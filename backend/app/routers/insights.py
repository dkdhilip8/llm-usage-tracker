"""Insights API — anomaly alerts + investigation, scoped to the viewer (demo data
when logged out, your own when logged in, all for admin). Plus an admin-only
demo helper that injects a synthetic usage spike."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import insights
from app.db import get_db
from app.scoping import Viewer, viewer
from app.security import require_admin

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("/alerts")
def alerts(db: Session = Depends(get_db), v: Viewer = Depends(viewer)) -> dict:
    return {"alerts": insights.list_alerts(db, v.user_id)}


@router.get("/alerts/{alert_id}")
def investigate(
    alert_id: str, db: Session = Depends(get_db), v: Viewer = Depends(viewer)
) -> dict:
    result = insights.investigate(db, alert_id, v.user_id)
    if result is None:
        raise HTTPException(404, "alert not found or no longer anomalous")
    return result


@router.post("/demo-spike", dependencies=[Depends(require_admin)])
def demo_spike(db: Session = Depends(get_db)) -> dict:
    return insights.inject_demo_spike(db)
