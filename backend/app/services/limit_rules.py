"""
A 股涨跌停与板块比例（复盘 / 后续可给回测对齐用）。

产品口径（顺序不可颠倒）：
1. 先判 ST → 涨跌停按 5%；
2. 再判新股（上市首日）→ 不参与涨跌停计数（limit_pct 视为 None）；
3. 再按交易所板块 → 10% / 20% / 30%（沪深主板/中小板类 10%，创业板/科创板 20%，北交所 30%）。

跌停与涨停对称：同一 effective_limit_pct 用相同容差做近似触及判断。
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


def is_ipo_trade_day(list_date: Optional[date], trade_date: date) -> bool:
    if list_date is None:
        return False
    return list_date == trade_date


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
) -> Optional[float]:
    if is_st_stock(name):
        return 0.05
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
