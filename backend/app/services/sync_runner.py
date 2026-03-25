"""全量同步入口：写日志文件、更新 sync_runs / sync_jobs。"""
import threading
import traceback
from typing import Optional
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_backend_root, settings
from app.database import SessionLocal
from app.models import InstrumentMeta, Symbol, SyncJob, SyncRun
from app.services.derivatives import daterange_start_default, recompute_consecutive_for_symbol
from app.services.ingestion import fetch_and_upsert_symbol, verify_tushare_token_for_sync


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


def run_full_sync(trigger: str, existing_run_id: Optional[int] = None) -> SyncRun:
    """同步股票池中 enabled 标的；在独立 Session 中执行。"""
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
        log_path = log_dir / fname
        run_row.log_path = str(log_path)
        db.commit()

        end = date.today()
        start = daterange_start_default()

        with open(log_path, "w", encoding="utf-8") as fp:
            fp.write(f"trigger={trigger} start={start} end={end}\n")
            verify_tushare_token_for_sync()
            symbols = db.query(Symbol).filter(Symbol.enabled.is_(True)).all()
            if not symbols:
                fp.write("no enabled symbols in pool, nothing to sync\n")
            total = 0
            ok_count = 0
            fail_count = 0
            adj_fail_count = 0
            total_symbols = len(symbols)
            loop_started_at = datetime.utcnow()
            for idx, sym in enumerate(symbols, start=1):
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
                    ok_count += 1
                except Exception as ex:  # noqa: BLE001
                    fail_count += 1
                    fp.write(f"ERROR {sym.ts_code}: {ex}\n")
                    fp.write(traceback.format_exc())
            fp.write(f"done total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")

        run_row.finished_at = datetime.utcnow()
        run_row.status = "success"
        run_row.message = f"done ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} total_rows~={total}"
        job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
        if job:
            job.last_run_at = run_row.finished_at
            job.last_status = "success"
            job.last_error = None
        db.commit()
        db.refresh(run_row)
        return run_row
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        if run_row and run_row.id:
            failed = db.query(SyncRun).filter(SyncRun.id == run_row.id).one_or_none()
            if failed:
                failed.finished_at = datetime.utcnow()
                failed.status = "failed"
                failed.message = str(ex)[:2000]
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
        log_path = log_dir / fname
        run_row.log_path = str(log_path)
        db.commit()

        norm_codes = [c.strip().upper() for c in ts_codes if c and c.strip()]
        with open(log_path, "w", encoding="utf-8") as fp:
            start_text = start.isoformat() if start is not None else "from_listing"
            fp.write(
                f"trigger={trigger} start={start_text} end={end.isoformat()} codes={len(norm_codes)}\n"
            )
            verify_tushare_token_for_sync()

            symbols: list[Symbol] = []
            for code in norm_codes:
                sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
                if not sym:
                    # 手动选择拉取后，默认纳入可查询股票池，避免前端 K 线页看不到。
                    sym = Symbol(ts_code=code, name=None, enabled=True)
                    db.add(sym)
                    db.flush()  # 让 sym.id 可用
                elif not sym.enabled:
                    sym.enabled = True
                symbols.append(sym)

            total = 0
            ok_count = 0
            fail_count = 0
            adj_fail_count = 0
            total_symbols = len(symbols)
            loop_started_at = datetime.utcnow()
            for idx, sym in enumerate(symbols, start=1):
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
                    ok_count += 1
                except Exception as ex:  # noqa: BLE001
                    fail_count += 1
                    fp.write(f"ERROR {sym.ts_code}: {ex}\n")
                    fp.write(traceback.format_exc())

            fp.write(f"done total_rows_touched~={total} ok={ok_count} fail={fail_count} adj_fail={adj_fail_count}\n")

        run_row.finished_at = datetime.utcnow()
        run_row.status = "success"
        run_row.message = f"done ok={ok_count} fail={fail_count} adj_fail={adj_fail_count} total_rows~={total}"

        job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
        if job:
            job.last_run_at = run_row.finished_at
            job.last_status = "success"
            job.last_error = None

        db.commit()
        db.refresh(run_row)
        return run_row
    except Exception as ex:  # noqa: BLE001
        db.rollback()
        if run_row and run_row.id:
            failed = db.query(SyncRun).filter(SyncRun.id == run_row.id).one_or_none()
            if failed:
                failed.finished_at = datetime.utcnow()
                failed.status = "failed"
                failed.message = str(ex)[:2000]
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
