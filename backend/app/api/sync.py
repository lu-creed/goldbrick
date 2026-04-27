"""同步任务 + 数据后台 API（路径前缀 /api/sync/）。

这个路由文件是「数据同步」的控制入口，包含：

1. 定时任务配置（GET/PUT /sync/job）
   - 查看和修改定时同步的 cron 表达式及开关
   - 修改后立即重新注册到 APScheduler（调度器）

2. 手动触发同步（POST /sync/run / /sync/fetch / /sync/fetch-all / /sync/fetch-all-index）
   - run：全量同步（全市场所有已登记个股）
   - fetch：指定股票列表 + 日期范围
   - fetch-all：全市场个股（instrument_meta 中 asset_type=stock）
   - fetch-all-index：全市场已登记指数（asset_type=index）

3. 暂停/继续/取消运行中的任务（协作式）
   - 工作线程在每只标的的间隙检查 pause_requested / cancel_requested 标志
   - force=true 的 cancel 可在线程已死/卡住时强制终结数据库记录

4. 查看运行记录和日志
   - GET /sync/runs：最近 N 条运行记录
   - GET /sync/runs/{id}/log：下载同步日志文件（路径校验防止目录穿越）

5. 元数据和指数管理
   - POST /sync/universe/sync：增量更新股票元数据
   - POST /sync/stock-list：更新股票列表
   - GET /sync/index-candidates：从 Tushare 获取指数候选列表
   - POST /sync/index-meta/apply：将勾选指数写入本地

6. 数据后台汇总（GET /sync/data-center）
   - 一条 SQL 返回所有已登记标的的 K 线条数、复权因子条数等统计信息

7. 单股日线分页查看（GET /sync/symbol/{ts_code}/daily）
8. 补录单日数据（POST /sync/single-day）

重要业务逻辑均在 services/sync_runner.py 和 services/ingestion.py 中实现。
"""

from __future__ import annotations
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_admin
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
def get_sync_job(_admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """查询当前定时同步任务配置（cron 表达式、是否启用、上次运行信息）。"""
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if not job:
        raise HTTPException(status_code=404, detail="no sync job")
    return job


@router.put("/job", response_model=SyncJobOut)
def put_sync_job(body: SyncJobUpdate, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """更新定时同步任务配置（修改 cron 表达式或启用/禁用），修改后立即重新注册到调度器。

    cron_expr 格式：标准 5 字段 cron，如 "0 20 * * 1-5"（工作日 20:00 运行）。
    """
    job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
    if not job:
        raise HTTPException(status_code=404, detail="no sync job")
    if body.cron_expr is not None:
        # 校验 cron 格式（必须有 5 个字段，否则 APScheduler 会抛异常）
        fields = body.cron_expr.strip().split()
        if len(fields) != 5:
            raise HTTPException(status_code=400, detail="cron_expr must have 5 fields: min hour day month dow")
        job.cron_expr = body.cron_expr.strip()
    if body.enabled is not None:
        job.enabled = body.enabled
    db.commit()
    db.refresh(job)
    # 修改后立即重新注册调度器（删除旧 job，按新 cron 创建新 job）
    reschedule_sync_job()
    return job


@router.post("/run", response_model=SyncRunOut)
def trigger_run(_admin=Depends(get_current_admin)):
    """手动立即触发全量同步（与定时任务相同的逻辑，trigger 标记为 'manual'）。

    同步会在后台线程中进行，此接口立即返回「排队中」状态的 SyncRun 记录。
    通过 GET /sync/runs 轮询查看实时进度。
    """
    try:
        run = enqueue_full_sync("manual")
        reschedule_sync_job()
        return run
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/fetch", response_model=SyncRunOut)
def trigger_fetch(body: ManualFetchRequest, _admin=Depends(get_current_admin)):
    """手动指定股票列表和日期范围进行拉取（用于补录特定股票特定时间段的数据）。

    参数说明见 ManualFetchRequest schema：
    - ts_codes: 股票代码列表，如 ["600000.SH", "000001.SZ"]
    - start_date / end_date: 拉取的日期范围
    - from_listing: True 时忽略 start_date，从该股上市日起拉取
    """
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
def trigger_fetch_all(body: ManualFetchAllRequest, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """全市场手动拉取：标的范围为 instrument_meta 中所有 asset_type='stock' 的个股。

    比 /run 更灵活：可以指定日期范围，而不是固定拉「今天往前 N 天」。
    适合初次建库（from_listing=True）或补录某个时段的数据。
    """
    # 修复可能存在的 symbols 表缺行问题（instrument_meta 有但 symbols 没有的情况）
    ensure_symbols_for_stock_meta(db)
    codes = [
        r[0]
        for r in db.query(InstrumentMeta.ts_code)
        .filter(InstrumentMeta.asset_type == "stock")
        .order_by(InstrumentMeta.ts_code.asc())
        .all()
    ]
    if not codes:
        # instrument_meta 为空（首次部署）→ 自动先同步股票元数据，再继续
        try:
            incremental_sync_stock_list_meta(db)
        except Exception as ex:  # noqa: BLE001
            raise HTTPException(
                status_code=400,
                detail=f"本地尚无个股元数据，自动同步股票列表失败：{ex}。请检查 Tushare Token 配置后重试。",
            ) from ex
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
                detail="同步股票列表后仍无数据，请确认 Tushare Token 有效且网络可达。",
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
def trigger_fetch_all_index(body: ManualFetchAllRequest, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """全市场指数手动拉取：标的为 instrument_meta 中 asset_type='index' 的已登记指数。

    走 Tushare index_daily 接口（无复权因子），日期语义与 /fetch-all 相同。
    需要先在数据后台「指数」页签从 Tushare 勾选并写入，才能有可拉取的指数。
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


@router.get("/runs", response_model=List[SyncRunOut])
def list_runs(limit: int = 20, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """查询最近 N 条同步运行记录（默认 20 条，最多 100 条），按 ID 降序（最新在前）。"""
    lim = min(max(limit, 1), 100)
    return db.query(SyncRun).order_by(SyncRun.id.desc()).limit(lim).all()


@router.post("/runs/{run_id}/pause", response_model=SyncRunOut)
def pause_sync_run(run_id: int, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """请求暂停正在运行的同步任务。

    协作式暂停：不强制杀死线程，而是设置 pause_requested=True 标志。
    工作线程在完成当前这只标的后，会读到此标志并进入 'paused' 状态阻塞等待，
    直到收到 resume 请求或 cancel 请求。

    适用状态：queued（排队中）或 running（运行中）。
    """
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
def resume_sync_run(run_id: int, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """解除暂停，继续执行同步任务。

    两种情况均可 resume：
    1. 任务已经进入 'paused' 状态（线程阻塞中）→ 清除 pause_requested，状态改回 running
    2. 任务仍在 queued/running 但已设置了 pause_requested（尚未真正暂停）→ 仅清除标志，取消预暂停
    """
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务已结束，无法继续")
    if not run.pause_requested and run.status != "paused":
        raise HTTPException(status_code=400, detail="当前未处于暂停")
    run.pause_requested = False
    if run.status == "paused":
        run.status = "running"  # 从暂停恢复为运行中
    db.commit()
    db.refresh(run)
    return run


@router.post("/runs/{run_id}/cancel", response_model=SyncRunOut)
def cancel_sync_run(run_id: int, force: bool = False, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """请求取消同步任务。

    两种模式：
    - 普通 cancel（force=False）：设置 cancel_requested=True 标志，工作线程在下一个间隙读到后自行退出。
      适合线程仍在正常运行时使用，会正确记录已完成/跳过条数。
    - 强制 cancel（force=True）：直接在数据库中将状态标记为 'cancelled'，无需等待线程响应。
      适合线程已死（崩溃）或长期卡住时使用。若线程仍在运行，下次轮询时会发现状态已是终态，
      自行停止（收尾时也不会覆盖已强制结束的记录）。
    """
    run = db.query(SyncRun).filter(SyncRun.id == run_id).one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status not in ("queued", "running", "paused"):
        raise HTTPException(status_code=400, detail="任务已结束，无法取消")
    if force:
        # 强制终结：直接修改数据库记录，不等待工作线程
        run.pause_requested = False
        run.cancel_requested = False
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        tail = "[强制结束：任务可能已无活跃工作线程，已在库中收口]"
        base = (run.message or "").strip()
        run.message = (f"{base} {tail}" if base else tail)[:1900]
        # 同时更新 sync_jobs 的最后运行状态
        job = db.query(SyncJob).order_by(SyncJob.id.asc()).first()
        if job:
            job.last_run_at = run.finished_at
            job.last_status = "cancelled"
            job.last_error = None
    else:
        # 协作式取消：设置标志，工作线程自行处理
        run.cancel_requested = True
    db.commit()
    db.refresh(run)
    return run


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(run_id: int, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """下载指定同步任务的日志文件内容（纯文本格式）。

    安全校验：日志路径必须在预设的 log_dir/sync 目录内，防止路径穿越攻击。
    （路径穿越攻击：恶意用户可能传入 ../../etc/passwd 之类的路径读取系统文件）

    返回：日志文件的完整文本内容，UTF-8 编码，无法解码的字节以「替换字符」显示。
    """
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
    # 安全白名单：日志文件必须在 log_dir/sync 目录下
    allowed_root = (get_backend_root() / settings.log_dir / "sync").resolve()
    try:
        log_path.relative_to(allowed_root)  # 若路径不在白名单内，抛 ValueError
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid log path") from None
    if not log_path.exists():
        raise HTTPException(
            status_code=404,
            detail="磁盘上尚无该日志文件（若任务刚启动可重试；若任务正在拉取单只标的，文件应已存在，请联系排查路径）",
        )
    return log_path.read_text(encoding="utf-8", errors="replace")


@router.post("/universe/sync", response_model=UniverseSyncOut)
def sync_universe(force: bool = False, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """兼容旧路径：等价于 POST /sync/stock-list（增量更新股票元数据）。"""
    try:
        return sync_universe_meta(db, force=force)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/stock-list", response_model=UniverseSyncOut)
def post_stock_list(_admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """从 Tushare 增量更新股票元数据（名称、市场、交易所、上市日期）。

    新股会自动新增，已有股票若字段有变化（如改名）则更新。
    同时会同步维护 symbols 表，确保 K 线下拉选单可用。
    """
    try:
        return incremental_sync_stock_list_meta(db)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/index-candidates", response_model=List[IndexCandidateRow])
def get_index_candidates(
    market: Optional[str] = None,
    limit: int = 2000,
    _admin=Depends(get_current_admin),
):
    """从 Tushare 获取指数候选列表（用于数据后台「指数」页签弹窗展示）。

    此接口只查询，不写库。用户在弹窗中勾选后，调用 POST /sync/index-meta/apply 才正式写入。

    Args:
        market: 可选的市场过滤（如 'SSE'=上交所，'SZSE'=深交所），None 返回全部。
        limit: 最多返回条数（上限 8000）。
    """
    lim = min(max(limit, 1), 8000)
    try:
        rows = fetch_remote_index_basic_rows(market=market, limit=lim)
        return [IndexCandidateRow.model_validate(r) for r in rows]
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.post("/index-meta/apply", response_model=IndexMetaApplyResult)
def post_index_meta_apply(body: IndexMetaApplyRequest, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """将用户勾选的指数正式写入 instrument_meta 和 symbols 表（本阶段不支持移除）。

    已存在的指数代码会跳过（幂等）；与个股代码冲突的会报 400 错误。
    写入后需手动拉取日线（POST /sync/fetch-all-index）才有 K 线数据。
    """
    try:
        payload = [it.model_dump() for it in body.items]
        r = apply_index_meta_selection(db, payload)
        return IndexMetaApplyResult(added=r["added"], skipped=r["skipped"])
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex


@router.get("/data-center", response_model=List[DataCenterRow])
def data_center(limit: int = 500, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """数据后台汇总：返回所有已登记标的的数据统计信息（K 线条数、复权因子条数、覆盖率等）。

    用单条聚合 SQL（替代 N+1 循环）一次性返回所有标的的统计，适合数据后台全量展示。

    返回字段说明：
    - bar_count: 该股已同步的 K 线条数
    - adj_factor_count: 已同步且与 K 线日期匹配的复权因子条数
    - adj_factor_coverage_ratio: 复权因子覆盖率（adj_count / bar_count），100% 表示每根 K 线都有复权因子
    - adj_factor_synced: 复权因子是否完整同步（coverage=100%）
    """
    # 兼容迁移：若 instrument_meta 为空，先从 symbols 迁移一次
    bootstrap_meta_from_symbols(db)

    lim = min(max(limit, 1), 2000)
    # 单条 SQL：一次查询所有标的的 bar 统计 + adj 匹配数
    # LEFT JOIN adj 只统计 bar 与 adj 日期完全相同的行（即已正确对齐的复权因子数）
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
        # 复权因子覆盖率（0~1，bar_count=0 时为 0 避免除零错误）
        coverage = (adj_count / bar_count) if bar_count > 0 else 0.0
        out.append(DataCenterRow(
            ts_code=r.ts_code,
            name=r.name,
            asset_type=r.asset_type,
            list_date=r.list_date,
            market=getattr(r, "market", None),
            exchange=getattr(r, "exchange", None),
            synced_once=bar_count > 0,  # True 表示至少同步过一次
            first_bar_date=r.first_bar_date,
            last_bar_date=r.last_bar_date,
            bar_count=bar_count,
            adj_factor_count=adj_count,
            adj_factor_coverage_ratio=coverage,
            adj_factor_synced=(bar_count > 0 and adj_count == bar_count),  # 完整同步
        ))
    return out


@router.get("/symbol/{ts_code}/daily", response_model=SymbolDailyPage)
def symbol_daily_bars(
    ts_code: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    page: int = 1,
    page_size: int = 20,
    _admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """查询单只股票的日线明细（分页，按日期降序，最新在前）。

    用于数据后台「单股 K 线明细」弹窗，同时标注每行是否有对应的复权因子（has_adj_factor 字段）。
    """
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
    # 预加载该股所有有复权因子的日期（用于打标签，避免 N+1）
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
            has_adj_factor=b.trade_date in adj_dates,  # True=该日有复权因子
        )
        for b in rows
    ]
    return SymbolDailyPage(total=total, items=items)


@router.post("/single-day", response_model=SyncRunOut)
def sync_single_day(body: SingleDaySyncRequest, _admin=Depends(get_current_admin)):
    """补录或覆盖单只股票的单日 K 线数据（bar + 复权因子）。

    适用场景：发现某只股票某天的数据有误或缺失，手动补录。
    使用与 /fetch 相同的工作线程机制，结果写入 sync_runs 日志。
    """
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
