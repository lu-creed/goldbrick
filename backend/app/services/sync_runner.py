"""
全量同步入口：拉取 Tushare 日线数据并写入本地数据库。

核心概念：协作式暂停/取消
  工作线程（background thread）在「每只股票开始拉取前」主动检查数据库里的
  pause_requested / cancel_requested 标志：
  - cancel_requested=True → 立刻停止，将 SyncRun.status 置为 cancelled
  - pause_requested=True → 进入 sleep 循环，等待前端 resume 或 cancel
  这种方式不能打断「单只股票内部正在进行的 Tushare API 请求」，
  只能在两只股票之间检查（粒度=单只股票）。

  为何每次新建 Session 读标志？
  SQLAlchemy 的 Session 有本地缓存（identity map），如果在同一个 Session 里反复读
  同一行，可能读到缓存里的旧值，而不是其他连接写入的新值（pause/cancel 由 API 线程写入）。
  因此 _poll_pause_or_cancel 每次都 SessionLocal() 新建一个短 Session 来读最新值。
"""
import threading
import time
import traceback
from typing import Literal, Optional
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_backend_root, settings
from app.database import SessionLocal
from app.models import InstrumentMeta, Symbol, SyncJob, SyncRun
from app.services.derivatives import daterange_start_default, recompute_consecutive_for_symbol
from app.services.ingestion import (
    ensure_symbols_for_stock_meta,
    fetch_and_upsert_symbol,
    verify_tushare_token_for_sync,
)
from app.services.indicator_precompute import rebuild_indicator_pre_for_symbol


def _fmt_eta(seconds: float) -> str:
    """将剩余秒数格式化为易读的 ETA 字符串，如 '3h12m05s' 或 '5m30s' 或 '45s'。"""
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}h{m:02d}m{sec:02d}s"
    if m > 0:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


def _ensure_log_dir() -> Path:
    """确保同步日志目录存在并返回其路径（backend/logs/sync/）。"""
    root = get_backend_root()
    d = root / settings.log_dir / "sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _create_queued_run(trigger: str) -> SyncRun:
    """在数据库中创建一条状态为 'queued' 的 SyncRun 记录，并返回该记录（含 id）。

    用于 enqueue_* 函数：先建记录拿到 id，再开线程。
    这样前端可以立即看到「排队中」的状态，而不是等线程启动后才出现。
    """
    db = SessionLocal()
    try:
        row = SyncRun(
            started_at=datetime.utcnow(),
            trigger=trigger,
            status="queued",
            message="queued",
            log_path=None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _poll_pause_or_cancel(db: Session, run_row: SyncRun) -> Literal["go", "stop"]:
    """在每只股票「开始拉取前」调用：用新 Session 读最新的 pause/cancel 标志。

    Args:
        db: 主工作 Session（保持 SyncRun 状态写入，不用于读标志）。
        run_row: 当前 SyncRun 的 ORM 对象（在主 db Session 里）。

    Returns:
        "stop"：用户已请求取消，或 SyncRun 已被外部置为终态（取消/失败/成功）。
        "go"：可以继续处理下一只股票。

    行为：
    - 若检测到 cancel → 立即返回 "stop"
    - 若检测到 pause → 进入 1 秒间隔的轮询睡眠，直到被 resume（清 pause_requested）或 cancel
    - 恢复后将 run_row.status 改回 "running"
    """
    run_id = run_row.id
    while True:
        # 每次用新 Session 避免读到本 Session 的缓存（其他线程写入的 pause/cancel 才有效）
        fresh = SessionLocal()
        try:
            row = fresh.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
            if not row:
                return "stop"
            # 外部直接把状态改为终态（强制结束）时也应停止
            if row.status in ("cancelled", "failed", "success"):
                return "stop"
            if row.cancel_requested:
                return "stop"
            if not row.pause_requested:
                # 从 paused 恢复：更新主 Session 里的状态
                if run_row.status == "paused":
                    run_row.status = "running"
                    db.commit()
                return "go"
        finally:
            fresh.close()

        # 处于 pause_requested 状态：更新消息提示并等待
        if run_row.status != "paused":
            run_row.status = "paused"
        base = (run_row.message or "").split("[已暂停")[0].strip()
        run_row.message = base + (" [已暂停，可继续或取消]" if base else "[已暂停，可继续或取消]")
        db.commit()
        time.sleep(1.0)


def _commit_run_finish(
    db: Session,
    run_row: SyncRun,
    *,
    stopped_early: bool,
    ok_count: int,
    fail_count: int,
    adj_fail_count: int,
    total: int,
) -> None:
    """将本次同步的终态写入数据库（同时更新 SyncJob 的 last_* 字段）。

    若 SyncRun 已被外部强制结束（status 已是 cancelled/failed/success 且有 finished_at），
    则只清理 pause/cancel 标志，不覆盖状态和消息（避免覆盖外部收口的结果）。
    """
    db.refresh(run_row)  # 重新从库读一次，防止本地缓存与外部写入不一致
    if run_row.status in ("cancelled", "failed", "success") and run_row.finished_at is not None:
        # 已被强制结束，只清标志
        run_row.pause_requested = False
        run_row.cancel_requested = False
        db.commit()
        return
    run_row.finished_at = datetime.utcnow()
    run_row.pause_requested = False
    run_row.cancel_requested = False
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if stopped_early:
        run_row.status = "cancelled"
        run_row.message = f"cancelled ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} total_rows~={total}"
        if job:
            job.last_run_at = run_row.finished_at
            job.last_status = "cancelled"
            job.last_error = None
    else:
        run_row.status = "success"
        run_row.message = f"done ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} total_rows~={total}"
        if job:
            job.last_run_at = run_row.finished_at
            job.last_status = "success"
            job.last_error = None
    db.commit()


def run_full_sync(trigger: str, existing_run_id: Optional[int] = None) -> SyncRun:
    """执行全量同步：拉取 instrument_meta 中所有个股（不含指数）的日线数据。

    Args:
        trigger: 触发方式，"schedule"（定时）或 "manual"（手动）。
        existing_run_id: 若指定，复用已有的 SyncRun 记录（enqueue 模式）；
                         不指定则新建一条记录。

    流程：
    1. 创建/更新 SyncRun 记录，分配日志文件路径
    2. 从 instrument_meta 查询所有 asset_type=stock 的股票
    3. 逐只处理：每只股票开始前调用 _poll_pause_or_cancel 检查暂停/取消
    4. fetch_and_upsert_symbol：调 Tushare API 拉日线 + 复权因子，写入数据库
    5. recompute_consecutive_for_symbol：重新计算涨跌停连续天数
    6. rebuild_indicator_pre_for_symbol：预计算内置指标缓存
    7. 处理完所有股票后更新 SyncRun 终态

    错误处理：单只股票失败不中断整体任务，计入 fail_count；整体异常才标为 failed。
    """
    log_dir = _ensure_log_dir()
    db = SessionLocal()
    run_row: Optional[SyncRun] = None
    try:
        if existing_run_id is not None:
            # enqueue 模式：复用已建好的 queued 记录
            run_row = db.query(SyncRun).filter(SyncRun.id == existing_run_id).one_or_none()
            if not run_row:
                raise ValueError("run_id not found")
            run_row.status = "running"
            run_row.message = "running"
            run_row.started_at = datetime.utcnow()
            db.commit()
            db.refresh(run_row)
        else:
            run_row = SyncRun(
                started_at=datetime.utcnow(),
                trigger=trigger,
                status="running",
                message=None,
                log_path=None,
            )
            db.add(run_row)
            db.commit()
            db.refresh(run_row)

        # 日志文件名格式：20240110_180000_123.log（时间+run_id）
        fname = f"{run_row.started_at.strftime('%Y%m%d_%H%M%S')}_{run_row.id}.log"
        log_path = (log_dir / fname).resolve()

        # 默认日期范围：从 daterange_start_default() 到今天
        end = date.today()
        start = daterange_start_default()

        # 先打开文件再 commit log_path：避免「库里有路径但文件尚不存在」的窗口，前端打开日志 404。
        with open(log_path, "w", encoding="utf-8") as fp:
            run_row.log_path = str(log_path)
            db.commit()
            fp.write(f"trigger={trigger} start={start} end={end}\n")
            fp.flush()
            total = 0
            ok_count = 0
            fail_count = 0
            adj_fail_count = 0
            stopped_early = False
            # 第一次检查：开始网络请求前就可能被取消
            if _poll_pause_or_cancel(db, run_row) == "stop":
                stopped_early = True
                fp.write("USER_CANCEL: cancelled before network setup (between symbols)\n")
            else:
                verify_tushare_token_for_sync()
                ensure_symbols_for_stock_meta(db)  # 补齐 instrument_meta 和 symbols 的同步
                symbols = (
                    db.query(Symbol)
                    .join(InstrumentMeta, InstrumentMeta.ts_code == Symbol.ts_code)
                    .filter(InstrumentMeta.asset_type == "stock")
                    .order_by(Symbol.ts_code.asc())
                    .all()
                )
                if not symbols:
                    fp.write("no stock rows in pool (check instrument_meta / stock list sync), nothing to sync\n")
                total_symbols = len(symbols)
                loop_started_at = datetime.utcnow()
                for idx, sym in enumerate(symbols, start=1):
                    # 每只股票开始前检查一次暂停/取消
                    if _poll_pause_or_cancel(db, run_row) == "stop":
                        stopped_early = True
                        fp.write("USER_CANCEL: stop requested (between symbols)\n")
                        break
                    try:
                        # 计算进度和 ETA（预计剩余时间）
                        processed = max(0, idx - 1)
                        eta_text = "calculating"
                        if processed > 0 and total_symbols > processed:
                            elapsed = (datetime.utcnow() - loop_started_at).total_seconds()
                            avg_per_symbol = elapsed / processed
                            eta_seconds = avg_per_symbol * (total_symbols - processed)
                            eta_text = _fmt_eta(eta_seconds)
                        run_row.message = (
                            f"progress {idx}/{total_symbols} [{int(idx * 100 / max(total_symbols, 1))}%] "
                            f"ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} eta={eta_text} code={sym.ts_code}"
                        )
                        db.commit()
                        # 拉日线 + 复权因子
                        n, adj_failed = fetch_and_upsert_symbol(db, sym, start, end, log_fp=fp)
                        total += n
                        if adj_failed:
                            adj_fail_count += 1
                        # 重算涨跌停连续天数（需要全量历史数据）
                        recompute_consecutive_for_symbol(db, sym.id)
                        # 预计算内置指标缓存（qfq 前复权）
                        try:
                            n_pre = rebuild_indicator_pre_for_symbol(db, sym.id, "qfq")
                            if n_pre:
                                fp.write(f"  indicator_pre_daily(qfq) rows={n_pre} {sym.ts_code}\n")
                        except Exception as ex_pre:  # noqa: BLE001
                            fp.write(f"  WARN indicator_pre_daily {sym.ts_code}: {ex_pre}\n")
                        ok_count += 1
                    except Exception as ex:  # noqa: BLE001
                        # 单只股票失败：记录错误但继续处理下一只
                        fail_count += 1
                        fp.write(f"ERROR {sym.ts_code}: {ex}\n")
                        fp.write(traceback.format_exc())
                    fp.flush()
            if stopped_early:
                fp.write(f"stopped by user total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")
            else:
                fp.write(f"done total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")

        _commit_run_finish(
            db,
            run_row,
            stopped_early=stopped_early,
            ok_count=ok_count,
            fail_count=fail_count,
            adj_fail_count=adj_fail_count,
            total=total,
        )
        db.refresh(run_row)
        return run_row
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        if run_row and run_row.id:
            failed = db.query(SyncRun).filter(SyncRun.id == run_row.id).one_or_none()
            if failed:
                # 用户已「强制结束」时不覆盖为 failed
                if failed.status == "cancelled" and failed.finished_at is not None:
                    pass
                else:
                    failed.finished_at = datetime.utcnow()
                    failed.status = "failed"
                    failed.message = str(ex)[:2000]
                    failed.pause_requested = False
                    failed.cancel_requested = False
                    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
                    if job:
                        job.last_run_at = failed.finished_at
                        job.last_status = "failed"
                        job.last_error = str(ex)[:2000]
                    db.commit()
        raise
    finally:
        db.close()


def run_symbol_list_sync(
    trigger: str,
    ts_codes: list[str],
    start: Optional[date],
    end: date,
    from_listing: bool = False,
    existing_run_id: Optional[int] = None,
) -> SyncRun:
    """按用户指定的股票列表和日期范围拉取数据并入库。

    Args:
        trigger: 触发方式，如 "manual_fetch"。
        ts_codes: 要同步的股票代码列表，如 ["600000.SH", "000001.SZ"]。
        start: 起始日期；from_listing=True 时可为 None（从各股上市日开始）。
        end: 截止日期（含）。
        from_listing: 为 True 时忽略 start，改从 instrument_meta.list_date 开始拉。
        existing_run_id: enqueue 模式下传入已建好的 SyncRun id。

    与 run_full_sync 的区别：
    - 范围：只处理指定列表，而非全部个股
    - 起始日期：可以从上市日开始（full sync 总是用 daterange_start_default）
    - 若股票不在 symbols 表里，会自动创建记录（新股补录场景）
    """
    if not ts_codes:
        raise ValueError("ts_codes is empty")
    if start is not None and start > end:
        raise ValueError("start_date must be <= end_date")

    log_dir = _ensure_log_dir()
    db = SessionLocal()
    run_row: Optional[SyncRun] = None

    try:
        if existing_run_id is not None:
            run_row = db.query(SyncRun).filter(SyncRun.id == existing_run_id).one_or_none()
            if not run_row:
                raise ValueError("run_id not found")
            run_row.status = "running"
            run_row.message = "running"
            run_row.started_at = datetime.utcnow()
            db.commit()
            db.refresh(run_row)
        else:
            run_row = SyncRun(
                started_at=datetime.utcnow(),
                trigger=trigger,
                status="running",
                message=None,
                log_path=None,
            )
            db.add(run_row)
            db.commit()
            db.refresh(run_row)

        fname = f"{run_row.started_at.strftime('%Y%m%d_%H%M%S')}_{run_row.id}.log"
        log_path = (log_dir / fname).resolve()

        # 统一大写并去除空白
        norm_codes = [c.strip().upper() for c in ts_codes if c and c.strip()]
        with open(log_path, "w", encoding="utf-8") as fp:
            run_row.log_path = str(log_path)
            db.commit()
            start_text = start.isoformat() if start is not None else "from_listing"
            fp.write(
                f"trigger={trigger} start={start_text} end={end.isoformat()} codes={len(norm_codes)}\n"
            )
            fp.flush()
            total = 0
            ok_count = 0
            fail_count = 0
            adj_fail_count = 0
            stopped_early = False
            if _poll_pause_or_cancel(db, run_row) == "stop":
                stopped_early = True
                fp.write("USER_CANCEL: cancelled before network setup (between symbols)\n")
            else:
                verify_tushare_token_for_sync()

                # 确保指定的代码都在 symbols 表里（新代码自动创建）
                symbols: list[Symbol] = []
                for code in norm_codes:
                    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
                    if not sym:
                        sym = Symbol(ts_code=code, name=None)
                        db.add(sym)
                        db.flush()
                    symbols.append(sym)

                total_symbols = len(symbols)
                loop_started_at = datetime.utcnow()
                for idx, sym in enumerate(symbols, start=1):
                    if _poll_pause_or_cancel(db, run_row) == "stop":
                        stopped_early = True
                        fp.write("USER_CANCEL: stop requested (between symbols)\n")
                        break
                    try:
                        processed = max(0, idx - 1)
                        eta_text = "calculating"
                        if processed > 0 and total_symbols > processed:
                            elapsed = (datetime.utcnow() - loop_started_at).total_seconds()
                            avg_per_symbol = elapsed / processed
                            eta_seconds = avg_per_symbol * (total_symbols - processed)
                            eta_text = _fmt_eta(eta_seconds)
                        run_row.message = (
                            f"progress {idx}/{total_symbols} [{int(idx * 100 / max(total_symbols, 1))}%] "
                            f"ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} eta={eta_text} code={sym.ts_code}"
                        )
                        db.commit()
                        # from_listing 模式：从 instrument_meta 读取该股的上市日期作为 start
                        real_start = start
                        if from_listing:
                            meta = db.query(InstrumentMeta).filter(InstrumentMeta.ts_code == sym.ts_code).one_or_none()
                            real_start = meta.list_date if meta and meta.list_date else date(1990, 1, 1)
                        if real_start is None:
                            raise ValueError("start_date is required when from_listing=false")
                        n, adj_failed = fetch_and_upsert_symbol(db, sym, real_start, end, log_fp=fp)
                        total += n
                        if adj_failed:
                            adj_fail_count += 1
                        recompute_consecutive_for_symbol(db, sym.id)
                        try:
                            n_pre = rebuild_indicator_pre_for_symbol(db, sym.id, "qfq")
                            if n_pre:
                                fp.write(f"  indicator_pre_daily(qfq) rows={n_pre} {sym.ts_code}\n")
                        except Exception as ex_pre:  # noqa: BLE001
                            fp.write(f"  WARN indicator_pre_daily {sym.ts_code}: {ex_pre}\n")
                        ok_count += 1
                    except Exception as ex:  # noqa: BLE001
                        fail_count += 1
                        fp.write(f"ERROR {sym.ts_code}: {ex}\n")
                        fp.write(traceback.format_exc())
                    fp.flush()

            if stopped_early:
                fp.write(f"stopped by user total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")
            else:
                fp.write(f"done total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")

        _commit_run_finish(
            db,
            run_row,
            stopped_early=stopped_early,
            ok_count=ok_count,
            fail_count=fail_count,
            adj_fail_count=adj_fail_count,
            total=total,
        )
        db.refresh(run_row)
        return run_row
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        if run_row and run_row.id:
            failed = db.query(SyncRun).filter(SyncRun.id == run_row.id).one_or_none()
            if failed:
                if failed.status == "cancelled" and failed.finished_at is not None:
                    pass
                else:
                    failed.finished_at = datetime.utcnow()
                    failed.status = "failed"
                    failed.message = str(ex)[:2000]
                    failed.pause_requested = False
                    failed.cancel_requested = False
                    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
                    if job:
                        job.last_run_at = failed.finished_at
                        job.last_status = "failed"
                        job.last_error = str(ex)[:2000]
                    db.commit()
        raise
    finally:
        db.close()


def ensure_default_sync_job(db: Session) -> SyncJob:
    """确保 sync_jobs 表至少有一条默认配置记录；不存在则创建（默认每天18点）。

    幂等操作：多次调用只会创建一次。应用启动时调用一次即可。
    """
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if job:
        return job
    job = SyncJob(cron_expr="0 18 * * *", enabled=True)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_full_sync(trigger: str) -> SyncRun:
    """将全量同步任务投入后台线程队列并立即返回 SyncRun 记录（异步模式）。

    先创建状态为 'queued' 的 SyncRun 记录，再启动守护线程。
    前端轮询 /sync/runs 接口即可看到实时进度。
    """
    row = _create_queued_run(trigger)
    t = threading.Thread(target=run_full_sync, args=(trigger, row.id), daemon=True)
    t.start()
    return row


def enqueue_symbol_list_sync(
    trigger: str,
    ts_codes: list[str],
    start: Optional[date],
    end: date,
    from_listing: bool = False,
) -> SyncRun:
    """将指定股票列表的同步任务投入后台线程并立即返回（异步模式）。

    与 enqueue_full_sync 相同模式：先建记录，再开线程。
    """
    row = _create_queued_run(trigger)
    t = threading.Thread(
        target=run_symbol_list_sync,
        args=(trigger, ts_codes, start, end, from_listing, row.id),
        daemon=True,
    )
    t.start()
    return row
