from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BarDaily, Symbol
from app.services.adj import apply_adj, build_adj_map, get_latest_factor
from app.services.indicator_compute import compute_indicators
from app.schemas import (
    BacktestDailyPoint,
    BuyOnceBacktestRequest,
    BuyOnceBacktestResponse,
    BuySellBacktestRequest,
    BuySellBacktestResponse,
    ConditionBuyRequest,
    ConditionBuyResponse,
    ConditionBuyDailyPoint,
    IndicatorRef,
)

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _is_limit_up(curr: float, prev_close: float) -> bool:
    if prev_close <= 0:
        return False
    return (curr - prev_close) / prev_close >= 0.098


def _is_limit_down(curr: float, prev_close: float) -> bool:
    if prev_close <= 0:
        return False
    return (curr - prev_close) / prev_close <= -0.098


@router.post("/buy-once", response_model=BuyOnceBacktestResponse)
def buy_once_backtest(body: BuyOnceBacktestRequest, db: Session = Depends(get_db)):
    code = body.ts_code.strip().upper()
    if body.start_date > body.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    if not (body.start_date <= body.buy_date <= body.end_date):
        raise HTTPException(status_code=400, detail="buy_date must be in [start_date, end_date]")
    if body.buy_price <= 0:
        raise HTTPException(status_code=400, detail="buy_price must be > 0")

    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="unknown ts_code")

    bars = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == sym.id, BarDaily.trade_date >= body.start_date, BarDaily.trade_date <= body.end_date)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        raise HTTPException(status_code=400, detail="no bar data in selected range")

    if body.buy_date not in {b.trade_date for b in bars}:
        raise HTTPException(status_code=400, detail="buy_date is not a trading day in selected range")

    # 加载复权因子（用户选择复权模式时生效）
    adj_map = build_adj_map(db, sym.id) if body.adj != "none" else {}
    latest_factor = get_latest_factor(adj_map)

    def adj_price(price: float, trade_date) -> float:
        return apply_adj(price, trade_date, body.adj, adj_map, latest_factor)

    cost = body.buy_price * body.buy_qty
    if cost > body.initial_cash:
        raise HTTPException(status_code=400, detail="initial_cash is not enough for buy order")

    cash = body.initial_cash
    bought = False
    prev_total = body.initial_cash
    peak_total = body.initial_cash
    max_drawdown = 0.0
    daily: list[BacktestDailyPoint] = []

    for i, b in enumerate(bars):
        prev_close = adj_price(float(bars[i - 1].close), bars[i - 1].trade_date) if i > 0 else adj_price(float(b.close), b.trade_date)
        if not bought and b.trade_date == body.buy_date:
            # V1.0.4: 涨停一字板近似不可买入（开盘价和最低价都在涨停价附近）。
            if _is_limit_up(adj_price(float(b.open), b.trade_date), prev_close) and _is_limit_up(adj_price(float(b.low), b.trade_date), prev_close):
                raise HTTPException(status_code=400, detail="buy_date 当日疑似涨停一字板，默认无法买入")
            cash -= cost
            bought = True

        close = adj_price(float(b.close), b.trade_date)
        stock_value = body.buy_qty * close if bought else 0.0
        total_asset = cash + stock_value
        daily_pnl = total_asset - prev_total
        cum_return = (total_asset - body.initial_cash) / body.initial_cash if body.initial_cash > 0 else 0.0

        if total_asset > peak_total:
            peak_total = total_asset
        drawdown = (peak_total - total_asset) / peak_total if peak_total > 0 else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown

        daily.append(
            BacktestDailyPoint(
                trade_date=b.trade_date,
                close=close,
                stock_value=stock_value,
                cash_value=cash,
                total_asset=total_asset,
                daily_pnl=daily_pnl,
                cum_return=cum_return,
            )
        )
        prev_total = total_asset

    return BuyOnceBacktestResponse(
        ts_code=code,
        start_date=body.start_date,
        end_date=body.end_date,
        buy_date=body.buy_date,
        buy_price=body.buy_price,
        buy_qty=body.buy_qty,
        initial_cash=body.initial_cash,
        remaining_cash=cash,
        max_drawdown=max_drawdown,
        daily=daily,
    )


@router.post("/buy-sell", response_model=BuySellBacktestResponse)
def buy_sell_backtest(body: BuySellBacktestRequest, db: Session = Depends(get_db)):
    code = body.ts_code.strip().upper()
    if body.start_date > body.end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")
    if not (body.start_date <= body.buy_date <= body.end_date):
        raise HTTPException(status_code=400, detail="buy_date must be in [start_date, end_date]")
    if body.buy_price <= 0:
        raise HTTPException(status_code=400, detail="buy_price must be > 0")
    if body.sell_target_price is not None and body.sell_target_price <= 0:
        raise HTTPException(status_code=400, detail="sell_target_price must be > 0")
    if body.sell_target_date is not None and body.sell_target_date < body.buy_date:
        raise HTTPException(status_code=400, detail="sell_target_date must be >= buy_date")
    has_sell_conditions = any([body.sell_target_price is not None, body.sell_target_return is not None, body.sell_target_date is not None])

    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="unknown ts_code")

    bars = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == sym.id, BarDaily.trade_date >= body.start_date, BarDaily.trade_date <= body.end_date)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        raise HTTPException(status_code=400, detail="no bar data in selected range")
    trade_dates = {b.trade_date for b in bars}
    if body.buy_date not in trade_dates:
        raise HTTPException(status_code=400, detail="buy_date is not a trading day in selected range")

    # 加载复权因子
    adj_map = build_adj_map(db, sym.id) if body.adj != "none" else {}
    latest_factor = get_latest_factor(adj_map)

    def adj_price(price: float, trade_date) -> float:
        return apply_adj(price, trade_date, body.adj, adj_map, latest_factor)

    cost = body.buy_price * body.buy_qty
    if cost > body.initial_cash:
        raise HTTPException(status_code=400, detail="initial_cash is not enough for buy order")

    cash = body.initial_cash
    holding_qty = 0
    sold = False
    sell_date = None
    sell_price = None
    sell_reason = None
    prev_total = body.initial_cash
    peak_total = body.initial_cash
    max_drawdown = 0.0
    daily: list[BacktestDailyPoint] = []

    for i, b in enumerate(bars):
        d = b.trade_date
        prev_close = adj_price(float(bars[i - 1].close), bars[i - 1].trade_date) if i > 0 else adj_price(float(b.close), b.trade_date)
        close = adj_price(float(b.close), b.trade_date)
        trigger_price = close
        if holding_qty == 0 and d == body.buy_date:
            # V1.0.4: 涨停一字板近似不可买入。
            if _is_limit_up(adj_price(float(b.open), d), prev_close) and _is_limit_up(adj_price(float(b.low), d), prev_close):
                raise HTTPException(status_code=400, detail="buy_date 当日疑似涨停一字板，默认无法买入")
            holding_qty = body.buy_qty
            cash -= cost

        # T+1: 买入当日不能卖出
        can_sell_today = holding_qty > 0 and d >= (body.buy_date + timedelta(days=1))
        if can_sell_today and not sold and has_sell_conditions:
            conds: list[bool] = []
            reasons: list[str] = []
            if body.sell_target_price is not None:
                ok = trigger_price >= body.sell_target_price
                conds.append(ok)
                if ok:
                    reasons.append("price")
            if body.sell_target_return is not None:
                ret = (trigger_price - body.buy_price) / body.buy_price
                ok = ret >= body.sell_target_return
                conds.append(ok)
                if ok:
                    reasons.append("return")
            if body.sell_target_date is not None:
                ok = d >= body.sell_target_date
                conds.append(ok)
                if ok:
                    reasons.append("date")

            should_sell = all(conds) if body.sell_logic == "and" else any(conds)
            if should_sell:
                # V1.0.4: 跌停一字板近似不可卖出（开盘价和最高价都在跌停价附近）。
                if _is_limit_down(adj_price(float(b.open), d), prev_close) and _is_limit_down(adj_price(float(b.high), d), prev_close):
                    should_sell = False
                    reasons = []
                else:
                    cash += holding_qty * trigger_price
                    holding_qty = 0
                    sold = True
                    sell_date = d
                    sell_price = trigger_price
                    sell_reason = ",".join(reasons) if reasons else "condition"

        stock_value = holding_qty * close
        total_asset = cash + stock_value
        daily_pnl = total_asset - prev_total
        cum_return = (total_asset - body.initial_cash) / body.initial_cash if body.initial_cash > 0 else 0.0
        if total_asset > peak_total:
            peak_total = total_asset
        drawdown = (peak_total - total_asset) / peak_total if peak_total > 0 else 0.0
        if drawdown > max_drawdown:
            max_drawdown = drawdown
        daily.append(
            BacktestDailyPoint(
                trade_date=d,
                close=close,
                stock_value=stock_value,
                cash_value=cash,
                total_asset=total_asset,
                daily_pnl=daily_pnl,
                cum_return=cum_return,
            )
        )
        prev_total = total_asset

    return BuySellBacktestResponse(
        ts_code=code,
        start_date=body.start_date,
        end_date=body.end_date,
        buy_date=body.buy_date,
        sell_date=sell_date,
        sell_price=sell_price,
        sell_reason=sell_reason,
        buy_price=body.buy_price,
        buy_qty=body.buy_qty,
        initial_cash=body.initial_cash,
        remaining_cash=cash,
        max_drawdown=max_drawdown,
        daily=daily,
    )


# ─────────────────────── V1.0.6 条件买入 ───────────────────────

def _resolve_ref(ref: IndicatorRef, indicators: dict[str, float]) -> Optional[float]:
    """将 IndicatorRef 解析为当日浮点值；无法解析返回 None。"""
    if ref.kind == "number":
        return ref.value
    # kind == "indicator"
    return indicators.get(ref.sub_name or "", None)


def _eval_condition(body_timing, indicators: dict[str, float], bar_high: float, bar_low: float) -> bool:
    """判断当日是否满足买入时机条件。"""
    if body_timing.condition_type == "price":
        p = body_timing.price
        if p is None:
            return False
        return bar_low < p < bar_high
    # indicator 条件
    if body_timing.left is None or body_timing.operator is None or body_timing.right is None:
        return False
    lv = _resolve_ref(body_timing.left, indicators)
    rv = _resolve_ref(body_timing.right, indicators)
    if lv is None or rv is None:
        return False
    if body_timing.operator == "gt":
        return lv > rv
    if body_timing.operator == "lt":
        return lv < rv
    return abs(lv - rv) < 1e-9  # eq


def _calc_buy_qty(body_qty, cash: float, buy_price: float) -> int:
    """按定量或比例计算本次可买股数（100股单位，向下取整，现金不能为负）。"""
    if buy_price <= 0:
        return 0
    if body_qty.type == "fixed":
        qty = (body_qty.fixed_qty or 0)
        qty = (qty // 100) * 100  # 保证100倍数
        if qty * buy_price > cash:
            # 资金不够，按比例最多能买多少手
            qty = int(cash / buy_price / 100) * 100
        return max(0, qty)
    # ratio
    ratio = min(1.0, max(0.0, body_qty.ratio or 0.0))
    affordable = int(cash * ratio / buy_price / 100) * 100
    return max(0, affordable)


@router.post("/condition-buy", response_model=ConditionBuyResponse)
def condition_buy_backtest(body: ConditionBuyRequest, db: Session = Depends(get_db)):
    code = body.ts_code.strip().upper()
    if body.start_date > body.end_date:
        raise HTTPException(400, "start_date must be <= end_date")

    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        raise HTTPException(404, "unknown ts_code")

    # ① 拉取冷启动 bars（start 前 180 天）+ 回测区间
    from datetime import date as _date
    warmup_start = _date.fromordinal(body.start_date.toordinal() - 180)
    all_bars = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == sym.id,
                BarDaily.trade_date >= warmup_start,
                BarDaily.trade_date <= body.end_date)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not all_bars:
        raise HTTPException(400, "no bar data in selected range")

    # ② 复权
    adj_map = build_adj_map(db, sym.id) if body.adj != "none" else {}
    latest_factor = get_latest_factor(adj_map)

    def ap(price: float, td) -> float:
        return apply_adj(price, td, body.adj, adj_map, latest_factor)

    # 构建复权后的 bars 结构（供 compute_indicators 使用）
    class _AdjBar:
        __slots__ = ("trade_date", "open", "high", "low", "close", "volume", "turnover_rate")
        def __init__(self, b):
            self.trade_date   = b.trade_date
            self.open         = ap(float(b.open),  b.trade_date)
            self.high         = ap(float(b.high),  b.trade_date)
            self.low          = ap(float(b.low),   b.trade_date)
            self.close        = ap(float(b.close), b.trade_date)
            self.volume       = float(b.volume)
            self.turnover_rate = float(b.turnover_rate) if b.turnover_rate is not None else None

    adj_bars = [_AdjBar(b) for b in all_bars]

    # ③ 计算指标
    ind_map = compute_indicators(adj_bars, start_date=None)

    # 只保留 start_date 内的交易日列表
    trade_dates_all = [b.trade_date for b in adj_bars]
    backtest_bars = [b for b in adj_bars if b.trade_date >= body.start_date]
    if not backtest_bars:
        raise HTTPException(400, "no bar data in backtest period")

    # ④ 回测引擎
    cash = body.initial_cash
    holdings: list[dict] = []   # 每笔买入：{qty, cost_price, buy_date}
    sold = False
    sell_date = None
    sell_price_result = None
    sell_reason = None
    buy_count = 0
    prev_total = body.initial_cash
    peak_total = body.initial_cash
    max_drawdown = 0.0
    daily: list[ConditionBuyDailyPoint] = []
    has_sell_cond = any([
        body.sell_target_price is not None,
        body.sell_target_return is not None,
        body.sell_target_date is not None,
    ])

    for i, bar in enumerate(backtest_bars):
        td = bar.trade_date
        ind_today = ind_map.get(td, {})

        # 确定时间偏移对应的 check_day
        offset = min(0, body.buy_timing.time_offset)  # 确保 <= 0
        if offset == 0:
            check_bar = bar
        else:
            # 在 trade_dates_all 中找 td 的索引，往前 |offset| 个交易日
            try:
                td_idx = trade_dates_all.index(td)
            except ValueError:
                td_idx = 0
            check_idx = max(0, td_idx + offset)
            check_td = trade_dates_all[check_idx]
            check_bar = adj_bars[check_idx] if check_idx < len(adj_bars) else bar

        ind_check = ind_map.get(check_bar.trade_date, {})

        # 评估买入条件（还没持仓时允许买入，或者多次买入均允许）
        if not sold:
            cond_met = _eval_condition(body.buy_timing, ind_check, check_bar.high, check_bar.low)
            if cond_met:
                # 确定买入价
                if body.buy_price.type == "fixed":
                    bp = body.buy_price.fixed_price or 0.0
                else:
                    bp = ind_today.get(body.buy_price.sub_name or "", 0.0)

                # 合理性校验：价格在今日高低价区间内，且非一字板
                prev_close_v = float(backtest_bars[i - 1].close) if i > 0 else float(bar.close)
                if bp > 0 and bar.low < bp < bar.high:
                    if not (_is_limit_up(bar.open, prev_close_v) and _is_limit_up(bar.low, prev_close_v)):
                        qty = _calc_buy_qty(body.buy_qty, cash, bp)
                        if qty >= 100:
                            cost = qty * bp
                            cash -= cost
                            holdings.append({"qty": qty, "cost_price": bp, "buy_date": td})
                            buy_count += 1

        # 计算当日持仓
        total_qty = sum(h["qty"] for h in holdings)
        stock_value = total_qty * bar.close
        total_asset = cash + stock_value
        daily_pnl = total_asset - prev_total
        cum_return = (total_asset - body.initial_cash) / body.initial_cash if body.initial_cash > 0 else 0.0

        if total_asset > peak_total:
            peak_total = total_asset
        dd = (peak_total - total_asset) / peak_total if peak_total > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        # 卖出判断（T+1 约束：买入当日不卖，至少有过一次买入）
        if total_qty > 0 and not sold and has_sell_cond:
            earliest_buy = min(h["buy_date"] for h in holdings)
            can_sell = td > earliest_buy
            if can_sell:
                conds: list[bool] = []
                reasons: list[str] = []
                avg_cost = sum(h["qty"] * h["cost_price"] for h in holdings) / total_qty

                if body.sell_target_price is not None:
                    ok = bar.close >= body.sell_target_price
                    conds.append(ok)
                    if ok: reasons.append("price")
                if body.sell_target_return is not None:
                    ret = (bar.close - avg_cost) / avg_cost if avg_cost > 0 else 0.0
                    ok = ret >= body.sell_target_return
                    conds.append(ok)
                    if ok: reasons.append("return")
                if body.sell_target_date is not None:
                    ok = td >= body.sell_target_date
                    conds.append(ok)
                    if ok: reasons.append("date")

                should_sell = all(conds) if body.sell_logic == "and" else any(conds)
                if should_sell:
                    prev_close_v2 = float(backtest_bars[i - 1].close) if i > 0 else float(bar.close)
                    if not (_is_limit_down(bar.open, prev_close_v2) and _is_limit_down(bar.high, prev_close_v2)):
                        cash += total_qty * bar.close
                        holdings = []
                        sold = True
                        sell_date = td
                        sell_price_result = bar.close
                        sell_reason = ",".join(reasons) if reasons else "condition"
                        stock_value = 0.0
                        total_asset = cash

        daily.append(ConditionBuyDailyPoint(
            trade_date=td,
            close=bar.close,
            holding_qty=total_qty,
            stock_value=stock_value,
            cash_value=cash,
            total_asset=total_asset,
            daily_pnl=daily_pnl,
            cum_return=cum_return,
        ))
        prev_total = total_asset

    final_total_qty = sum(h["qty"] for h in holdings)
    return ConditionBuyResponse(
        ts_code=code,
        start_date=body.start_date,
        end_date=body.end_date,
        initial_cash=body.initial_cash,
        remaining_cash=cash,
        buy_count=buy_count,
        sell_date=sell_date,
        sell_price=sell_price_result,
        sell_reason=sell_reason,
        max_drawdown=max_drawdown,
        daily=daily,
    )
