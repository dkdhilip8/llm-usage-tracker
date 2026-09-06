from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import UsageLog, VirtualKey
from app.pricing import models_catalog
from app.scoping import Viewer, scope_usage, viewer

router = APIRouter(prefix="/api", tags=["usage"])

_actual = func.coalesce(
    func.sum(case((UsageLog.cost_source == "provider", UsageLog.cost), else_=0)), 0
)
_estimated = func.coalesce(
    func.sum(case((UsageLog.cost_source != "provider", UsageLog.cost), else_=0)), 0
)


def _apply_filters(
    stmt: Select,
    start: datetime | None,
    end: datetime | None,
    provider: str | None,
    model: str | None,
    key_id: int | None,
) -> Select:
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
    return stmt


@router.get("/models")
def get_models() -> list[dict]:
    return models_catalog()


@router.get("/usage/summary")
def usage_summary(
    start: datetime | None = None,
    end: datetime | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_id: int | None = None,
    db: Session = Depends(get_db),
    v: Viewer = Depends(viewer),
) -> dict:
    stmt = scope_usage(
        _apply_filters(
            select(
                func.count().label("total_requests"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0),
                _actual,
                _estimated,
                func.percentile_cont(0.5).within_group(UsageLog.latency_ms.asc()),
                func.percentile_cont(0.95).within_group(UsageLog.latency_ms.asc()),
                func.coalesce(
                    func.sum(case((UsageLog.status == "error", 1), else_=0)), 0
                ),
                func.coalesce(func.sum(UsageLog.latency_ms), 0),
            ),
            start,
            end,
            provider,
            model,
            key_id,
        ),
        v.user_ids,
    )
    row = db.execute(stmt).one()
    ak_stmt = select(func.count()).select_from(VirtualKey).where(VirtualKey.revoked_at.is_(None))
    if v.user_ids is not None:
        ak_stmt = ak_stmt.where(VirtualKey.user_id.in_(v.user_ids))
    active_keys = db.scalar(ak_stmt)
    total_requests = row[0]
    errors = int(row[9])
    latency_sum_s = float(row[10]) / 1000.0
    return {
        "total_requests": total_requests,
        "total_tokens": int(row[1]),
        "total_prompt_tokens": int(row[2]),
        "total_completion_tokens": int(row[3]),
        "total_cost": float(row[4]),
        "cost_actual": float(row[5]),      # provider-reported (e.g. OpenRouter usage.cost)
        "cost_estimated": float(row[6]),   # tokens x configured price table
        "latency_p50_ms": int(row[7]) if row[7] is not None else 0,
        "latency_p95_ms": int(row[8]) if row[8] is not None else 0,
        "error_rate": round(errors / total_requests, 4) if total_requests else 0.0,
        "errors": errors,
        "tokens_per_sec": round(int(row[1]) / latency_sum_s, 1) if latency_sum_s else 0.0,
        "active_keys": active_keys or 0,
    }


@router.get("/usage/timeseries")
def usage_timeseries(
    start: datetime | None = None,
    end: datetime | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_id: int | None = None,
    bucket: str = "day",
    db: Session = Depends(get_db),
    v: Viewer = Depends(viewer),
) -> list[dict]:
    day = func.date_trunc(bucket, UsageLog.ts).label("day")
    stmt = scope_usage(
        _apply_filters(
            select(
                day,
                UsageLog.provider,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0),
            ),
            start,
            end,
            provider,
            model,
            key_id,
        ),
        v.user_ids,
    ).group_by(day, UsageLog.provider).order_by(day)
    return [
        {
            "day": r[0].date().isoformat(),
            "provider": r[1],
            "requests": r[2],
            "total_tokens": int(r[3]),
            "cost": float(r[4]),
        }
        for r in db.execute(stmt)
    ]


@router.get("/usage/by-key")
def usage_by_key(
    start: datetime | None = None,
    end: datetime | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_id: int | None = None,
    db: Session = Depends(get_db),
    v: Viewer = Depends(viewer),
) -> list[dict]:
    stmt = scope_usage(
        _apply_filters(
            select(
                UsageLog.key_id,
                VirtualKey.label,
                VirtualKey.key_prefix,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageLog.prompt_tokens), 0),
                func.coalesce(func.sum(UsageLog.completion_tokens), 0),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0),
                func.max(UsageLog.ts),
            ),
            start,
            end,
            provider,
            model,
            key_id,
        ),
        v.user_ids,
    ).join(VirtualKey, VirtualKey.id == UsageLog.key_id).group_by(
        UsageLog.key_id, VirtualKey.label, VirtualKey.key_prefix
    ).order_by(func.coalesce(func.sum(UsageLog.cost), 0).desc())
    return [
        {
            "key_id": r[0],
            "label": r[1],
            "key_prefix": r[2],
            "requests": r[3],
            "prompt_tokens": int(r[4]),
            "completion_tokens": int(r[5]),
            "total_tokens": int(r[6]),
            "cost": float(r[7]),
            "last_used_at": r[8].isoformat() if r[8] else None,
        }
        for r in db.execute(stmt)
    ]


@router.get("/usage/by-model")
def usage_by_model(
    start: datetime | None = None,
    end: datetime | None = None,
    provider: str | None = None,
    model: str | None = None,
    key_id: int | None = None,
    db: Session = Depends(get_db),
    v: Viewer = Depends(viewer),
) -> list[dict]:
    stmt = scope_usage(
        _apply_filters(
            select(
                UsageLog.provider,
                UsageLog.model,
                func.count().label("requests"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0),
                func.coalesce(func.sum(UsageLog.cost), 0),
            ),
            start,
            end,
            provider,
            model,
            key_id,
        ),
        v.user_ids,
    ).group_by(UsageLog.provider, UsageLog.model).order_by(
        func.coalesce(func.sum(UsageLog.total_tokens), 0).desc()
    )
    return [
        {
            "provider": r[0],
            "model": r[1],
            "requests": r[2],
            "total_tokens": int(r[3]),
            "cost": float(r[4]),
        }
        for r in db.execute(stmt)
    ]
