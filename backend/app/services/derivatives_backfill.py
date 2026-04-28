"""涨跌停连续天数存量回填（0.0.4-dev 升级 → 版本 2）。

背景：V1.0.0 起 bars_daily.consecutive_limit_up_days / _down_days 按「收盘涨幅 ≥ 9.8%」硬编码计数，
未按板块分档、未做新股豁免。0.0.4-dev 重写 services/derivatives.py 后，存量数据需要按新口径回算一次。

机制：
- app_settings 里用 key=derivatives_recompute_version 记录已完成的版本号；目标版本为 _TARGET_VERSION。
- 应用启动时（main.py lifespan）若发现 current < target，则 fork 一个后台 daemon 线程跑 run_backfill_in_background。
- 线程按 asset_type='stock' 的 symbol 遍历，对每只调 recompute_consecutive_for_symbol（内部独立 Session）。
- 跑完写入 target version；失败则保留旧 version，下次启动再试。
- 不 block 应用启动；不影响其他 HTTP 请求；日志前缀 [DERIV_BACKFILL]。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from app.database import SessionLocal
from app.models import AppSetting, InstrumentMeta, Symbol
from app.services.derivatives import recompute_consecutive_for_symbol

log = logging.getLogger(__name__)

_TARGET_VERSION = 2
_VERSION_KEY = "derivatives_recompute_version"
_backfill_thread: Optional[threading.Thread] = None


def _get_version() -> int:
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _VERSION_KEY).one_or_none()
        if not row:
            return 0
        try:
            return int((row.value or "0").strip() or "0")
        except ValueError:
            return 0
    finally:
        db.close()


def _set_version(v: int) -> None:
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _VERSION_KEY).one_or_none()
        if not row:
            db.add(AppSetting(key=_VERSION_KEY, value=str(v)))
        else:
            row.value = str(v)
        db.commit()
    finally:
        db.close()


def _list_stock_symbol_ids() -> list[tuple[int, str]]:
    """返回 [(symbol_id, ts_code), ...]，按 ts_code 升序。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(Symbol.id, Symbol.ts_code)
            .join(InstrumentMeta, InstrumentMeta.ts_code == Symbol.ts_code)
            .filter(InstrumentMeta.asset_type == "stock")
            .order_by(Symbol.ts_code.asc())
            .all()
        )
        return [(int(r[0]), str(r[1])) for r in rows]
    finally:
        db.close()


def _run_backfill() -> None:
    """在后台线程里执行回填。异常只记录日志不抛出。"""
    ids = _list_stock_symbol_ids()
    total = len(ids)
    if total == 0:
        log.info("[DERIV_BACKFILL] 无 asset_type=stock 个股，跳过并直接标记版本 %s", _TARGET_VERSION)
        _set_version(_TARGET_VERSION)
        return

    log.info("[DERIV_BACKFILL] 开始回填 total=%d target_version=%d", total, _TARGET_VERSION)
    start_ts = time.time()
    ok = 0
    fail = 0
    for i, (sid, ts_code) in enumerate(ids, start=1):
        db = SessionLocal()
        try:
            recompute_consecutive_for_symbol(db, sid)
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
            log.warning("[DERIV_BACKFILL] 失败 ts=%s err=%s", ts_code, e)
        finally:
            db.close()
        if i % 200 == 0 or i == total:
            elapsed = time.time() - start_ts
            log.info(
                "[DERIV_BACKFILL] progress=%d/%d ok=%d fail=%d elapsed=%.1fs",
                i, total, ok, fail, elapsed,
            )

    # 只要有一条成功，就认为回填已生效；版本号推进。
    # 全失败（极端异常）则保留旧版本以便重试。
    if ok > 0:
        _set_version(_TARGET_VERSION)
        log.info(
            "[DERIV_BACKFILL] 完成 ok=%d fail=%d version→%d",
            ok, fail, _TARGET_VERSION,
        )
    else:
        log.error("[DERIV_BACKFILL] 全部失败 ok=0 fail=%d 版本号不推进，下次启动重试", fail)


def maybe_start_backfill_on_startup() -> None:
    """lifespan 启动时调用；若 current version < target 则异步回填。

    幂等：同进程内重复调用不会起多个线程。
    """
    global _backfill_thread
    current = _get_version()
    if current >= _TARGET_VERSION:
        return
    if _backfill_thread is not None and _backfill_thread.is_alive():
        return
    log.info(
        "[DERIV_BACKFILL] 触发存量回填 current=%d target=%d（后台线程）",
        current, _TARGET_VERSION,
    )
    t = threading.Thread(
        target=_run_backfill,
        name="derivatives-backfill",
        daemon=True,
    )
    _backfill_thread = t
    t.start()
