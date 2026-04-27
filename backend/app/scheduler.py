"""
APScheduler 定时器：从数据库读取 cron 表达式，按时触发全量同步任务。

APScheduler 是一个 Python 后台定时任务库。这里用 BackgroundScheduler（后台线程）。
每次服务启动时，会从数据库读取 SyncJob 表里的 cron_expr 配置，注册一个定时器。
当 cron 时间到达时，自动在后台线程里调用 _scheduled_job，执行全量同步。
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.database import SessionLocal
from app.services.sync_runner import ensure_default_sync_job, run_full_sync

log = logging.getLogger(__name__)

# 全局 APScheduler 实例（后台线程模式，不阻塞 FastAPI 主线程）
scheduler = BackgroundScheduler()
# 定时任务的 ID，用于查找和替换已注册的任务（修改配置后重新注册时不会重复）
_JOB_ID = "pool_sync_cron"
_AUTO_UPDATE_JOB_ID = "auto_update_check"
_AUTO_UPDATE_CLEANUP_JOB_ID = "auto_update_cleanup"


def _scheduled_job() -> None:
    """定时触发的实际工作：检查任务是否启用，然后执行全量同步。

    先查一次 DB 确认 SyncJob.enabled 仍为 True（用户可能在 UI 里禁用了）。
    如果已禁用则跳过本次触发，等下次 cron 再来。
    """
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
    """根据数据库中第一条 SyncJob 的配置，重新注册定时器。

    何时调用：应用启动时（start_scheduler 里调用）、用户通过 API 修改 cron 配置后调用。
    逻辑：先删掉旧的同名定时器，再根据最新 cron_expr 注册新的。
    若 enabled=False，不注册任何定时器（只有手动同步可触发）。
    cron_expr 格式为标准 5 域：「分 时 日 月 周」，如 "0 18 * * *"=每天18时整。
    """
    db = SessionLocal()
    try:
        job = ensure_default_sync_job(db)
        if scheduler.get_job(_JOB_ID):
            scheduler.remove_job(_JOB_ID)
        if not job.enabled:
            log.info("sync cron not registered (disabled)")
            return
        # 解析 5 域 cron 表达式
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
    """启动后台定时器并注册所有定时任务。应用启动时由 lifespan 调用一次。"""
    if not scheduler.running:
        scheduler.start()
    reschedule_sync_job()
    reschedule_auto_update_job()
    # 每日凌晨 3:17 清理 30 天前的自动更新日志（避免表无限增长）
    scheduler.add_job(
        _auto_update_cleanup_tick,
        CronTrigger(hour=3, minute=17),
        id=_AUTO_UPDATE_CLEANUP_JOB_ID,
        replace_existing=True,
    )


def _auto_update_tick() -> None:
    """自动更新定时触发入口：读 DB 判断是否启用，启用则跑一次 run_check_once。

    拆分成独立函数而不是 inline lambda，便于 APScheduler 任务列表里识别。
    """
    from app.services.auto_update import get_or_create_config, run_check_once

    db = SessionLocal()
    try:
        cfg = get_or_create_config(db)
        if not cfg.enabled:
            return
    finally:
        db.close()
    # 释放 DB 会话后再执行（run_check_once 内部会自己开 session）
    run_check_once()


def _auto_update_cleanup_tick() -> None:
    """每日清理 30 天前的自动更新日志。"""
    from app.services.auto_update import cleanup_old_logs
    try:
        cleanup_old_logs(days=30)
    except Exception:
        log.exception("auto-update cleanup failed")


def reschedule_auto_update_job() -> None:
    """根据 DB 中的 interval_minutes 重新注册自动更新定时器。

    何时调用：应用启动时（start_scheduler 里调用）、用户通过 API 修改频率后调用。
    逻辑：无论 enabled 与否都注册 IntervalTrigger（tick 内部会读 DB 判断是否真的跑），
          这样用户在 UI 里开关启用时不需要重启后端也能立即生效。
    频率范围：1 到 1440 分钟（1 天）。超出会被 clamp。
    """
    from app.services.auto_update import get_or_create_config

    db = SessionLocal()
    try:
        cfg = get_or_create_config(db)
        interval = max(1, min(1440, int(cfg.interval_minutes or 5)))
    finally:
        db.close()

    if scheduler.get_job(_AUTO_UPDATE_JOB_ID):
        scheduler.remove_job(_AUTO_UPDATE_JOB_ID)
    scheduler.add_job(
        _auto_update_tick,
        IntervalTrigger(minutes=interval),
        id=_AUTO_UPDATE_JOB_ID,
        replace_existing=True,
    )
    log.info("registered auto-update check: every %d min", interval)


def shutdown_scheduler() -> None:
    """停止后台定时器。应用关闭时由 lifespan 调用，wait=False 不等待正在跑的任务。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
