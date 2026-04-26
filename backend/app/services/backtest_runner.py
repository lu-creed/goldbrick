"""
全市场条件选股回测引擎。

回测逻辑（每个交易日顺序执行）：
  1. 检查持仓中满足卖出条件的股票 → 以当日收盘价平仓
  2. 检查全市场满足买入条件的股票 → 按指标值降序填满空仓位 → 以当日收盘价建仓
  3. 记录当日总权益（现金 + 所有持仓市值）

优化策略：
- 预热期计算：一次性加载 (start_date - 400天) 到 end_date 的全部 K 线，
  对每只股票计算完整指标序列，避免逐日重复查询。
- 分批查询：每批 450 只（复用 screening_runner 的 _CHUNK），避免 SQL IN 过长。

资金管理：
- 每个仓位固定资金 = initial_capital / max_positions
- 使用小数股（不强制 A 股 100 股整手）
- 平仓后现金归池，供后续买入使用
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import BarDaily, UserIndicator
from app.services.screening_runner import (
    _CHUNK,
    _COMPARE_OPS,
    _WARMUP_DAYS,
    _cmp,
    _load_bars_grouped,
)
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition


def _all_stocks_with_bars_in_range(
    db: Session, start: date, end: date, max_scan: int
) -> list[dict[str, Any]]:
    """查询在 [start, end] 内至少有一天日线的全部股票（asset_type='stock'）。

    返回字段：symbol_id, ts_code, name。
    结果按 ts_code 升序排列，最多取 max_scan 只。
    """
    sql = text("""
        SELECT DISTINCT s.id AS symbol_id, s.ts_code, m.name
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
    return [r[0] for r in rows]


def _close_on_date(bars: list[BarDaily], d: date) -> Optional[float]:
    """从已排序的 K 线列表中取指定日期的收盘价，找不到返回 None。"""
    for b in reversed(bars):
        if b.trade_date == d:
            return float(b.close)
        if b.trade_date < d:
            break
    return None


def _build_signal_series(
    parsed: Any,
    sub_key: str,
    bars: list[BarDaily],
    warmup_end: date,
    start: date,
    end: date,
    buy_op: str,
    buy_threshold: float,
    sell_op: str,
    sell_threshold: float,
) -> dict[date, tuple[bool, bool, float]]:
    """对一只股票计算 [start, end] 每个交易日的买入/卖出信号。

    先用 compute_definition_series 计算整段（预热期 + 回测期）的指标序列，
    然后按日期提取信号。

    Returns:
        {trade_date: (buy_signal, sell_signal, indicator_value)}
        只包含在 [start, end] 范围内且指标值有效的日期。
    """
    if len(bars) < 5:
        return {}
    try:
        series = compute_definition_series(parsed, bars)
    except (ValueError, Exception):  # noqa: BLE001
        return {}
    seq = series.get(sub_key)
    if not seq:
        return {}

    # bars 与 seq 一一对应（同等长度，按时间升序）
    result: dict[date, tuple[bool, bool, float]] = {}
    for i, bar in enumerate(bars):
        if bar.trade_date < start or bar.trade_date > end:
            continue
        if i >= len(seq):
            break
        v = seq[i]
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            continue
        val = float(v)
        buy_sig = _cmp(val, buy_op, buy_threshold)
        sell_sig = _cmp(val, sell_op, sell_threshold)
        result[bar.trade_date] = (buy_sig, sell_sig, val)
    return result


def run_backtest(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    user_indicator_id: int,
    sub_key: str,
    buy_op: str,
    buy_threshold: float,
    sell_op: str,
    sell_threshold: float,
    initial_capital: float,
    max_positions: int,
    max_scan: int,
) -> dict[str, Any]:
    """对全市场（最多 max_scan 只）在 [start_date, end_date] 执行条件选股回测。

    Args:
        start_date / end_date: 回测时间范围。
        user_indicator_id: 使用哪个已保存的自定义指标（必须是 DSL 类型）。
        sub_key: 指标子线 key（DSL 指标必须传，旧版 expr 传空字符串）。
        buy_op / buy_threshold: 买入条件（指标值满足此条件时建仓）。
        sell_op / sell_threshold: 卖出条件（指标值满足此条件时平仓）。
        initial_capital: 初始资金（元）。
        max_positions: 最多同时持有几只股票。
        max_scan: 最多扫描几只股票。

    Returns:
        包含 equity_curve, trades, 绩效指标等字段的字典。

    Raises:
        ValueError: 指标不存在、子线无效、日期范围无数据等。
    """
    if buy_op not in _COMPARE_OPS:
        raise ValueError(f"buy_op 须为 {_COMPARE_OPS}")
    if sell_op not in _COMPARE_OPS:
        raise ValueError(f"sell_op 须为 {_COMPARE_OPS}")
    if start_date >= end_date:
        raise ValueError("start_date 必须早于 end_date")

    # 加载并校验自定义指标
    ui = db.query(UserIndicator).filter(UserIndicator.id == user_indicator_id).one_or_none()
    if not ui:
        raise ValueError("自定义指标不存在")

    is_dsl = bool(ui.definition_json and str(ui.definition_json).strip())
    if not is_dsl:
        raise ValueError("回测当前仅支持 DSL 指标，旧版 expr 指标请先迁移")

    try:
        parsed = parse_and_validate_definition(db, json.loads(ui.definition_json))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"指标定义无效: {e}") from e

    # 确定实际使用的 sub_key
    sk_valid = {
        str(s.get("key"))
        for s in parsed.sub_indicators
        if s.get("use_in_screening") and not s.get("auxiliary_only")
    }
    if not sk_valid:
        raise ValueError("该指标没有可用于回测的子线（请勾选子线「选股/回测」且非仅辅助）")
    sk_in = (sub_key or "").strip()
    if not sk_in:
        sub_key = sorted(sk_valid)[0]
    elif sk_in not in sk_valid:
        raise ValueError(f"子线 {sk_in!r} 未参与选股或仅为辅助线")

    # 获取交易日列表
    trading_dates = _trading_dates_in_range(db, start_date, end_date)
    if not trading_dates:
        raise ValueError("指定日期范围内无交易日数据，请先同步")

    # 获取参与回测的股票列表
    stocks = _all_stocks_with_bars_in_range(db, start_date, end_date, max_scan)
    if not stocks:
        raise ValueError("指定日期范围内无股票日线数据，请先同步")

    # ---- 预计算阶段：为每只股票计算整段指标序列 ----
    # warmup_start 向前 _WARMUP_DAYS 天，保证指标有足够历史 K 线
    warmup_start = start_date - timedelta(days=_WARMUP_DAYS)

    # buy_signals[ts_code][trade_date] = (buy_signal, sell_signal, indicator_value)
    all_signals: dict[str, dict[date, tuple[bool, bool, float]]] = {}
    # 保存每只股票各交易日的收盘价，用于计算持仓市值
    all_bars_map: dict[str, list[BarDaily]] = {}

    scanned_stock_set: set[str] = set()

    for i in range(0, len(stocks), _CHUNK):
        chunk = stocks[i : i + _CHUNK]
        ids = [int(r["symbol_id"]) for r in chunk]
        grouped = _load_bars_grouped(db, ids, warmup_start, end_date)

        id_to_tscode = {int(r["symbol_id"]): r["ts_code"] for r in chunk}

        for r in chunk:
            sid = int(r["symbol_id"])
            ts_code = r["ts_code"]
            bars = grouped.get(sid, [])
            if not bars:
                continue

            scanned_stock_set.add(ts_code)
            all_bars_map[ts_code] = bars

            sigs = _build_signal_series(
                parsed=parsed,
                sub_key=sub_key,
                bars=bars,
                warmup_end=start_date - timedelta(days=1),
                start=start_date,
                end=end_date,
                buy_op=buy_op,
                buy_threshold=buy_threshold,
                sell_op=sell_op,
                sell_threshold=sell_threshold,
            )
            if sigs:
                all_signals[ts_code] = sigs

    # ---- 模拟交易阶段 ----
    # 每仓位固定资金 = 初始资金 / 最大持仓数
    position_capital = initial_capital / max_positions
    cash = initial_capital

    # 当前持仓：{ts_code: {buy_date, buy_price, shares}}
    holdings: dict[str, dict[str, Any]] = {}

    # 已完成的交易记录
    closed_trades: list[dict[str, Any]] = []

    # 资金曲线
    equity_curve: list[dict[str, Any]] = []
    peak_equity = initial_capital

    # ts_code → 名称映射（用于交易记录）
    ts_to_name = {r["ts_code"]: r.get("name") for r in stocks}

    for d in trading_dates:
        # -- 1. 卖出：检查持仓中满足卖出条件的股票 --
        to_sell = []
        for ts_code, pos in holdings.items():
            sigs_map = all_signals.get(ts_code, {})
            sig = sigs_map.get(d)
            if sig is None:
                continue
            _buy_sig, sell_sig, _val = sig
            if sell_sig:
                to_sell.append(ts_code)

        for ts_code in to_sell:
            pos = holdings.pop(ts_code)
            # 取当日收盘价作为平仓价
            close_price = _close_on_date(all_bars_map.get(ts_code, []), d)
            if close_price is None:
                close_price = pos["buy_price"]  # 找不到收盘价时保守处理：以成本价平
            sell_value = pos["shares"] * close_price
            cash += sell_value
            pnl = sell_value - position_capital
            pnl_pct = pnl / position_capital * 100.0
            closed_trades.append({
                "ts_code": ts_code,
                "name": ts_to_name.get(ts_code),
                "buy_date": pos["buy_date"],
                "buy_price": pos["buy_price"],
                "shares": pos["shares"],
                "sell_date": d.isoformat(),
                "sell_price": round(close_price, 4),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 3),
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
                    # 确认该股当日有收盘价
                    close_price = _close_on_date(all_bars_map.get(ts_code, []), d)
                    if close_price is not None and close_price > 0:
                        candidates.append((ts_code, val))

            # 按指标值从大到小排序，取前 open_slots 只
            candidates.sort(key=lambda x: x[1], reverse=True)
            for ts_code, _val in candidates[:open_slots]:
                if cash < position_capital * 0.99:  # 现金不足（允许 1% 浮动误差）
                    break
                close_price = _close_on_date(all_bars_map[ts_code], d)
                if close_price is None or close_price <= 0:
                    continue
                shares = position_capital / close_price
                cash -= position_capital
                holdings[ts_code] = {
                    "buy_date": d.isoformat(),
                    "buy_price": round(close_price, 4),
                    "shares": shares,
                }

        # -- 3. 计算当日总权益 --
        position_value = 0.0
        for ts_code, pos in holdings.items():
            cp = _close_on_date(all_bars_map.get(ts_code, []), d)
            if cp is not None:
                position_value += pos["shares"] * cp
            else:
                position_value += position_capital  # 找不到则以成本计

        equity = cash + position_value
        if equity > peak_equity:
            peak_equity = equity
        drawdown_pct = (equity - peak_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0

        equity_curve.append({
            "date": d.isoformat(),
            "equity": round(equity, 2),
            "drawdown_pct": round(drawdown_pct, 3),
        })

    # ---- 回测结束：将仍持有的仓位按最后一日收盘价强制平仓（计入交易记录）----
    last_date = trading_dates[-1] if trading_dates else end_date
    for ts_code, pos in holdings.items():
        close_price = _close_on_date(all_bars_map.get(ts_code, []), last_date)
        if close_price is None:
            close_price = pos["buy_price"]
        sell_value = pos["shares"] * close_price
        pnl = sell_value - position_capital
        pnl_pct = pnl / position_capital * 100.0
        closed_trades.append({
            "ts_code": ts_code,
            "name": ts_to_name.get(ts_code),
            "buy_date": pos["buy_date"],
            "buy_price": pos["buy_price"],
            "shares": pos["shares"],
            "sell_date": None,          # None 表示回测结束时仍持有，未实际卖出
            "sell_price": round(close_price, 4),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 3),
        })

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
    }
