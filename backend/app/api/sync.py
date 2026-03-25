from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_backend_root, settings
from app.database import get_db
from app.models import AdjFactorDaily, BarDaily, InstrumentMeta, Symbol, SyncJob, SyncRun
from app.scheduler import reschedule_sync_job
from app.schemas import (
    DataCenterRow,
    ManualFetchRequest,
    SyncJobOut,
    SyncJobUpdate,
    SyncRunOut,
    UniverseSyncOut,
)
from app.services.ingestion import bootstrap_meta_from_symbols, sync_universe_meta
from app.services.sync_runner import enqueue_full_sync, enqueue_symbol_list_sync, run_full_sync

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/job", response_model=SyncJobOut)
def get_sync_job(db: Session = Depends(get_db)):
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if not job:
        raise HTTPException(status_code=404, detail="no sync job")
    return job


@router.put("/job", response_model=SyncJobOut)
def put_sync_job(body: SyncJobUpdate, db: Session = Depends(get_db)):
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if not job:
        raise HTTPException(status_code=404, detail="no sync job")
    if body.cron_expr is not None:
        fields = body.cron_expr.strip().split()
        if len(fields) != 5:
            raise HTTPException(status_code=400, detail="cron_expr must have 5 fields: min hour day month dow")
        job.cron_expr = body.cron_expr.strip()
    if body.enabled is not None:
        job.enabled = body.enabled
    db.commit()
    db.refresh(job)
    reschedule_sync_job()
    return job


@router.post("/run", response_model=SyncRunOut)
def trigger_run():
    try:
        run = enqueue_full_sync("manual")
        reschedule_sync_job()
        return run
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/fetch", response_model=SyncRunOut)
def trigger_fetch(body: ManualFetchRequest):
    """前端手动拉取：指定股票列表 + 日期范围。"""
    try:
        run = enqueue_symbol_list_sync(
            trigger="manual_fetch",
            ts_codes=body.ts_codes,
            start=body.start_date,
            end=body.end_date,
            from_listing=body.from_listing,
        )
        reschedule_sync_job()
        return run
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/runs", response_model=list[SyncRunOut])
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    lim = min(max(limit, 1), 100)
    return db.query(SyncRun).order_by(SyncRun.id.desc()).limit(lim).all()


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(run_id: int, db: Session = Depends(get_db)):
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run or not run.log_path:
        raise HTTPException(status_code=404, detail="log not found")
    log_path = Path(run.log_path).resolve()
    allowed_root = (get_backend_root() / settings.log_dir / "sync").resolve()
    if allowed_root not in log_path.parents:
        raise HTTPException(status_code=400, detail="invalid log path")
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="log file missing")
    return log_path.read_text(encoding="utf-8", errors="replace")


@router.post("/universe/sync", response_model=UniverseSyncOut)
def sync_universe(force: bool = False, db: Session = Depends(get_db)):
    try:
        return sync_universe_meta(db, force=force)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/data-center", response_model=list[DataCenterRow])
def data_center(limit: int = 500, db: Session = Depends(get_db)):
    lim = min(max(limit, 1), 2000)
    # 兜底：若元数据为空，先从 symbols 本地迁移，避免因外部接口限频导致页面空白。
    bootstrap_meta_from_symbols(db)
    metas = db.query(InstrumentMeta).order_by(InstrumentMeta.asset_type.asc(), InstrumentMeta.ts_code.asc()).limit(lim).all()
    out: list[DataCenterRow] = []
    for m in metas:
        sym = db.query(Symbol).filter(Symbol.ts_code == m.ts_code).one_or_none()
        if not sym:
            out.append(
                DataCenterRow(
                    ts_code=m.ts_code,
                    name=m.name,
                    asset_type=m.asset_type,
                    list_date=m.list_date,
                    synced_once=False,
                    first_bar_date=None,
                    last_bar_date=None,
                    bar_count=0,
                )
            )
            continue
        first_d, last_d, cnt = db.query(func.min(BarDaily.trade_date), func.max(BarDaily.trade_date), func.count(BarDaily.id)).filter(
            BarDaily.symbol_id == sym.id
        ).one()
        count = int(cnt or 0)
        bar_dates = {
            r[0]
            for r in db.query(BarDaily.trade_date)
            .filter(BarDaily.symbol_id == sym.id)
            .all()
            if r[0] is not None
        }
        adj_dates = {
            r[0]
            for r in db.query(AdjFactorDaily.trade_date)
            .filter(AdjFactorDaily.symbol_id == sym.id)
            .all()
            if r[0] is not None
        }
        matched_adj_count = len(bar_dates & adj_dates)
        coverage_ratio = (matched_adj_count / count) if count > 0 else 0.0
        out.append(
            DataCenterRow(
                ts_code=m.ts_code,
                name=m.name,
                asset_type=m.asset_type,
                list_date=m.list_date,
                synced_once=count > 0,
                first_bar_date=first_d,
                last_bar_date=last_d,
                bar_count=count,
                adj_factor_count=matched_adj_count,
                adj_factor_coverage_ratio=coverage_ratio,
                adj_factor_synced=(count > 0 and matched_adj_count == count),
            )
        )
    return out
