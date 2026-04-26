"""股票回测 API（路径前缀 /api/backtest/）。

提供一个接口：POST /api/backtest/run
- 基于用户自定义指标（DSL 类型），在 [start_date, end_date] 内对全市场逐日选股回测
- 买入：指标值满足 buy_op 条件时建仓（按指标值从大到小，填满空仓位）
- 卖出：指标值满足 sell_op 条件时平仓
- 最多同时持有 max_positions 只，等额分配初始资金
- 返回资金曲线、交易记录、总收益率、最大回撤、胜率等绩效指标

核心回测逻辑在 app/services/backtest_runner.py 中实现。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    BacktestRunIn,
    BacktestRunOut,
    BacktestTradeRow,
    BacktestEquityPoint,
)
from app.services.backtest_runner import run_backtest

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
