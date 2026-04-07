"""全量同步入口：写日志文件、更新 sync_runs / sync_jobs。"""
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
    """将秒数格式化为易读 ETA（如 3m12s）。"""
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
    root = get_backend_root()
    d = root / settings.log_dir / "sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _create_queued_run(trigger: str) -> SyncRun:
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
    """在每只股票**开始**拉取前调用：单独开短 Session 读标志位，避免与 API 写入同会话缓存不一致。

    返回 stop = 用户已取消，应结束整次任务；go = 可继续当前标的。
    若 pause_requested 为真，阻塞睡眠直至恢复（清 pause）或取消。
    """
    run_id = run_row.id
    while True:
        fresh = SessionLocal()
        try:
            row = fresh.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
            if not row:
                return "stop"
            # 含「强制结束」：仅改库、不设 cancel_requested 时工作线程也必须退出
            if row.status in ("cancelled", "failed", "success"):
                return "stop"
            if row.cancel_requested:
                return "stop"
            if not row.pause_requested:
                if run_row.status == "paused":
                    run_row.status = "running"
                    db.commit()
                return "go"
        finally:
            fresh.close()

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
    """写入本轮 sync_run 终态；若已被「强制结束」API 先收口则不再覆盖状态与摘要。"""
    db.refresh(run_row)
    if run_row.status in ("cancelled", "failed", "success") and run_row.finished_at is not None:
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
    """定时/手动全量：拉取 instrument_meta 中全部个股在默认日期窗内的日线（不含指数）。"""
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

        end = date.today()
        start = daterange_start_default()

        # 必须先打开文件再 commit log_path，否则会出现「库里有路径但文件尚不存在」的窗口，前端打开日志 404。
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
            if _poll_pause_or_cancel(db, run_row) == "stop":
                stopped_early = True
                fp.write("USER_CANCEL: cancelled before network setup (between symbols)\n")
            else:
                verify_tushare_token_for_sync()
                ensure_symbols_for_stock_meta(db)
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
                        n, adj_failed = fetch_and_upsert_symbol(db, sym, start, end, log_fp=fp)
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
    """按用户选择的标的 + 日期范围拉取并入库。"""
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
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if job:
        return job
    job = SyncJob(cron_expr="0 18 * * *", enabled=True)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_full_sync(trigger: str) -> SyncRun:
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
    row = _create_queued_run(trigger)
    t = threading.Thread(
        target=run_symbol_list_sync,
        args=(trigger, ts_codes, start, end, from_listing, row.id),
        daemon=True,
    )
    t.start()
    return row
