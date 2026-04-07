"""同步任务 + 数据后台（路径前缀 /sync，即 /api/sync/...）。

包含：定时任务配置、立即全量同步、按股票+日期范围拉数、全市场个股/全市场已登记指数按同一日期规则拉数、运行记录与日志、
运行中暂停/继续/取消（标的粒度协作式，不打断单标的内请求）、
元数据/指数同步、数据后台汇总、单股日 K 分页、单日补数。
重活在 app/services/sync_runner.py、ingestion.py。
对应前端：同步任务页、数据后台页。
"""

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_backend_root, settings
from app.database import get_db
from app.models import AdjFactorDaily, BarDaily, InstrumentMeta, Symbol, SyncJob, SyncRun
from app.scheduler import reschedule_sync_job
from app.schemas import (
    DataCenterRow,
    IndexCandidateRow,
    IndexMetaApplyRequest,
    IndexMetaApplyResult,
    ManualFetchAllRequest,
    ManualFetchRequest,
    SingleDaySyncRequest,
    SymbolDailyPage,
    SymbolDailyRow,
    SyncJobOut,
    SyncJobUpdate,
    SyncRunOut,
    UniverseSyncOut,
)
from app.services.ingestion import (
    apply_index_meta_selection,
    bootstrap_meta_from_symbols,
    ensure_symbols_for_index_meta,
    ensure_symbols_for_stock_meta,
    fetch_remote_index_basic_rows,
    incremental_sync_stock_list_meta,
    sync_universe_meta,
)
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


@router.post("/fetch-all", response_model=SyncRunOut)
def trigger_fetch_all(body: ManualFetchAllRequest, db: Session = Depends(get_db)):
    """全市场手动拉取：标的范围与数据池个股一致（instrument_meta 中 asset_type=stock）。"""
    ensure_symbols_for_stock_meta(db)
    codes = [
        r[0]
        for r in db.query(InstrumentMeta.ts_code)
        .filter(InstrumentMeta.asset_type == "stock")
        .order_by(InstrumentMeta.ts_code.asc())
        .all()
    ]
    if not codes:
        raise HTTPException(
            status_code=400,
            detail="本地尚无个股元数据（instrument_meta 为空），请先在数据后台执行「同步全量标的元数据/股票列表」",
        )
    try:
        run = enqueue_symbol_list_sync(
            trigger="manual_fetch_all",
            ts_codes=codes,
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


@router.post("/fetch-all-index", response_model=SyncRunOut)
def trigger_fetch_all_index(body: ManualFetchAllRequest, db: Session = Depends(get_db)):
    """全市场指数手动拉取：标的为 instrument_meta 中 asset_type=index（与数据后台已登记指数一致）。

    走 Tushare `index_daily`，无复权因子；日期语义与 `/fetch-all`、`/fetch` 相同。
    """
    ensure_symbols_for_index_meta(db)
    codes = [
        r[0]
        for r in db.query(InstrumentMeta.ts_code)
        .filter(InstrumentMeta.asset_type == "index")
        .order_by(InstrumentMeta.ts_code.asc())
        .all()
    ]
    if not codes:
        raise HTTPException(
            status_code=400,
            detail="本地尚无已登记指数（instrument_meta 中无 index），请先在数据后台「指数」页签从 Tushare 勾选加入",
        )
    try:
        run = enqueue_symbol_list_sync(
            trigger="manual_fetch_all_index",
            ts_codes=codes,
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


@router.post("/runs/{run_id}/pause", response_model=SyncRunOut)
def pause_sync_run(run_id: int, db: Session = Depends(get_db)):
    """请求暂停：工作线程在下一只标的开始时进入 paused 并阻塞，直至 resume 或 cancel。"""
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status == "paused":
        raise HTTPException(status_code=400, detail="任务已在暂停中")
    if run.status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="仅排队中或运行中的任务可暂停")
    run.pause_requested = True
    db.commit()
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/resume", response_model=SyncRunOut)
def resume_sync_run(run_id: int, db: Session = Depends(get_db)):
    """解除暂停：排队中预暂停、或 paused 状态均可恢复为继续拉取。"""
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务已结束，无法继续")
    if not run.pause_requested and run.status != "paused":
        raise HTTPException(status_code=400, detail="当前未处于暂停")
    run.pause_requested = False
    if run.status == "paused":
        run.status = "running"
    db.commit()
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/cancel", response_model=SyncRunOut)
def cancel_sync_run(run_id: int, force: bool = False, db: Session = Depends(get_db)):
    """请求取消：默认同协作式 cancel_requested；force=true 时在库内直接记为 cancelled（线程已丢/长期卡住时用）。

    若工作线程仍在跑，force 后其下一次轮询会读到终态并尽快退出，且收尾时不会覆盖已强制结束的记录。
    """
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")
    if force:
        run.pause_requested = False
        run.cancel_requested = False
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        tail = "[强制结束：任务可能已无活跃工作线程，已在库中收口]"
        base = (run.message or "").strip()
        run.message = (f"{base} {tail}" if base else tail)[:1900]
        job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
        if job:
            job.last_run_at = run.finished_at
            job.last_status = "cancelled"
            job.last_error = None
    else:
        run.cancel_requested = True
    db.commit()
    db.refresh(run)
    return run


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(run_id: int, db: Session = Depends(get_db)):
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    raw = (run.log_path or "").strip()
    if not raw:
        raise HTTPException(
            status_code=404,
            detail="log 路径尚未写入（任务可能仍在排队或启动瞬间，请稍后刷新）",
        )
    log_path = Path(raw)
    if not log_path.is_absolute():
        log_path = (get_backend_root() / raw).resolve()
    else:
        log_path = log_path.resolve()
    allowed_root = (get_backend_root() / settings.log_dir / "sync").resolve()
    try:
        log_path.relative_to(allowed_root)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid log path") from None
    if not log_path.exists():
        raise HTTPException(
            status_code=404,
            detail="磁盘上尚无该日志文件（若任务刚启动可重试；若任务正在拉取单只标的，文件应已存在，请联系排查路径）",
        )
    return log_path.read_text(encoding="utf-8", errors="replace")


@router.post("/universe/sync", response_model=UniverseSyncOut)
def sync_universe(force: bool = False, db: Session = Depends(get_db)):
    """兼容旧路径：等价于增量更新股票列表。"""
    try:
        return sync_universe_meta(db, force=force)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/stock-list", response_model=UniverseSyncOut)
def post_stock_list(db: Session = Depends(get_db)):
    """更新股票列表：Tushare stock_basic 增量合并元数据（含市场类别、交易所）。"""
    try:
        return incremental_sync_stock_list_meta(db)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/index-candidates", response_model=list[IndexCandidateRow])
def get_index_candidates(
    market: Optional[str] = None,
    limit: int = 2000,
):
    lim = min(max(limit, 1), 8000)
    try:
        rows = fetch_remote_index_basic_rows(market=market, limit=lim)
        return [IndexCandidateRow.model_validate(r) for r in rows]
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/index-meta/apply", response_model=IndexMetaApplyResult)
def post_index_meta_apply(body: IndexMetaApplyRequest, db: Session = Depends(get_db)):
    """将勾选指数写入元数据与 symbols 池（本阶段不支持移除）。"""
    try:
        payload = [it.model_dump() for it in body.items]
        r = apply_index_meta_selection(db, payload)
        return IndexMetaApplyResult(added=r["added"], skipped=r["skipped"])
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/data-center", response_model=list[DataCenterRow])
def data_center(limit: int = 500, db: Session = Depends(get_db)):
    lim = min(max(limit, 1), 2000)
    bootstrap_meta_from_symbols(db)

    # 用单条 SQL 替代 N+1 循环：一次性拿到所有 bar 统计 + adj 匹配数
    # LEFT JOIN adj 时只统计 bar 与 adj 日期完全相同的行（即已匹配的复权因子数）
    sql = text("""
        SELECT
            m.ts_code,
            m.name,
            m.asset_type,
            m.list_date,
            m.market,
            m.exchange,
            s.id              AS symbol_id,
            COUNT(b.id)       AS bar_count,
            MIN(b.trade_date) AS first_bar_date,
            MAX(b.trade_date) AS last_bar_date,
            COUNT(a.id)       AS adj_count
        FROM instrument_meta m
        LEFT JOIN symbols s       ON s.ts_code = m.ts_code
        LEFT JOIN bars_daily b    ON b.symbol_id = s.id
        LEFT JOIN adj_factors_daily a
               ON a.symbol_id = s.id AND a.trade_date = b.trade_date
        GROUP BY m.ts_code, m.name, m.asset_type, m.list_date, m.market, m.exchange, s.id
        ORDER BY m.asset_type ASC, m.ts_code ASC
        LIMIT :lim
    """)
    rows_raw = db.execute(sql, {"lim": lim}).fetchall()

    out: list[DataCenterRow] = []
    for r in rows_raw:
        bar_count = int(r.bar_count or 0)
        adj_count = int(r.adj_count or 0)
        coverage = (adj_count / bar_count) if bar_count > 0 else 0.0
        out.append(DataCenterRow(
            ts_code=r.ts_code,
            name=r.name,
            asset_type=r.asset_type,
            list_date=r.list_date,
            market=getattr(r, "market", None),
            exchange=getattr(r, "exchange", None),
            synced_once=bar_count > 0,
            first_bar_date=r.first_bar_date,
            last_bar_date=r.last_bar_date,
            bar_count=bar_count,
            adj_factor_count=adj_count,
            adj_factor_coverage_ratio=coverage,
            adj_factor_synced=(bar_count > 0 and adj_count == bar_count),
        ))
    return out


@router.get("/symbol/{ts_code}/daily", response_model=SymbolDailyPage)
def symbol_daily_bars(
    ts_code: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
):
    code = ts_code.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        raise HTTPException(404, "unknown ts_code")
    q = db.query(BarDaily).filter(BarDaily.symbol_id == sym.id)
    if start is not None:
        q = q.filter(BarDaily.trade_date >= start)
    if end is not None:
        q = q.filter(BarDaily.trade_date <= end)
    total = q.count()
    pg = max(1, page)
    ps = min(max(1, page_size), 200)
    rows = q.order_by(BarDaily.trade_date.desc()).offset((pg - 1) * ps).limit(ps).all()
    adj_dates = {
        r[0]
        for r in db.query(AdjFactorDaily.trade_date).filter(AdjFactorDaily.symbol_id == sym.id).all()
        if r[0] is not None
    }
    items = [
        SymbolDailyRow(
            trade_date=b.trade_date,
            open=float(b.open),
            high=float(b.high),
            low=float(b.low),
            close=float(b.close),
            volume=int(b.volume),
            amount=float(b.amount),
            turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
            has_adj_factor=b.trade_date in adj_dates,
        )
        for b in rows
    ]
    return SymbolDailyPage(total=total, items=items)


@router.post("/single-day", response_model=SyncRunOut)
def sync_single_day(body: SingleDaySyncRequest):
    """补录或覆盖单日 bar+复权因子，写入 sync_runs 日志。"""
    try:
        run = enqueue_symbol_list_sync(
            trigger="single_day",
            ts_codes=[body.ts_code.strip().upper()],
            start=body.trade_date,
            end=body.trade_date,
            from_listing=False,
        )
        reschedule_sync_job()
        return run
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex
