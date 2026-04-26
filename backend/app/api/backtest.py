"""股票回测 API（路径前缀 /api/backtest/）。

提供两个接口：

POST /api/backtest/run
- 基于用户自定义指标（DSL 类型），在 [start_date, end_date] 内对全市场逐日选股回测

GET /api/backtest/trade-chart
- 返回单只股票在回测区间内的 K 线 + 指标子线序列
- 供前端 Drawer 里的"触发条件验证图"使用
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BarDaily, Symbol, UserIndicator
from app.schemas import (
    BacktestRunIn,
    BacktestRunOut,
    BacktestTradeRow,
    BacktestEquityPoint,
    TradeChartOut,
    TradeChartBarPoint,
    TradeChartIndicatorPoint,
)
from app.services.backtest_runner import run_backtest
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition
from app.services.screening_runner import _WARMUP_DAYS
import json

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestRunOut)
def backtest_run(body: BacktestRunIn, db: Session = Depends(get_db)):
    """在 [start_date, end_date] 对全市场执行条件选股回测。

    回测流程（每个交易日）：
    1. 持仓中满足卖出条件的股票 → 以收盘价平仓
    2. 满足买入条件的股票（未持有）→ 按指标值降序，填满仓位空槽 → 以收盘价建仓
    3. 记录当日总权益（现金 + 持仓市值）

    Args（均在 body 中传入，见 BacktestRunIn schema）：
        start_date / end_date: 回测时间范围。
        user_indicator_id: 使用哪个已保存的自定义指标（必须是 DSL 类型）。
        sub_key: 指标子线 key。
        buy_op / buy_threshold: 买入条件。
        sell_op / sell_threshold: 卖出条件。
        initial_capital: 初始资金（元）。
        max_positions: 最多同时持仓数（默认 3）。
        max_scan: 每轮最多扫描股票数（默认 3000）。

    Returns:
        BacktestRunOut，包含资金曲线、交易记录、绩效指标。
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


@router.get("/trade-chart", response_model=TradeChartOut)
def backtest_trade_chart(
    ts_code: str = Query(...),
    user_indicator_id: int = Query(..., ge=1),
    sub_key: str = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
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
        import math
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
