"""后端指标计算：为回测引擎提供每日指标值字典。

返回格式：{trade_date: {"MA5": v, "MA10": v, ..., "K": v, "close": v, ...}}
所有参数锁定为默认值，MA 以 MA5/10/20/30/60 作为独立子指标。
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence


# ---------- 原始数据行（只需 OHLCV）----------
class _Bar:
    __slots__ = ("trade_date", "open", "high", "low", "close", "volume", "turnover_rate")

    def __init__(self, trade_date: date, open_: float, high: float, low: float,
                 close: float, volume: float, turnover_rate: Optional[float]):
        self.trade_date = trade_date
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.turnover_rate = turnover_rate if turnover_rate is not None else 0.0


def compute_indicators(
    bars: Sequence,  # list[BarDaily] ORM 对象或 _Bar
    start_date: date | None = None,
) -> dict[date, dict[str, float]]:
    """计算全部锁定参数的指标，返回 start_date 之后（含）的日期字典。

    Args:
        bars: 按 trade_date 升序的 K 线序列（含冷启动数据）。
        start_date: 只返回 >= start_date 的结果；None 则返回全部。
    """
    n = len(bars)
    if n == 0:
        return {}

    dates = [b.trade_date for b in bars]
    closes = [float(b.close) for b in bars]
    highs  = [float(b.high)  for b in bars]
    lows   = [float(b.low)   for b in bars]
    opens  = [float(b.open)  for b in bars]
    vols   = [float(b.volume) for b in bars]
    turns  = [float(b.turnover_rate) if hasattr(b, "turnover_rate") and b.turnover_rate is not None else 0.0 for b in bars]

    result: dict[date, dict[str, float]] = {}

    # ---- MA ----
    ma_periods = [5, 10, 20, 30, 60]
    ma_vals: Dict[int, List[Optional[float]]] = {}
    for p in ma_periods:
        arr: List[Optional[float]] = []
        for i in range(n):
            if i < p - 1:
                arr.append(None)
            else:
                arr.append(sum(closes[i - p + 1 : i + 1]) / p)
        ma_vals[p] = arr

    # ---- EXPMA（12 / 26）----
    def _ema(src: list[float], period: int) -> list[float]:
        alpha = 2.0 / (period + 1)
        out: list[float] = []
        for i, v in enumerate(src):
            if i == 0:
                out.append(v)
            else:
                out.append(alpha * v + (1 - alpha) * out[-1])
        return out

    expma12 = _ema(closes, 12)
    expma26 = _ema(closes, 26)

    # ---- BOLL（N=20, sigma=2）----
    boll_n = 20
    boll_mid: List[Optional[float]] = []
    boll_upper: List[Optional[float]] = []
    boll_lower: List[Optional[float]] = []
    for i in range(n):
        if i < boll_n - 1:
            boll_mid.append(None); boll_upper.append(None); boll_lower.append(None)
        else:
            sl = closes[i - boll_n + 1 : i + 1]
            mid = sum(sl) / boll_n
            std = (sum((x - mid) ** 2 for x in sl) / boll_n) ** 0.5
            boll_mid.append(mid)
            boll_upper.append(mid + 2 * std)
            boll_lower.append(mid - 2 * std)

    # ---- MACD（12/26/9）----
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    macd_bar = [2 * (d - e) for d, e in zip(dif, dea)]

    # ---- KDJ（N=9, M1=3, M2=3）----
    kdj_n = 9
    k_vals: list[float] = []
    d_vals: list[float] = []
    j_vals: list[float] = []
    for i in range(n):
        lo = min(lows[max(0, i - kdj_n + 1) : i + 1])
        hi = max(highs[max(0, i - kdj_n + 1) : i + 1])
        rsv = (closes[i] - lo) / (hi - lo) * 100 if hi != lo else 50.0
        prev_k = k_vals[-1] if k_vals else 50.0
        prev_d = d_vals[-1] if d_vals else 50.0
        k = prev_k * (2 / 3) + rsv * (1 / 3)
        d = prev_d * (2 / 3) + k * (1 / 3)
        j = 3 * k - 2 * d
        k_vals.append(k); d_vals.append(d); j_vals.append(j)

    # ---- 组装结果 ----
    for i, d in enumerate(dates):
        if start_date and d < start_date:
            continue
        row: dict[str, float] = {
            "close":        closes[i],
            "open":         opens[i],
            "high":         highs[i],
            "low":          lows[i],
            "volume":       vols[i],
            "turnover_rate": turns[i],
            "EXPMA12":      expma12[i],
            "EXPMA26":      expma26[i],
            "DIF":          dif[i],
            "DEA":          dea[i],
            "MACD柱":       macd_bar[i],
            "K":            k_vals[i],
            "D":            d_vals[i],
            "J":            j_vals[i],
        }
        for p in ma_periods:
            v = ma_vals[p][i]
            if v is not None:
                row[f"MA{p}"] = v
        if boll_mid[i] is not None:
            row["MID"]   = boll_mid[i]    # type: ignore[assignment]
            row["UPPER"] = boll_upper[i]  # type: ignore[assignment]
            row["LOWER"] = boll_lower[i]  # type: ignore[assignment]
        result[d] = row

    return result
