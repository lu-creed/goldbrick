"""股票回测 API（路径前缀 /api/backtest/）。

接口列表：
  POST /api/backtest/run              执行一次回测，结果自动保存到历史
  GET  /api/backtest/records          获取回测历史列表（分页）
  GET  /api/backtest/records/{id}     获取单条历史详情（含完整结果）
  DELETE /api/backtest/records/{id}   删除单条历史记录
  GET  /api/backtest/trade-chart      获取单笔交易的 K 线验证图

多条件支持：body 可传 strategy_id（引用已保存策略）、buy_logic+sell_logic（直接传）或老的单条件字段。
历史表新增 buy/sell strategy_snapshot_json，详情页读取并反序列化为 logic 对象。
"""

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BacktestRecord, BarDaily, Strategy, Symbol, UserIndicator
from app.auth import get_current_user
from app.schemas import (
    BacktestEquityPoint,
    BacktestRecordDetail,
    BacktestRecordItem,
    BacktestRunIn,
    BacktestRunOut,
    BacktestTradeRow,
    StrategyLogic,
    TradeChartBarPoint,
    TradeChartIndicatorPoint,
    TradeChartOut,
)
from app.services.backtest_runner import run_backtest
from app.services.screening_runner import _WARMUP_DAYS
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition

log = logging.getLogger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


def _build_run_out(raw: dict) -> BacktestRunOut:
    """将 run_backtest 返回的原始字典转换为 BacktestRunOut Pydantic 对象。"""
    trades = [BacktestTradeRow(**t) for t in raw["trades"]]
    equity_curve = [BacktestEquityPoint(**pt) for pt in raw["equity_curve"]]
    benchmark_curve = [BacktestEquityPoint(**pt) for pt in raw.get("benchmark_curve", [])]
    buy_logic_raw = raw.get("buy_logic")
    sell_logic_raw = raw.get("sell_logic")
    is_multi = bool(raw.get("is_multi"))
    return BacktestRunOut(
        start_date=raw["start_date"],
        end_date=raw["end_date"],
        initial_capital=raw["initial_capital"],
        final_equity=raw["final_equity"],
        total_return_pct=raw["total_return_pct"],
        max_drawdown_pct=raw["max_drawdown_pct"],
        total_trades=raw["total_trades"],
        win_rate=raw.get("win_rate"),
        scanned_stocks=raw["scanned_stocks"],
        equity_curve=equity_curve,
        trades=trades,
        note=raw.get("note"),
        annualized_return=raw.get("annualized_return"),
        sharpe_ratio=raw.get("sharpe_ratio"),
        calmar_ratio=raw.get("calmar_ratio"),
        profit_factor=raw.get("profit_factor"),
        avg_win_pct=raw.get("avg_win_pct"),
        avg_loss_pct=raw.get("avg_loss_pct"),
        max_win_pct=raw.get("max_win_pct"),
        max_loss_pct=raw.get("max_loss_pct"),
        avg_holding_days=raw.get("avg_holding_days"),
        total_win=raw.get("total_win", 0),
        total_loss=raw.get("total_loss", 0),
        benchmark_curve=benchmark_curve,
        benchmark_index=raw.get("benchmark_index"),
        benchmark_return_pct=raw.get("benchmark_return_pct"),
        alpha_pct=raw.get("alpha_pct"),
        commission_cost_total=raw.get("commission_cost_total", 0.0),
        adj_mode=raw.get("adj_mode", "qfq"),
        execution_price=raw.get("execution_price", "close"),
        commission_rate=raw.get("commission_rate", 0.00025),
        commission_min=raw.get("commission_min", 5.0),
        stamp_duty_rate=raw.get("stamp_duty_rate", 0.001),
        slippage_bps=raw.get("slippage_bps", 10.0),
        lot_size=raw.get("lot_size", 100),
        is_multi=is_multi,
        buy_logic=StrategyLogic(**buy_logic_raw) if is_multi and buy_logic_raw else None,
        sell_logic=StrategyLogic(**sell_logic_raw) if is_multi and sell_logic_raw else None,
    )


def _resolve_backtest_logic(
    body: BacktestRunIn, current_user, db: Session,
) -> tuple[Optional[dict], Optional[dict], Optional[int]]:
    """按优先级解析回测入参：strategy_id > buy_logic/sell_logic > 老字段（由 runner 内部转）。

    Returns: (buy_logic_dict | None, sell_logic_dict | None, strategy_id | None)
    """
    if body.strategy_id is not None:
        row = db.query(Strategy).filter(Strategy.id == body.strategy_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="策略不存在")
        if row.user_id is not None and row.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="策略不存在")
        if row.kind != "backtest":
            raise HTTPException(status_code=400, detail="该策略不是回测策略")
        if not row.buy_logic_json or not row.sell_logic_json:
            raise HTTPException(status_code=500, detail="回测策略缺少 buy_logic 或 sell_logic")
        return json.loads(row.buy_logic_json), json.loads(row.sell_logic_json), row.id
    if body.buy_logic is not None and body.sell_logic is not None:
        return (
            body.buy_logic.model_dump(exclude_none=False, mode="json"),
            body.sell_logic.model_dump(exclude_none=False, mode="json"),
            None,
        )
    return None, None, None


def _primary_condition_of(logic_dict: Optional[dict]) -> dict:
    """取出 logic 里 primary_condition_id 对应的条件，用于回填老字段冗余。"""
    if not logic_dict:
        return {}
    pid = logic_dict.get("primary_condition_id")
    for c in logic_dict.get("conditions") or []:
        if int(c.get("id", 0)) == int(pid):
            return c
    return {}


@router.post("/run", response_model=BacktestRunOut)
def backtest_run(body: BacktestRunIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """在 [start_date, end_date] 对全市场执行条件选股回测，并自动保存本次结果到历史。

    优先级：strategy_id > buy_logic+sell_logic > 老单条件字段。
    """
    buy_logic, sell_logic, used_strategy_id = _resolve_backtest_logic(body, current_user, db)
    try:
        if buy_logic is not None and sell_logic is not None:
            raw = run_backtest(
                db,
                start_date=body.start_date,
                end_date=body.end_date,
                buy_logic=buy_logic,
                sell_logic=sell_logic,
                initial_capital=body.initial_capital,
                max_positions=body.max_positions,
                max_scan=body.max_scan,
                commission_rate=body.commission_rate,
                commission_min=body.commission_min,
                stamp_duty_rate=body.stamp_duty_rate,
                slippage_bps=body.slippage_bps,
                lot_size=body.lot_size,
                execution_price=body.execution_price,
                benchmark_index=body.benchmark_index,
            )
        else:
            raw = run_backtest(
                db,
                start_date=body.start_date,
                end_date=body.end_date,
                user_indicator_id=body.user_indicator_id,
                sub_key=body.sub_key or "",
                buy_op=body.buy_op,
                buy_threshold=body.buy_threshold,
                sell_op=body.sell_op,
                sell_threshold=body.sell_threshold,
                initial_capital=body.initial_capital,
                max_positions=body.max_positions,
                max_scan=body.max_scan,
                commission_rate=body.commission_rate,
                commission_min=body.commission_min,
                stamp_duty_rate=body.stamp_duty_rate,
                slippage_bps=body.slippage_bps,
                lot_size=body.lot_size,
                execution_price=body.execution_price,
                benchmark_index=body.benchmark_index,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    out = _build_run_out(raw)
    out.strategy_id = used_strategy_id
    is_multi = bool(raw.get("is_multi"))

    # ── 查询指标名用于冗余存储 ────────────────────────────────────
    buy_logic_raw = raw.get("buy_logic")  # runner 总会回填（老路径也会）
    sell_logic_raw = raw.get("sell_logic")
    primary_buy = _primary_condition_of(buy_logic_raw)
    primary_sell = _primary_condition_of(sell_logic_raw)
    primary_uid = int(primary_buy.get("user_indicator_id") or body.user_indicator_id or 0) or None
    indicator_name = ""
    indicator_code = ""
    if primary_uid:
        ind = db.query(UserIndicator).filter(UserIndicator.id == primary_uid).first()
        if ind:
            indicator_name = ind.display_name
            indicator_code = ind.code
    if is_multi:
        n_conds = (len(buy_logic_raw.get("conditions") or []) + len(sell_logic_raw.get("conditions") or [])
                   if buy_logic_raw and sell_logic_raw else 0)
        indicator_name = indicator_name + f"｜多条件 ({n_conds})" if indicator_name else f"多条件 ({n_conds})"

    # ── 自动保存到历史记录 ────────────────────────────────────────
    record = BacktestRecord(
        user_id=current_user.id,
        created_at=datetime.now(),
        start_date=str(body.start_date),
        end_date=str(body.end_date),
        user_indicator_id=primary_uid,
        indicator_name=indicator_name,
        indicator_code=indicator_code,
        sub_key=primary_buy.get("sub_key") if is_multi else body.sub_key,
        buy_op=primary_buy.get("compare_op", body.buy_op),
        buy_threshold=float(primary_buy.get("threshold", body.buy_threshold)),
        sell_op=primary_sell.get("compare_op", body.sell_op),
        sell_threshold=float(primary_sell.get("threshold", body.sell_threshold)),
        initial_capital=body.initial_capital,
        max_positions=body.max_positions,
        total_return_pct=out.total_return_pct,
        max_drawdown_pct=out.max_drawdown_pct,
        total_trades=out.total_trades,
        win_rate=out.win_rate,
        annualized_return=out.annualized_return,
        sharpe_ratio=out.sharpe_ratio,
        commission_rate=body.commission_rate,
        commission_min=body.commission_min,
        stamp_duty_rate=body.stamp_duty_rate,
        slippage_bps=body.slippage_bps,
        lot_size=body.lot_size,
        execution_price=body.execution_price,
        benchmark_index=out.benchmark_index,
        benchmark_return_pct=out.benchmark_return_pct,
        alpha_pct=out.alpha_pct,
        result_json=out.model_dump_json(),
        buy_strategy_snapshot_json=(
            json.dumps(buy_logic_raw, ensure_ascii=False) if is_multi and buy_logic_raw else None
        ),
        sell_strategy_snapshot_json=(
            json.dumps(sell_logic_raw, ensure_ascii=False) if is_multi and sell_logic_raw else None
        ),
    )
    try:
        db.add(record)
        db.commit()
    except Exception:
        log.exception("保存回测历史失败，不影响本次结果返回")
        db.rollback()
    # ────────────────────────────────────────────────────────────

    return out


@router.get("/records", response_model=List[BacktestRecordItem])
def backtest_records_list(
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(20, ge=5, le=100, description="每页条数"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取回测历史列表（按执行时间倒序，最新的在最前）。"""
    offset = (page - 1) * page_size
    rows = (
        db.query(BacktestRecord)
        .filter(BacktestRecord.user_id == current_user.id)
        .order_by(BacktestRecord.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    # from_attributes 不支持表达式字段（is_multi 由 JSON 非空推导），逐条手动构造
    return [
        BacktestRecordItem(
            id=r.id,
            created_at=r.created_at,
            start_date=r.start_date,
            end_date=r.end_date,
            indicator_name=r.indicator_name,
            indicator_code=r.indicator_code,
            user_indicator_id=r.user_indicator_id,
            sub_key=r.sub_key,
            buy_op=r.buy_op,
            buy_threshold=float(r.buy_threshold),
            sell_op=r.sell_op,
            sell_threshold=float(r.sell_threshold),
            initial_capital=float(r.initial_capital),
            max_positions=r.max_positions,
            total_return_pct=float(r.total_return_pct),
            max_drawdown_pct=float(r.max_drawdown_pct),
            total_trades=r.total_trades,
            win_rate=float(r.win_rate) if r.win_rate is not None else None,
            annualized_return=float(r.annualized_return) if r.annualized_return is not None else None,
            sharpe_ratio=float(r.sharpe_ratio) if r.sharpe_ratio is not None else None,
            execution_price=r.execution_price,
            benchmark_index=r.benchmark_index,
            benchmark_return_pct=float(r.benchmark_return_pct) if r.benchmark_return_pct is not None else None,
            alpha_pct=float(r.alpha_pct) if r.alpha_pct is not None else None,
            commission_rate=float(r.commission_rate) if r.commission_rate is not None else None,
            slippage_bps=float(r.slippage_bps) if r.slippage_bps is not None else None,
            is_multi=bool(r.buy_strategy_snapshot_json),
        )
        for r in rows
    ]


@router.get("/records/{record_id}", response_model=BacktestRecordDetail)
def backtest_record_detail(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单条回测历史的详情，包含完整回测结果 + 多条件快照（如有）。"""
    row = db.query(BacktestRecord).filter(
        BacktestRecord.id == record_id,
        BacktestRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="回测记录不存在")

    result: Optional[BacktestRunOut] = None
    try:
        result = BacktestRunOut.model_validate_json(row.result_json)
    except Exception:
        log.exception("解析回测历史 result_json 失败，id=%s", record_id)

    buy_logic_obj: Optional[StrategyLogic] = None
    sell_logic_obj: Optional[StrategyLogic] = None
    if row.buy_strategy_snapshot_json:
        try:
            buy_logic_obj = StrategyLogic(**json.loads(row.buy_strategy_snapshot_json))
        except Exception:
            log.exception("解析 buy_strategy_snapshot_json 失败，id=%s", record_id)
    if row.sell_strategy_snapshot_json:
        try:
            sell_logic_obj = StrategyLogic(**json.loads(row.sell_strategy_snapshot_json))
        except Exception:
            log.exception("解析 sell_strategy_snapshot_json 失败，id=%s", record_id)

    return BacktestRecordDetail(
        id=row.id,
        created_at=row.created_at,
        start_date=row.start_date,
        end_date=row.end_date,
        indicator_name=row.indicator_name,
        indicator_code=row.indicator_code,
        user_indicator_id=row.user_indicator_id,
        sub_key=row.sub_key,
        buy_op=row.buy_op,
        buy_threshold=float(row.buy_threshold),
        sell_op=row.sell_op,
        sell_threshold=float(row.sell_threshold),
        initial_capital=float(row.initial_capital),
        max_positions=row.max_positions,
        total_return_pct=float(row.total_return_pct),
        max_drawdown_pct=float(row.max_drawdown_pct),
        total_trades=row.total_trades,
        win_rate=float(row.win_rate) if row.win_rate is not None else None,
        annualized_return=float(row.annualized_return) if row.annualized_return is not None else None,
        sharpe_ratio=float(row.sharpe_ratio) if row.sharpe_ratio is not None else None,
        execution_price=row.execution_price,
        benchmark_index=row.benchmark_index,
        benchmark_return_pct=float(row.benchmark_return_pct) if row.benchmark_return_pct is not None else None,
        alpha_pct=float(row.alpha_pct) if row.alpha_pct is not None else None,
        commission_rate=float(row.commission_rate) if row.commission_rate is not None else None,
        slippage_bps=float(row.slippage_bps) if row.slippage_bps is not None else None,
        is_multi=bool(row.buy_strategy_snapshot_json),
        result=result,
        buy_logic=buy_logic_obj,
        sell_logic=sell_logic_obj,
    )


@router.delete("/records/{record_id}")
def backtest_record_delete(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """删除指定的回测历史记录（不可恢复）。"""
    row = db.query(BacktestRecord).filter(
        BacktestRecord.id == record_id,
        BacktestRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/trade-chart", response_model=TradeChartOut)
def backtest_trade_chart(
    ts_code: str = Query(...),
    user_indicator_id: int = Query(..., ge=1),
    sub_key: str = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
    _user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回单只股票在回测区间的 K 线 + 指标子线，供 Drawer 验证图使用。

    K 线只返回 [start_date, end_date] 范围内的数据（前端展示）；
    指标计算时向前加载 _WARMUP_DAYS 天预热数据，保证 MA60 等指标有效。
    """
    sym = db.query(Symbol).filter(Symbol.ts_code == ts_code.upper()).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail=f"未找到股票 {ts_code}")

    ui = db.query(UserIndicator).filter(UserIndicator.id == user_indicator_id).one_or_none()
    if not ui or not (ui.definition_json and str(ui.definition_json).strip()):
        raise HTTPException(status_code=404, detail="指标不存在或非 DSL 类型")

    try:
        parsed = parse_and_validate_definition(db, json.loads(ui.definition_json))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"指标定义无效: {e}") from e

    # 找到子线的显示名称
    sub_display_name = sub_key
    for s in parsed.sub_indicators:
        if str(s.get("key")) == sub_key:
            sub_display_name = str(s.get("name") or sub_key)
            break

    # 加载 K 线（含预热）
    warmup_start = start_date - timedelta(days=_WARMUP_DAYS)
    bars_all = (
        db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == sym.id,
            BarDaily.trade_date >= warmup_start,
            BarDaily.trade_date <= end_date,
        )
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if len(bars_all) < 5:
        raise HTTPException(status_code=400, detail="该股票日线数据不足")

    # 计算指标序列
    try:
        series = compute_definition_series(parsed, bars_all)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"指标计算失败: {e}") from e

    ind_seq = series.get(sub_key) or []

    # 只返回 [start_date, end_date] 的数据点
    bars_out: list[TradeChartBarPoint] = []
    indicator_out: list[TradeChartIndicatorPoint] = []
    for i, b in enumerate(bars_all):
        if b.trade_date < start_date:
            continue
        bars_out.append(TradeChartBarPoint(
            time=b.trade_date.isoformat(),
            open=float(b.open),
            high=float(b.high),
            low=float(b.low),
            close=float(b.close),
        ))
        v = ind_seq[i] if i < len(ind_seq) else None
        if v is not None and isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            v = None
        indicator_out.append(TradeChartIndicatorPoint(
            time=b.trade_date.isoformat(),
            value=float(v) if v is not None else None,
        ))

    return TradeChartOut(
        bars=bars_out,
        indicator=indicator_out,
        sub_key=sub_key,
        sub_display_name=sub_display_name,
    )
