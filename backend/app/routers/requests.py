"""Request log — recent `usage_logs` rows for the Requests tab, scoped to the
viewer (demo data when logged out, your own when logged in, all for admin).

Prompt/response previews are only populated when LOG_BODIES=true, off on the
public deploy."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import UsageLog, VirtualKey
from app.scoping import Viewer, scope_usage, viewer

router = APIRouter(prefix="/api", tags=["requests"])


@router.get("/requests")
def list_requests(
    limit: int = 50,
    cursor: int | None = None,  # return rows with id < cursor
    provider: str | None = None,
    model: str | None = None,
    key_id: int | None = None,
    status: str | None = None,
    mode: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
    v: Viewer = Depends(viewer),
) -> dict:
    limit = max(1, min(limit, 200))
    stmt: Select = scope_usage(
        select(UsageLog, VirtualKey.label, VirtualKey.key_prefix)
        .join(VirtualKey, VirtualKey.id == UsageLog.key_id)
        .order_by(UsageLog.id.desc())
        .limit(limit + 1),
        v.user_id,
    )
    if cursor is not None:
        stmt = stmt.where(UsageLog.id < cursor)
    if start is not None:
        stmt = stmt.where(UsageLog.ts >= start)
    if end is not None:
        stmt = stmt.where(UsageLog.ts < end)
    if provider:
        stmt = stmt.where(UsageLog.provider == provider)
    if model:
        stmt = stmt.where(UsageLog.model == model)
    if key_id is not None:
        stmt = stmt.where(UsageLog.key_id == key_id)
    if status:
        stmt = stmt.where(UsageLog.status == status)
    if mode:
        stmt = stmt.where(UsageLog.mode == mode)

    rows = db.execute(stmt).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [
        {
            "id": u.id,
            "ts": u.ts.isoformat(),
            "request_id": u.request_id,
            "key_id": u.key_id,
            "key_label": label,
            "key_prefix": prefix,
            "provider": u.provider,
            "model": u.model,
            "mode": u.mode,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "total_tokens": u.total_tokens,
            "cost": float(u.cost),
            "cost_source": u.cost_source,
            "latency_ms": u.latency_ms,
            "status": u.status,
            "prompt_preview": u.prompt_preview,
            "response_preview": u.response_preview,
        }
        for u, label, prefix in rows
    ]
    return {
        "items": items,
        "next_cursor": items[-1]["id"] if (items and has_more) else None,
        "bodies_logged": settings.LOG_BODIES,
    }
