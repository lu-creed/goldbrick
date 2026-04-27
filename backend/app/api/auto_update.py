"""自动更新后台接口（/api/admin/auto-update）：查询状态/日志、修改配置、手动触发一次。

全部路由要求 is_admin=True（get_current_admin 依赖）。

- GET  /admin/auto-update/status  —— 返回当前配置 + 最近 100 条日志
- POST /admin/auto-update/config  —— 更新 enabled / interval_minutes；改频率后立即重注册 APScheduler 任务
- POST /admin/auto-update/trigger —— 立即在后台线程跑一次检查（不修改配置，纯手动触发）
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.database import get_db
from app.models import AutoUpdateLog
from app.services.auto_update import get_or_create_config, trigger_check_now

router = APIRouter(prefix="/admin/auto-update", tags=["admin"])


class ConfigOut(BaseModel):
    enabled: bool
    interval_minutes: int
    last_run_at: Optional[str]
    last_commit_hash: Optional[str]


class LogOut(BaseModel):
    id: int
    created_at: str
    action: str
    status: str
    details: Optional[str]
    duration_ms: Optional[int]


class StatusOut(BaseModel):
    config: ConfigOut
    recent_logs: List[LogOut]


class UpdateConfigReq(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None


def _serialize_config(cfg) -> ConfigOut:
    return ConfigOut(
        enabled=cfg.enabled,
        interval_minutes=cfg.interval_minutes,
        last_run_at=cfg.last_run_at.isoformat() if cfg.last_run_at else None,
        last_commit_hash=cfg.last_commit_hash,
    )


@router.get("/status", response_model=StatusOut)
def get_status(
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    cfg = get_or_create_config(db)
    logs = (
        db.query(AutoUpdateLog)
        .order_by(desc(AutoUpdateLog.created_at))
        .limit(100)
        .all()
    )
    return StatusOut(
        config=_serialize_config(cfg),
        recent_logs=[
            LogOut(
                id=row.id,
                created_at=row.created_at.isoformat(),
                action=row.action,
                status=row.status,
                details=row.details,
                duration_ms=row.duration_ms,
            )
            for row in logs
        ],
    )


@router.post("/config", response_model=ConfigOut)
def update_config(
    body: UpdateConfigReq,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    cfg = get_or_create_config(db)
    need_reschedule = False

    if body.enabled is not None:
        cfg.enabled = body.enabled
    if body.interval_minutes is not None:
        if body.interval_minutes < 1 or body.interval_minutes > 1440:
            raise HTTPException(status_code=400, detail="interval_minutes 必须在 1-1440 之间")
        if body.interval_minutes != cfg.interval_minutes:
            need_reschedule = True
            cfg.interval_minutes = body.interval_minutes

    db.commit()
    db.refresh(cfg)

    if need_reschedule:
        # 频率变了才重注册任务；仅 enabled 切换不需要（tick 内部读 DB 判断）
        from app.scheduler import reschedule_auto_update_job
        reschedule_auto_update_job()

    return _serialize_config(cfg)


@router.post("/trigger")
def trigger_now(_admin=Depends(get_current_admin)):
    """立即触发一次检查（在 daemon 线程里执行，API 立即返回）。"""
    trigger_check_now()
    return {"ok": True, "message": "已触发自动更新检查，几秒后刷新日志列表即可看到结果"}
