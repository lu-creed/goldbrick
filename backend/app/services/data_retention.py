"""磁盘瘦身 / 数据保留策略。

提供两类操作：
  1. prune_history_older_than(years)  — 删除 N 年之前的日线、复权因子、指标缓存。
  2. vacuum_database()                — 回收 SQLite 删除行占用的空页，真正把文件变小。

使用场景：
- sync_runner 在全量同步正常结束后调用 prune 做增量清理（量小，速度快）。
- 服务器 cron 每周/每月调用 `scripts/cleanup_cron.sh` 做完整 prune + vacuum。

⚠️ VACUUM 的磁盘代价：
  SQLite VACUUM 会把数据库重写到临时文件再替换，峰值需要额外约等于「VACUUM 后体积」的磁盘空间。
  盘几乎满的时候直接跑会失败。调用方应先释放其他临时空间（node_modules / pip cache / 日志）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.database import engine
from app.models import AdjFactorDaily, BarDaily, IndicatorPreDaily

logger = logging.getLogger(__name__)


def _cutoff_date(years: int) -> date:
    """计算保留窗口的起始日期；N 年前的整日。"""
    if years <= 0:
        raise ValueError("years must be > 0")
    return date.today() - timedelta(days=365 * years)


def prune_history_older_than(db: Session, years: int) -> dict[str, int]:
    """删除超过 N 年的三张主表历史数据，返回各表删除行数。

    删除策略：按 trade_date 严格小于 cutoff 的行。
    事务内一次性提交：任何一张表删失败会整体回滚。
    """
    cutoff = _cutoff_date(years)
    result: dict[str, int] = {"cutoff": cutoff.isoformat()}  # type: ignore[dict-item]

    for model, key in (
        (BarDaily, "bars_daily"),
        (AdjFactorDaily, "adj_factors_daily"),
        (IndicatorPreDaily, "indicator_pre_daily"),
    ):
        stmt = delete(model).where(model.trade_date < cutoff)
        deleted = db.execute(stmt).rowcount or 0
        result[key] = int(deleted)
        logger.info("prune %s rows<%s: %d", key, cutoff, deleted)

    db.commit()
    return result


def vacuum_database() -> None:
    """对 SQLite 执行 VACUUM，把 DELETE 释放的空页还给磁盘。

    ⚠️ 需要峰值磁盘空间 ≈ VACUUM 后文件大小。盘紧时先清其他地方再调用。
    VACUUM 不能在事务里跑，这里用 AUTOCOMMIT 隔离级别。
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("VACUUM"))
    logger.info("vacuum done")


def safe_prune_and_vacuum(years: int, min_free_mb: int = 500) -> dict:
    """适合 cron 调用的安全版本：先检查磁盘空余，不够就跳过 VACUUM 只做 prune。

    Args:
        years: 保留年数
        min_free_mb: VACUUM 前要求的最小空余 MB；不足则跳过 VACUUM 以免写入途中盘满
    """
    import shutil
    from app.database import SessionLocal

    out: dict = {"years": years}
    db = SessionLocal()
    try:
        out["pruned"] = prune_history_older_than(db, years)
    finally:
        db.close()

    free_mb = shutil.disk_usage("/").free // (1024 * 1024)
    out["free_mb_before_vacuum"] = int(free_mb)
    if free_mb < min_free_mb:
        out["vacuum"] = f"skipped (only {free_mb}MB free, need {min_free_mb}MB)"
        logger.warning("skip VACUUM: only %dMB free", free_mb)
        return out

    try:
        vacuum_database()
        out["vacuum"] = "ok"
    except Exception as ex:  # noqa: BLE001
        out["vacuum"] = f"failed: {ex}"
        logger.error("vacuum failed: %s", ex)
    return out
