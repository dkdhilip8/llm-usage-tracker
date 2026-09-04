"""Admin-only Insights API — anomaly alerts, investigation drill-down, and a
demo helper that injects a synthetic usage spike."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import insights
from app.db import get_db
from app.security import require_admin

router = APIRouter(
    prefix="/api/insights", tags=["insights"], dependencies=[Depends(require_admin)]
)


@router.get("/alerts")
def alerts(db: Session = Depends(get_db)) -> dict:
    return {"alerts": insights.list_alerts(db)}


@router.get("/alerts/{alert_id}")
def investigate(alert_id: str, db: Session = Depends(get_db)) -> dict:
    result = insights.investigate(db, alert_id)
    if result is None:
        raise HTTPException(404, "alert not found or no longer anomalous")
    return result


@router.post("/demo-spike")
def demo_spike(db: Session = Depends(get_db)) -> dict:
    return insights.inject_demo_spike(db)
