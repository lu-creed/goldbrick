"""
全市场条件选股回测引擎（0.0.4-dev：加入交易成本、滑点、整手、次日开盘成交、基准对比）。

回测逻辑（每个交易日顺序执行）：
  1. 持仓中满足卖出条件的股票 → 在 exec_date 用 exec_price 平仓
  2. 满足买入条件的股票（未持有）→ 按指标值降序，填满仓位空槽 → 在 exec_date 用 exec_price 建仓
  3. 记录当日总权益（现金 + 所有持仓市值）

exec_date / exec_price：
  - execution_price="close"：T 日信号 → exec_date=T, exec_price=close
  - execution_price="next_open"：T 日信号 → exec_date=T+1 下一交易日, exec_price=open
    若 T+1 为一字涨停（买）或一字跌停（卖）则跳过（买单放弃、卖单延后到下一日评估）。

成本口径：
  - 佣金：双边按 max(commission_min, gross * commission_rate)，gross 用含滑点成交价
  - 印花税：仅卖出，sell_gross * stamp_duty_rate
  - 滑点：买单上浮 slippage_bps/10000，卖单下压同样幅度
  - 整手：shares = floor(position_capital / exec_price_with_slip / lot_size) * lot_size；<1 手放弃
  - 未平仓强平：仍使用 close 模式以最后一个交易日的收盘价结算（含滑点+全套费用）

基准曲线：
  - benchmark_index 为 None/数据缺失 → benchmark_curve 为空、benchmark_return_pct/alpha_pct 置 None
  - 否则按 trading_dates 对齐取指数收盘，归一化到 initial_capital

优化策略：
- 预热期计算：一次性加载 (start_date - 400天) 到 end_date 的全部 qfq K 线，
  对每只股票计算完整指标序列，避免逐日重复查询。
- 分批查询：每批 450 只（复用 screening_runner 的 _CHUNK），避免 SQL IN 过长。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import BarDaily, Symbol, UserIndicator
from app.services.limit_rules import (
    board_limit_pct,
    effective_limit_pct,
    is_one_word_limit_down,
    is_one_word_limit_up,
)
from app.services.screening_runner import (
    _CHUNK,
    _COMPARE_OPS,
    _WARMUP_DAYS,
    _cmp,
    _load_bars_grouped,
)
from app.services.strategy_engine import (
    CompiledStrategy,
    compile_strategy,
    eval_strategy_on_series,
    legacy_to_logic,
)
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition


def _all_stocks_with_bars_in_range(
    db: Session, start: date, end: date, max_scan: int
) -> list[dict[str, Any]]:
    """查询在 [start, end] 内至少有一天日线的全部股票（asset_type='stock'）。

    返回字段：symbol_id, ts_code, name, market, exchange, list_date。
    结果按 ts_code 升序排列，最多取 max_scan 只。
    """
    sql = text("""
        SELECT DISTINCT s.id AS symbol_id, s.ts_code, m.name,
                        m.market, m.exchange, m.list_date
        FROM bars_daily b
        JOIN symbols s ON s.id = b.symbol_id
        JOIN instrument_meta m ON m.ts_code = s.ts_code AND m.asset_type = 'stock'
        WHERE b.trade_date >= :start AND b.trade_date <= :end
        ORDER BY s.ts_code
        LIMIT :limit
    """)
    rows = db.execute(sql, {"start": start, "end": end, "limit": max_scan}).fetchall()
    return [dict(r._mapping) for r in rows]


def _trading_dates_in_range(
    db: Session, start: date, end: date
) -> list[date]:
    """返回 [start, end] 内所有有日线记录的交易日（升序）。

    只看 bars_daily 中实际存在的日期，不依赖节假日日历。
    """
    sql = text("""
        SELECT DISTINCT trade_date FROM bars_daily
        WHERE trade_date >= :start AND trade_date <= :end
        ORDER BY trade_date ASC
    """)
    rows = db.execute(sql, {"start": start, "end": end}).fetchall()
    result: list[date] = []
    for r in rows:
        d = r[0]
        if isinstance(d, str):
            d = date.fromisoformat(d)
        result.append(d)
    return result


def _load_benchmark_curve(
    db: Session,
    benchmark_index: Optional[str],
    trading_dates: list[date],
    initial_capital: float,
) -> tuple[list[dict[str, Any]], Optional[float]]:
    """加载基准指数并按 trading_dates 对齐、归一化到 initial_capital。

    Returns:
        (benchmark_curve, benchmark_return_pct)
        - benchmark_curve 为空 + benchmark_return_pct=None 表示基准不可用（未填/未同步/无数据）
        - drawdown_pct 字段复用：相对基准自身峰值的回撤（与策略的字段口径一致）
    """
    if not benchmark_index or not trading_dates:
        return [], None
    ts_code = benchmark_index.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == ts_code).one_or_none()
    if not sym:
        return [], None

    start = trading_dates[0]
    end = trading_dates[-1]
    rows = (
        db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == sym.id,
            BarDaily.trade_date >= start,
            BarDaily.trade_date <= end,
        )
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not rows:
        return [], None

    close_by_date = {b.trade_date: float(b.close) for b in rows}
    base_close: Optional[float] = None
    curve: list[dict[str, Any]] = []
    peak_equity = initial_capital
    for d in trading_dates:
        c = close_by_date.get(d)
        if c is None or c <= 0:
            # 停牌/节假日缺数据 → 沿用上一点权益，保持曲线长度和策略一致
            if curve:
                prev_eq = curve[-1]["equity"]
            else:
                prev_eq = initial_capital
            curve.append({"date": d.isoformat(), "equity": prev_eq, "drawdown_pct": 0.0})
            continue
        if base_close is None:
            base_close = c
        equity = initial_capital * (c / base_close)
        if equity > peak_equity:
            peak_equity = equity
        dd = (equity - peak_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        curve.append({"date": d.isoformat(), "equity": round(equity, 2), "drawdown_pct": round(dd, 3)})

    if base_close is None:
        return [], None
    final_equity = curve[-1]["equity"]
    bench_ret = (final_equity - initial_capital) / initial_capital * 100.0
    return curve, round(bench_ret, 3)


def _close_on_date(bars: list[Any], d: date) -> Optional[float]:
    """从已排序的 K 线列表中取指定日期的收盘价，找不到返回 None。"""
    for b in reversed(bars):
        if b.trade_date == d:
            return float(b.close)
        if b.trade_date < d:
            break
    return None


def _bar_on_date(bars: list[Any], d: date) -> Optional[Any]:
    """从已排序的 K 线列表中取指定日期那根 bar，找不到返回 None。"""
    for b in reversed(bars):
        if b.trade_date == d:
            return b
        if b.trade_date < d:
            break
    return None


def _build_signal_series_multi(
    buy_compiled: CompiledStrategy,
    sell_compiled: CompiledStrategy,
    bars: list[Any],
    start: date,
    end: date,
) -> dict[date, tuple[bool, bool, float]]:
    """一只股票 [start, end] 每个交易日的买/卖信号（多条件版）。

    - buy_compiled / sell_compiled 各跑一次 eval_strategy_on_series
    - 合并为 {trade_date: (buy_hit, sell_hit, primary_val)}
    - primary_val 取自 buy 侧主条件值（用于建仓排序）；仅有 sell 信号的日期用 sell primary_val 占位

    Returns:
        {trade_date: (buy_signal, sell_signal, primary_val)}
    """
    if len(bars) < 5:
        return {}
    try:
        buy_map = eval_strategy_on_series(buy_compiled, bars, start, end)
        sell_map = eval_strategy_on_series(sell_compiled, bars, start, end)
    except (ValueError, Exception):  # noqa: BLE001
        return {}

    result: dict[date, tuple[bool, bool, float]] = {}
    for d, (buy_hit, buy_val) in buy_map.items():
        sell_hit, _sv = sell_map.get(d, (False, None))
        result[d] = (bool(buy_hit), bool(sell_hit), float(buy_val))
    for d, (sell_hit, sell_val) in sell_map.items():
        if d not in result:
            # buy 主条件无值的日期：入场不会触发（buy_hit=False），保留 sell 信号以便平仓
            result[d] = (False, bool(sell_hit), float(sell_val))
    return result


def _compute_commission(gross: float, commission_rate: float, commission_min: float) -> float:
    """双边佣金：按成交金额的 rate 收取，不低于 commission_min。"""
    if gross <= 0:
        return 0.0
    return max(commission_min, gross * commission_rate)


def run_backtest(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    # 新路径：多条件双路 logic
    buy_logic: Optional[dict] = None,
    sell_logic: Optional[dict] = None,
    # 老路径：单条件入参；仅当 buy_logic/sell_logic 都为空时生效
    user_indicator_id: Optional[int] = None,
    sub_key: Optional[str] = None,
    buy_op: str = "gt",
    buy_threshold: float = 0.0,
    sell_op: str = "lt",
    sell_threshold: float = 0.0,
    initial_capital: float = 100_000.0,
    max_positions: int = 3,
    max_scan: int = 3000,
    commission_rate: float = 0.00025,
    commission_min: float = 5.0,
    stamp_duty_rate: float = 0.001,
    slippage_bps: float = 10.0,
    lot_size: int = 100,
    execution_price: Literal["close", "next_open"] = "next_open",
    benchmark_index: Optional[str] = "000300.SH",
) -> dict[str, Any]:
    """对全市场（最多 max_scan 只）在 [start_date, end_date] 执行条件选股回测。

    支持两种入参方式：
      - 多条件（新）：传 buy_logic 和 sell_logic（各自含 conditions/groups/combiner/primary）
      - 单条件（老）：传 user_indicator_id + sub_key + buy_op/buy_threshold + sell_op/sell_threshold
        内部 legacy_to_logic 转成等价单条件双路 logic，走同一条统一引擎

    Args:
        start_date / end_date: 回测时间范围。
        buy_logic / sell_logic: 多条件买卖策略（二者必须同时给出或同时省略）。
        user_indicator_id / sub_key / buy_op / buy_threshold / sell_op / sell_threshold: 老参数。
        initial_capital, max_positions, max_scan: 资金/仓位配置。
        commission_rate / commission_min: 双边佣金率 / 每笔最低（元）。
        stamp_duty_rate: 印花税率（仅卖出）。
        slippage_bps: 滑点基点（1bp=0.01%）。
        lot_size: 整手大小（默认 100 股）。
        execution_price: "close"=T 日收盘成交；"next_open"=T+1 开盘成交。
        benchmark_index: 基准指数 ts_code。

    Returns:
        包含 equity_curve, trades, 绩效指标、策略 echo-back 等字段的字典。

    Raises:
        ValueError: 指标不存在、子线无效、日期范围无数据等。
    """
    if start_date >= end_date:
        raise ValueError("start_date 必须早于 end_date")
    if execution_price not in {"close", "next_open"}:
        raise ValueError("execution_price 须为 close 或 next_open")

    slip = float(slippage_bps) / 10000.0  # 1bp = 0.01% = 0.0001

    # ---- 解析 buy / sell 两路 logic ----
    is_multi = buy_logic is not None or sell_logic is not None
    if is_multi:
        if buy_logic is None or sell_logic is None:
            raise ValueError("buy_logic 和 sell_logic 必须同时提供")
    else:
        if user_indicator_id is None:
            raise ValueError("必须提供 buy_logic/sell_logic 或 user_indicator_id")
        if buy_op not in _COMPARE_OPS:
            raise ValueError(f"buy_op 须为 {_COMPARE_OPS}")
        if sell_op not in _COMPARE_OPS:
            raise ValueError(f"sell_op 须为 {_COMPARE_OPS}")
        buy_logic = legacy_to_logic(user_indicator_id, sub_key, buy_op, buy_threshold)
        sell_logic = legacy_to_logic(user_indicator_id, sub_key, sell_op, sell_threshold)

    try:
        buy_compiled = compile_strategy(db, buy_logic)
        sell_compiled = compile_strategy(db, sell_logic)
    except ValueError:
        raise

    # 回测当前仅支持 DSL 指标（legacy expr 向量化成本高，保留未来优化）
    for ind in (*buy_compiled.indicators.values(), *sell_compiled.indicators.values()):
        if not ind.is_dsl:
            raise ValueError(f"回测当前仅支持 DSL 指标；指标 {ind.code} 是 legacy expr，请迁移到 DSL")

    # 获取交易日列表
    trading_dates = _trading_dates_in_range(db, start_date, end_date)
    if not trading_dates:
        raise ValueError("指定日期范围内无交易日数据，请先同步")
    date_to_idx = {d: i for i, d in enumerate(trading_dates)}

    # 获取参与回测的股票列表
    stocks = _all_stocks_with_bars_in_range(db, start_date, end_date, max_scan)
    if not stocks:
        raise ValueError("指定日期范围内无股票日线数据，请先同步")

    # ---- 预计算阶段：为每只股票计算整段指标序列（qfq 口径）----
    # warmup_start 向前 _WARMUP_DAYS 天，保证指标有足够历史 K 线
    warmup_start = start_date - timedelta(days=_WARMUP_DAYS)

    # all_signals[ts_code][trade_date] = (buy_signal, sell_signal, indicator_value)
    all_signals: dict[str, dict[date, tuple[bool, bool, float]]] = {}
    # 保存每只股票各交易日的 qfq bar（供 exec_price 取值、一字板判定、持仓市值计算）
    all_bars_map: dict[str, list[Any]] = {}
    # 保存每只股票的板块元数据（用于涨跌停限价计算）
    stock_meta: dict[str, dict[str, Any]] = {}

    scanned_stock_set: set[str] = set()

    for i in range(0, len(stocks), _CHUNK):
        chunk = stocks[i : i + _CHUNK]
        ids = [int(r["symbol_id"]) for r in chunk]
        grouped = _load_bars_grouped(db, ids, warmup_start, end_date, adj_mode="qfq")

        for r in chunk:
            sid = int(r["symbol_id"])
            ts_code = r["ts_code"]
            bars = grouped.get(sid, [])
            if not bars:
                continue

            scanned_stock_set.add(ts_code)
            all_bars_map[ts_code] = bars
            stock_meta[ts_code] = {
                "name": r.get("name"),
                "market": r.get("market"),
                "exchange": r.get("exchange"),
                "list_date": r.get("list_date"),
            }

            sigs = _build_signal_series_multi(
                buy_compiled=buy_compiled,
                sell_compiled=sell_compiled,
                bars=bars,
                start=start_date,
                end=end_date,
            )
            if sigs:
                all_signals[ts_code] = sigs

    # ---- 一字板判定辅助：对指定交易日计算该股的有效涨跌停幅度 ----
    def _limit_pct_on(ts_code: str, exec_d: date) -> Optional[float]:
        meta = stock_meta.get(ts_code, {})
        list_date = meta.get("list_date")
        if list_date is not None and not isinstance(list_date, date):
            # SQL 驱动偶尔返回字符串
            try:
                list_date = date.fromisoformat(str(list_date))
            except ValueError:
                list_date = None
        # 用 bars_map 里 ≤ exec_d 的行数粗估 days_since_ipo_trade（新股豁免判断）
        bars = all_bars_map.get(ts_code, [])
        day_idx = None
        if list_date:
            day_idx = sum(1 for b in bars if b.trade_date <= exec_d and b.trade_date >= list_date)
            if day_idx <= 0:
                day_idx = None
        return effective_limit_pct(
            meta.get("name"),
            meta.get("market"),
            meta.get("exchange"),
            ts_code,
            exec_d,
            list_date if isinstance(list_date, date) else None,
            days_since_ipo_trade=day_idx,
        )

    # ---- 模拟交易阶段 ----
    # 每仓位目标资金 = 初始资金 / 最大持仓数
    position_capital = initial_capital / max_positions
    cash = initial_capital
    commission_cost_total = 0.0

    # 当前持仓：{ts_code: {buy_date, buy_price, shares, cost_basis, buy_fee, buy_trigger_val}}
    holdings: dict[str, dict[str, Any]] = {}

    # 已完成的交易记录
    closed_trades: list[dict[str, Any]] = []

    # 资金曲线
    equity_curve: list[dict[str, Any]] = []
    peak_equity = initial_capital

    # ts_code → 名称映射（用于交易记录）
    ts_to_name = {r["ts_code"]: r.get("name") for r in stocks}

    def _exec_bar(ts_code: str, signal_date: date) -> tuple[Optional[date], Optional[Any], Optional[float]]:
        """根据 execution_price 模式定位成交 bar。

        Returns:
            (exec_date, exec_bar, exec_price_base) 或 (None, None, None) 表示无法成交。
        """
        bars = all_bars_map.get(ts_code, [])
        if execution_price == "close":
            bar = _bar_on_date(bars, signal_date)
            if bar is None:
                return None, None, None
            return signal_date, bar, float(bar.close)
        # next_open：到下一交易日的开盘
        idx = date_to_idx.get(signal_date)
        if idx is None or idx + 1 >= len(trading_dates):
            return None, None, None
        next_d = trading_dates[idx + 1]
        bar = _bar_on_date(bars, next_d)
        if bar is None:
            return None, None, None
        return next_d, bar, float(bar.open)

    def _prev_close_before(ts_code: str, d: date) -> Optional[float]:
        bars = all_bars_map.get(ts_code, [])
        prev = None
        for b in bars:
            if b.trade_date < d:
                prev = float(b.close)
            elif b.trade_date == d:
                return prev
            else:
                break
        return prev

    for d in trading_dates:
        # -- 1. 卖出：处理持仓中满足卖出信号的股票 --
        to_sell = []
        for ts_code, _pos in holdings.items():
            sigs_map = all_signals.get(ts_code, {})
            sig = sigs_map.get(d)
            if sig is None:
                continue
            _buy_sig, sell_sig, _val = sig
            if sell_sig:
                to_sell.append(ts_code)

        for ts_code in to_sell:
            exec_d, exec_bar, exec_price_base = _exec_bar(ts_code, d)
            if exec_d is None or exec_bar is None or exec_price_base is None or exec_price_base <= 0:
                # 无法在 exec_date 成交（如 next_open 模式下 d 是最后一日），延到回测结束强平
                continue
            # 一字跌停不能卖出 → 延后（保持持仓，等下一 d 再评估）
            prev_close = _prev_close_before(ts_code, exec_d)
            limit_pct = _limit_pct_on(ts_code, exec_d)
            if prev_close is not None and limit_pct is not None:
                if is_one_word_limit_down(
                    float(exec_bar.open), float(exec_bar.high), float(exec_bar.low), prev_close, limit_pct
                ):
                    continue
            pos = holdings.pop(ts_code)
            sell_price = exec_price_base * (1.0 - slip)  # 卖单滑点：价格下压
            gross = pos["shares"] * sell_price
            fee = _compute_commission(gross, commission_rate, commission_min)
            stamp = gross * stamp_duty_rate
            net_proceeds = gross - fee - stamp
            cash += net_proceeds
            commission_cost_total += fee + stamp
            # 盈亏 = 卖出净回款 - 买入总成本（现金口径）
            pnl = net_proceeds - pos["cost_basis"]
            pnl_pct = pnl / pos["cost_basis"] * 100.0 if pos["cost_basis"] > 0 else 0.0
            sell_sig_data = all_signals.get(ts_code, {}).get(d)
            closed_trades.append({
                "ts_code": ts_code,
                "name": ts_to_name.get(ts_code),
                "buy_date": pos["buy_date"],
                "buy_price": pos["buy_price"],
                "shares": pos["shares"],
                "sell_date": exec_d.isoformat(),
                "sell_price": round(sell_price, 4),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 3),
                "buy_trigger_val": pos.get("buy_trigger_val"),
                "sell_trigger_val": round(sell_sig_data[2], 4) if sell_sig_data else None,
                "cost": round(pos.get("buy_fee", 0.0) + fee + stamp, 2),
            })

        # -- 2. 买入：在仓位有空槽时建仓 --
        open_slots = max_positions - len(holdings)
        if open_slots > 0:
            # 收集当日满足买入条件且未持有的候选股
            candidates: list[tuple[str, float]] = []  # (ts_code, indicator_value)
            for ts_code, sigs_map in all_signals.items():
                if ts_code in holdings:
                    continue  # 已持有，不重复买入
                sig = sigs_map.get(d)
                if sig is None:
                    continue
                buy_sig, _sell_sig, val = sig
                if buy_sig:
                    candidates.append((ts_code, val))

            # 按指标值从大到小排序，取前 open_slots 只
            candidates.sort(key=lambda x: x[1], reverse=True)
            for ts_code, trigger_val in candidates[:open_slots]:
                if cash < position_capital * 0.99:  # 现金不足（允许 1% 浮动误差）
                    break
                exec_d, exec_bar, exec_price_base = _exec_bar(ts_code, d)
                if exec_d is None or exec_bar is None or exec_price_base is None or exec_price_base <= 0:
                    continue
                # 一字涨停不能买入 → 跳过
                prev_close = _prev_close_before(ts_code, exec_d)
                limit_pct = _limit_pct_on(ts_code, exec_d)
                if prev_close is not None and limit_pct is not None:
                    if is_one_word_limit_up(
                        float(exec_bar.open), float(exec_bar.high), float(exec_bar.low), prev_close, limit_pct
                    ):
                        continue
                buy_price = exec_price_base * (1.0 + slip)  # 买单滑点：价格上浮
                # 整手：先扣除预估佣金再算买得起的整手数（粗估佣金用 position_capital 的 rate，确保买入后现金>=0）
                est_fee = max(commission_min, position_capital * commission_rate)
                affordable = position_capital - est_fee
                if affordable <= 0:
                    continue
                shares = int(math.floor(affordable / buy_price / lot_size)) * lot_size
                if shares <= 0:
                    continue
                gross = shares * buy_price
                fee = _compute_commission(gross, commission_rate, commission_min)
                cost_basis = gross + fee
                if cost_basis > cash + 1e-6:
                    # 罕见：精度抖动导致超出现金。降一手再试。
                    shares -= lot_size
                    if shares <= 0:
                        continue
                    gross = shares * buy_price
                    fee = _compute_commission(gross, commission_rate, commission_min)
                    cost_basis = gross + fee
                    if cost_basis > cash + 1e-6:
                        continue
                cash -= cost_basis
                commission_cost_total += fee
                holdings[ts_code] = {
                    "buy_date": exec_d.isoformat(),
                    "buy_price": round(buy_price, 4),
                    "shares": shares,
                    "cost_basis": cost_basis,
                    "buy_fee": fee,
                    "buy_trigger_val": round(trigger_val, 4),
                }

        # -- 3. 计算当日总权益（市值按 d 当日 qfq close）--
        position_value = 0.0
        for ts_code, pos in holdings.items():
            cp = _close_on_date(all_bars_map.get(ts_code, []), d)
            if cp is not None:
                position_value += pos["shares"] * cp
            else:
                position_value += pos["cost_basis"]  # 找不到则以成本计

        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        drawdown_pct = (equity - peak_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0

        equity_curve.append({
            "date": d.isoformat(),
            "equity": round(equity, 2),
            "drawdown_pct": round(drawdown_pct, 3),
        })

    # ---- 回测结束：将仍持有的仓位按最后一日收盘价强平（按成本模型结算）----
    last_date = trading_dates[-1] if trading_dates else end_date
    for ts_code, pos in list(holdings.items()):
        close_price = _close_on_date(all_bars_map.get(ts_code, []), last_date)
        if close_price is None or close_price <= 0:
            close_price = pos["buy_price"]
        sell_price = close_price * (1.0 - slip)
        gross = pos["shares"] * sell_price
        fee = _compute_commission(gross, commission_rate, commission_min)
        stamp = gross * stamp_duty_rate
        net_proceeds = gross - fee - stamp
        pnl = net_proceeds - pos["cost_basis"]
        pnl_pct = pnl / pos["cost_basis"] * 100.0 if pos["cost_basis"] > 0 else 0.0
        closed_trades.append({
            "ts_code": ts_code,
            "name": ts_to_name.get(ts_code),
            "buy_date": pos["buy_date"],
            "buy_price": pos["buy_price"],
            "shares": pos["shares"],
            "sell_date": None,          # None 表示回测结束时仍持有
            "sell_price": round(sell_price, 4),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 3),
            "buy_trigger_val": pos.get("buy_trigger_val"),
            "sell_trigger_val": None,
            "cost": round(pos.get("buy_fee", 0.0) + fee + stamp, 2),
        })
        # 注意：此处仅用于绩效口径，不真正把现金回池（最终净值口径以 equity_curve 最后一点为准）

    # ---- 基础绩效指标 ----
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    total_return_pct = (final_equity - initial_capital) / initial_capital * 100.0
    max_drawdown_pct = min((pt["drawdown_pct"] for pt in equity_curve), default=0.0)

    # 胜率：已平仓（有 sell_date）的交易中盈利的比例
    finished = [t for t in closed_trades if t["sell_date"] is not None]
    if finished:
        win_count = sum(1 for t in finished if (t["pnl"] or 0) > 0)
        win_rate = round(win_count / len(finished) * 100.0, 1)
    else:
        win_rate = None

    # ---- 高级绩效指标 ----
    trading_day_count = len(trading_dates)

    # 年化收益率：按 252 交易日/年折算
    if trading_day_count > 1 and initial_capital > 0:
        ann_factor = 252.0 / trading_day_count
        annualized_return = round(((final_equity / initial_capital) ** ann_factor - 1) * 100.0, 3)
    else:
        annualized_return = None

    # 夏普比率：日收益率序列的 (均值 / 标准差) × sqrt(252)；无风险利率假设为 0
    if len(equity_curve) > 2:
        daily_rets = [
            (equity_curve[i]["equity"] - equity_curve[i - 1]["equity"]) / equity_curve[i - 1]["equity"]
            for i in range(1, len(equity_curve))
            if equity_curve[i - 1]["equity"] > 0
        ]
        if daily_rets:
            avg_ret = sum(daily_rets) / len(daily_rets)
            std_ret = (sum((r - avg_ret) ** 2 for r in daily_rets) / len(daily_rets)) ** 0.5
            sharpe_ratio = round(avg_ret / std_ret * (252 ** 0.5), 3) if std_ret > 1e-10 else None
        else:
            sharpe_ratio = None
    else:
        sharpe_ratio = None

    # 卡玛比率 = 年化收益率 / |最大回撤|（最大回撤为负数）
    if annualized_return is not None and max_drawdown_pct < -0.01:
        calmar_ratio = round(annualized_return / abs(max_drawdown_pct), 3)
    else:
        calmar_ratio = None

    # 盈亏比、平均盈亏幅度、最大单笔盈亏
    wins = [t for t in finished if (t["pnl_pct"] or 0) > 0]
    losses = [t for t in finished if (t["pnl_pct"] or 0) <= 0]
    total_win = len(wins)
    total_loss = len(losses)

    total_win_pnl = sum(t["pnl_pct"] or 0 for t in wins)
    total_loss_pnl = sum(abs(t["pnl_pct"] or 0) for t in losses)

    avg_win_pct = round(total_win_pnl / total_win, 3) if total_win > 0 else None
    avg_loss_pct = round(-total_loss_pnl / total_loss, 3) if total_loss > 0 else None
    max_win_pct = round(max((t["pnl_pct"] or 0) for t in wins), 3) if wins else None
    max_loss_pct = round(min((t["pnl_pct"] or 0) for t in finished), 3) if finished else None
    profit_factor = round(total_win_pnl / total_loss_pnl, 3) if total_loss_pnl > 1e-6 else None

    # 平均持仓自然日天数（只统计已平仓的交易）
    holding_days_list: list[int] = []
    for t in finished:
        if t.get("buy_date") and t.get("sell_date"):
            buy_d = date.fromisoformat(t["buy_date"])
            sell_d = date.fromisoformat(t["sell_date"])
            holding_days_list.append((sell_d - buy_d).days)
    avg_holding_days = round(sum(holding_days_list) / len(holding_days_list), 1) if holding_days_list else None

    # ---- 基准对比 ----
    bench_curve, bench_ret = _load_benchmark_curve(db, benchmark_index, trading_dates, initial_capital)
    alpha_pct: Optional[float] = None
    if bench_ret is not None:
        alpha_pct = round(total_return_pct - bench_ret, 3)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "total_return_pct": round(total_return_pct, 3),
        "max_drawdown_pct": round(max_drawdown_pct, 3),
        "total_trades": len(closed_trades),
        "win_rate": win_rate,
        "scanned_stocks": len(scanned_stock_set),
        "equity_curve": equity_curve,
        "trades": closed_trades,
        "note": None,
        "annualized_return": annualized_return,
        "sharpe_ratio": sharpe_ratio,
        "calmar_ratio": calmar_ratio,
        "profit_factor": profit_factor,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "max_win_pct": max_win_pct,
        "max_loss_pct": max_loss_pct,
        "avg_holding_days": avg_holding_days,
        "total_win": total_win,
        "total_loss": total_loss,
        # 基准与成本回显
        "benchmark_curve": bench_curve,
        "benchmark_index": (benchmark_index or None) if bench_curve else None,
        "benchmark_return_pct": bench_ret,
        "alpha_pct": alpha_pct,
        "commission_cost_total": round(commission_cost_total, 2),
        "adj_mode": "qfq",
        "execution_price": execution_price,
        "commission_rate": commission_rate,
        "commission_min": commission_min,
        "stamp_duty_rate": stamp_duty_rate,
        "slippage_bps": slippage_bps,
        "lot_size": lot_size,
        # 多条件 echo-back：多条件路径会同时回填 buy_logic/sell_logic；老路径仍然回填（1 条件版）
        "is_multi": is_multi,
        "buy_logic": buy_logic,
        "sell_logic": sell_logic,
    }
