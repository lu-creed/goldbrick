"""股票回测 API（路径前缀 /api/backtest/）。

接口列表：
  POST /api/backtest/run              执行一次回测，结果自动保存到历史
  GET  /api/backtest/records          获取回测历史列表（分页）
  GET  /api/backtest/records/{id}     获取单条历史详情（含完整结果）
  DELETE /api/backtest/records/{id}   删除单条历史记录
  GET  /api/backtest/trade-chart      获取单笔交易的 K 线验证图
"""

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BacktestRecord, BarDaily, Symbol, UserIndicator
from app.auth import get_current_user
from app.schemas import (
    BacktestEquityPoint,
    BacktestRecordDetail,
    BacktestRecordItem,
    BacktestRunIn,
    BacktestRunOut,
    BacktestTradeRow,
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
    )


@router.post("/run", response_model=BacktestRunOut)
def backtest_run(body: BacktestRunIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """在 [start_date, end_date] 对全市场执行条件选股回测，并自动保存本次结果到历史。

    回测流程（每个交易日）：
    1. 持仓中满足卖出条件的股票 → 以收盘价平仓
    2. 满足买入条件的股票（未持有）→ 按指标值降序，填满仓位空槽 → 以收盘价建仓
    3. 记录当日总权益（现金 + 持仓市值）
    4. 回测完成后将结果写入 backtest_records 表（自动保存）
    """
    try:
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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    out = _build_run_out(raw)

    # ── 自动保存到历史记录 ────────────────────────────────────────
    indicator_name = ""
    indicator_code = ""
    ind = db.query(UserIndicator).filter(UserIndicator.id == body.user_indicator_id).first()
    if ind:
        indicator_name = ind.display_name
        indicator_code = ind.code

    record = BacktestRecord(
        user_id=current_user.id,
        created_at=datetime.utcnow(),
        start_date=str(body.start_date),
        end_date=str(body.end_date),
        user_indicator_id=body.user_indicator_id,
        indicator_name=indicator_name,
        indicator_code=indicator_code,
        sub_key=body.sub_key,
        buy_op=body.buy_op,
        buy_threshold=body.buy_threshold,
        sell_op=body.sell_op,
        sell_threshold=body.sell_threshold,
        initial_capital=body.initial_capital,
        max_positions=body.max_positions,
        total_return_pct=out.total_return_pct,
        max_drawdown_pct=out.max_drawdown_pct,
        total_trades=out.total_trades,
        win_rate=out.win_rate,
        annualized_return=out.annualized_return,
        sharpe_ratio=out.sharpe_ratio,
        result_json=out.model_dump_json(),   # 完整结果序列化为 JSON
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
    return rows


@router.get("/records/{record_id}", response_model=BacktestRecordDetail)
def backtest_record_detail(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单条回测历史的详情，包含完整回测结果（资金曲线、交易记录、绩效指标）。"""
    row = db.query(BacktestRecord).filter(
        BacktestRecord.id == record_id,
        BacktestRecord.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="回测记录不存在")

    # 将 result_json 反序列化为 BacktestRunOut 对象
    result: Optional[BacktestRunOut] = None
    try:
        result = BacktestRunOut.model_validate_json(row.result_json)
    except Exception:
        log.exception("解析回测历史 result_json 失败，id=%s", record_id)

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
        result=result,
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
