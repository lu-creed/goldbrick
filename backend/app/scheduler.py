"""APScheduler：从数据库读取 cron，触发同步。"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
from app.services.sync_runner import ensure_default_sync_job, run_full_sync

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler()
_JOB_ID = "pool_sync_cron"


def _scheduled_job() -> None:
    db = SessionLocal()
    try:
        job = ensure_default_sync_job(db)
        if not job.enabled:
            log.info("sync job disabled, skip schedule")
            return
    finally:
        db.close()
    try:
        run_full_sync("schedule")
    except Exception:
        log.exception("scheduled sync failed")


def reschedule_sync_job() -> None:
    """根据 DB 中第一条 SyncJob 重建 cron（应用启动或更新配置后调用）。"""
    db = SessionLocal()
    try:
        job = ensure_default_sync_job(db)
        if scheduler.get_job(_JOB_ID):
            scheduler.remove_job(_JOB_ID)
        if not job.enabled:
            log.info("sync cron not registered (disabled)")
            return
        # 5 域：分 时 日 月 周
        fields = job.cron_expr.strip().split()
        if len(fields) != 5:
            log.error("invalid cron_expr (need 5 fields): %s", job.cron_expr)
            return
        minute, hour, day, month, day_of_week = fields
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        scheduler.add_job(_scheduled_job, trigger, id=_JOB_ID, replace_existing=True)
        log.info("registered sync cron: %s", job.cron_expr)
    finally:
        db.close()


def start_scheduler() -> None:
    if not scheduler.running:
        scheduler.start()
    reschedule_sync_job()


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
