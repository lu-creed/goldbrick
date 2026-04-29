"""
A 股涨跌停与板块比例（复盘 / 回测 / K 线连板均走这里）。

产品口径（顺序不可颠倒）：
1. 先判 ST → 涨跌停按 5%；
2. 再判新股无涨跌幅窗口：
   - 主板、北交所：仅上市首日（第 1 个交易日）
   - 创业板（300 开头）、科创板（688 开头）：上市前 5 个交易日
   在窗口内返回 limit_pct=None，表示「不适用涨跌停」，不计入连板与涨跌停家数；
3. 再按交易所板块 → 10% / 20% / 30%（沪深主板/中小板类 10%，创业板/科创板 20%，北交所 30%）。

跌停与涨停对称：同一 effective_limit_pct 用相同容差做近似触及判断。

调用方：
- services/replay_daily.py：单日复盘与情绪趋势，查 SQL 时传入上市后第几个交易日
- services/derivatives.py：bars_daily 连续涨跌停天数回算（按股票顺序累加计数）
- services/backtest_runner.py：一字板过滤（是否允许成交）
"""

from __future__ import annotations

from datetime import date
from typing import Optional

_LIMIT_TOUCH_RATIO = 0.98


def is_st_stock(name: Optional[str]) -> bool:
    if not name or not str(name).strip():
        return False
    s = str(name).strip().upper()
    return s.startswith("*ST") or s.startswith("ST")


def _coerce_date(v: object) -> Optional[date]:
    """SQLite 驱动偶尔把 DATE 列读成字符串，这里统一转成 date（无法解析时返回 None）。"""
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


def is_ipo_trade_day(list_date: Optional[date], trade_date: date) -> bool:
    """是否为上市首日（保留作向后兼容，新代码请用 days_since_ipo_trade 窗口判断）。"""
    ld = _coerce_date(list_date)
    td = _coerce_date(trade_date)
    if ld is None or td is None:
        return False
    return ld == td


def ipo_no_limit_trade_days(
    market: Optional[str],
    exchange: Optional[str],
    ts_code: str,
) -> int:
    """该板块新股上市后无涨跌幅限制的交易日数量。

    - 创业板（300 开头 / market 含「创业板」）：5
    - 科创板（688 开头 / market 含「科创板」）：5
    - 北交所（.BJ / BSE / market 含「北交」）：1
    - 其他（主板等）：1

    返回值永远 ≥ 1；调用方据此判断 1 <= day_idx <= N 时豁免涨跌幅。
    """
    code = (ts_code or "").strip().upper()
    ex = (exchange or "").strip().upper()
    m = (market or "").strip()

    if code.startswith("300") or "创业板" in m:
        return 5
    if code.startswith("688") or "科创板" in m:
        return 5
    if ex == "BSE" or code.endswith(".BJ") or "北交" in m:
        return 1
    return 1


def board_limit_pct(
    market: Optional[str],
    exchange: Optional[str],
    ts_code: str,
) -> float:
    code = (ts_code or "").strip().upper()
    ex = (exchange or "").strip().upper()
    m = (market or "").strip()

    if ex == "BSE" or code.endswith(".BJ"):
        return 0.30
    if "北交" in m:
        return 0.30
    if "创业板" in m or m == "创业板":
        return 0.20
    if "科创板" in m or m == "科创板":
        return 0.20
    if code.startswith("688"):
        return 0.20
    if code.startswith("300"):
        return 0.20
    return 0.10


def effective_limit_pct(
    name: Optional[str],
    market: Optional[str],
    exchange: Optional[str],
    ts_code: str,
    trade_date: date,
    list_date: Optional[date],
    days_since_ipo_trade: Optional[int] = None,
) -> Optional[float]:
    """当日生效的涨跌幅限制（正比例），None 表示无限制（新股窗口内）。

    days_since_ipo_trade: 交易日次序（从上市首日起算，首日=1）。
      - 不传（None）：退回旧逻辑，仅判上市首日是否恰好 == trade_date（复盘侧的自然日近似）
      - 传入：若 1 <= day_idx <= ipo_no_limit_trade_days(...)，返回 None 豁免；否则按板块。
    """
    if is_st_stock(name):
        return 0.05
    if days_since_ipo_trade is not None:
        n = ipo_no_limit_trade_days(market, exchange, ts_code)
        if 1 <= days_since_ipo_trade <= n:
            return None
    else:
        if is_ipo_trade_day(list_date, trade_date):
            return None
    return board_limit_pct(market, exchange, ts_code)


def hits_limit_up(high: float, prev_close: float, limit_pct: Optional[float]) -> bool:
    if limit_pct is None or prev_close <= 0:
        return False
    return (high - prev_close) / prev_close >= limit_pct * _LIMIT_TOUCH_RATIO


def hits_limit_down(low: float, prev_close: float, limit_pct: Optional[float]) -> bool:
    if limit_pct is None or prev_close <= 0:
        return False
    return (low - prev_close) / prev_close <= -limit_pct * _LIMIT_TOUCH_RATIO


def pct_change(close: float, prev_close: float) -> Optional[float]:
    if prev_close <= 0:
        return None
    return (close - prev_close) / prev_close


def is_one_word_limit_up(
    open_: float,
    high: float,
    low: float,
    prev_close: float,
    limit_pct: Optional[float],
) -> bool:
    """一字涨停：开 == 高 == 低 且相对昨收涨幅达到板块涨停阈值（容差内）。

    用于回测的一字板过滤：买入时遇一字涨停应跳过，因为实盘难以成交。
    """
    if limit_pct is None or prev_close <= 0:
        return False
    if not (abs(open_ - high) < 1e-6 and abs(open_ - low) < 1e-6):
        return False
    return (open_ - prev_close) / prev_close >= limit_pct * _LIMIT_TOUCH_RATIO


def is_one_word_limit_down(
    open_: float,
    high: float,
    low: float,
    prev_close: float,
    limit_pct: Optional[float],
) -> bool:
    """一字跌停：开 == 高 == 低 且相对昨收跌幅达到板块跌停阈值（容差内）。

    用于回测的一字板过滤：卖出时遇一字跌停应跳过，等下一交易日重新评估。
    """
    if limit_pct is None or prev_close <= 0:
        return False
    if not (abs(open_ - high) < 1e-6 and abs(open_ - low) < 1e-6):
        return False
    return (open_ - prev_close) / prev_close <= -limit_pct * _LIMIT_TOUCH_RATIO
