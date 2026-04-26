"""
后端内置指标计算：将日线序列转换为每日指标值字典。

支持的指标（参数固定，不可调）：
  MA5/10/20/30/60  移动平均线（简单平均）
  EXPMA12/26       指数移动平均（EMA）
  BOLL（上轨/中轨/下轨）  布林带，N=20，sigma=2
  MACD（DIF/DEA/MACD柱） MACD指标，参数 12/26/9
  KDJ（K/D/J）     随机指标，N=9，M1=M2=3
  RSI6/12/24       相对强弱指数，N=6/12/24
  ATR14/ATR14_PCT  真实波动幅度，N=14
  WR10/WR6         威廉指标，N=10/6

返回格式：{trade_date: {"MA5": v, "MA10": v, ..., "K": v, "close": v, ...}}
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Sequence


# ---------- 原始数据行（只需 OHLCV）----------
class _Bar:
    """内部用途：统一 BarDaily ORM 对象和自定义对象的接口。"""
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
    """计算全部内置指标并返回按日期索引的字典。

    Args:
        bars: 按 trade_date 升序的 K 线序列。
              可包含冷启动数据（比 start_date 更早的数据），用于计算需要历史窗口的指标（如 MA60 需要 60 根）。
        start_date: 只返回 >= start_date 的结果；None 则返回全部。

    Returns:
        {trade_date: {"MA5": v, ..., "close": v, ...}}
        某些指标在历史不足时不会出现（如前 N-1 根没有 MA_N）。
    """
    n = len(bars)
    if n == 0:
        return {}

    # 提取各 OHLCV 字段为纯 float 列表，方便后续统一计算
    dates = [b.trade_date for b in bars]
    closes = [float(b.close) for b in bars]
    highs  = [float(b.high)  for b in bars]
    lows   = [float(b.low)   for b in bars]
    opens  = [float(b.open)  for b in bars]
    vols   = [float(b.volume) for b in bars]
    turns  = [float(b.turnover_rate) if hasattr(b, "turnover_rate") and b.turnover_rate is not None else 0.0 for b in bars]

    result: dict[date, dict[str, float]] = {}

    # ---- MA（简单移动平均）----
    # MA_N = 最近 N 天收盘价的算术平均；前 N-1 根不够计算，值为 None
    ma_periods = [5, 10, 20, 30, 60]
    ma_vals: Dict[int, List[Optional[float]]] = {}
    for p in ma_periods:
        arr: List[Optional[float]] = []
        for i in range(n):
            if i < p - 1:
                arr.append(None)  # 数据不足，跳过
            else:
                arr.append(sum(closes[i - p + 1 : i + 1]) / p)
        ma_vals[p] = arr

    # ---- EXPMA / EMA（指数移动平均）----
    # EMA 公式：EMA_i = alpha × price_i + (1 - alpha) × EMA_{i-1}
    # alpha = 2/(N+1)；第一天初始值=当天收盘价
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

    # ---- BOLL（布林带）N=20, sigma=2 ----
    # 中轨 MID = MA20；标准差 std = 最近 20 日总体标准差（除以 N）
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

    # ---- MACD（指数平滑异同移动平均）参数 12/26/9 ----
    # DIF = EMA12 - EMA26；DEA = DIF 的 9 日 EMA；MACD柱 = 2×(DIF-DEA)
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    macd_bar = [2 * (d - e) for d, e in zip(dif, dea)]

    # ---- KDJ（随机指标）N=9, M1=3, M2=3 ----
    # RSV = (收盘 - N日最低) / (N日最高 - N日最低) × 100
    # K = 2/3×前K + 1/3×RSV；D = 2/3×前D + 1/3×K；J = 3K - 2D
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

    # ---- RSI（相对强弱指数）N=6/12/24 ----
    # RSI = 100 - 100 / (1 + RS)，RS = N日内平均涨幅 / N日内平均跌幅
    # 使用 Wilder 平滑：首根用简单平均，后续用指数平滑 alpha=1/N
    def _rsi(closes_: list[float], period: int) -> list[Optional[float]]:
        out: List[Optional[float]] = [None] * n
        if n < period + 1:
            return out
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, n):
            diff = closes_[i] - closes_[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))

        # 首个 RSI 用简单平均初始化
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        if avg_loss == 0:
            out[period] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[period] = 100.0 - 100.0 / (1.0 + rs)

        # 后续用 Wilder 指数平滑（alpha = 1/N）
        for i in range(period + 1, n):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            if avg_loss == 0:
                out[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                out[i] = 100.0 - 100.0 / (1.0 + rs)
        return out

    rsi6  = _rsi(closes, 6)
    rsi12 = _rsi(closes, 12)
    rsi24 = _rsi(closes, 24)

    # ---- ATR（真实波动幅度）N=14 ----
    # True Range(i) = max(high-low, |high-prev_close|, |low-prev_close|)
    # ATR14 = Wilder 平滑均值（首根用简单平均）
    atr_period = 14
    tr_vals: list[float] = []
    for i in range(n):
        hl = highs[i] - lows[i]
        if i == 0:
            tr_vals.append(hl)
        else:
            hpc = abs(highs[i] - closes[i - 1])
            lpc = abs(lows[i] - closes[i - 1])
            tr_vals.append(max(hl, hpc, lpc))

    atr14: List[Optional[float]] = [None] * n
    if n >= atr_period:
        atr14[atr_period - 1] = sum(tr_vals[:atr_period]) / atr_period
        for i in range(atr_period, n):
            atr14[i] = (atr14[i - 1] * (atr_period - 1) + tr_vals[i]) / atr_period  # type: ignore[operator]

    atr14_pct: List[Optional[float]] = [
        round(v / closes[i] * 100.0, 4) if v is not None and closes[i] > 0 else None
        for i, v in enumerate(atr14)
    ]

    # ---- WR（威廉指标）N=10/6 ----
    # WR = (N日最高 - 收盘) / (N日最高 - N日最低) × (-100)
    # 取值 -100~0；越接近 0 越超买，越接近 -100 越超卖
    def _wr(period: int) -> list[Optional[float]]:
        out: List[Optional[float]] = []
        for i in range(n):
            if i < period - 1:
                out.append(None)
            else:
                hi = max(highs[i - period + 1 : i + 1])
                lo = min(lows[i - period + 1 : i + 1])
                if hi == lo:
                    out.append(-50.0)
                else:
                    out.append((hi - closes[i]) / (hi - lo) * (-100.0))
        return out

    wr10 = _wr(10)
    wr6  = _wr(6)

    # ---- 组装结果 ----
    for i, d in enumerate(dates):
        if start_date and d < start_date:
            continue
        row: dict[str, float] = {
            "close":         closes[i],
            "open":          opens[i],
            "high":          highs[i],
            "low":           lows[i],
            "volume":        vols[i],
            "turnover_rate": turns[i],
            "EXPMA12":       expma12[i],
            "EXPMA26":       expma26[i],
            "DIF":           dif[i],
            "DEA":           dea[i],
            "MACD柱":        macd_bar[i],
            "K":             k_vals[i],
            "D":             d_vals[i],
            "J":             j_vals[i],
        }
        # MA：数据不足的日期不加入字典
        for p in ma_periods:
            v = ma_vals[p][i]
            if v is not None:
                row[f"MA{p}"] = v
        # BOLL：数据不足时三条线均不加入
        if boll_mid[i] is not None:
            row["MID"]   = boll_mid[i]    # type: ignore[assignment]
            row["UPPER"] = boll_upper[i]  # type: ignore[assignment]
            row["LOWER"] = boll_lower[i]  # type: ignore[assignment]
        # RSI：数据不足时不加入
        if rsi6[i] is not None:
            row["RSI6"] = rsi6[i]   # type: ignore[assignment]
        if rsi12[i] is not None:
            row["RSI12"] = rsi12[i]  # type: ignore[assignment]
        if rsi24[i] is not None:
            row["RSI24"] = rsi24[i]  # type: ignore[assignment]
        # ATR
        if atr14[i] is not None:
            row["ATR14"] = atr14[i]  # type: ignore[assignment]
        if atr14_pct[i] is not None:
            row["ATR14_PCT"] = atr14_pct[i]  # type: ignore[assignment]
        # WR
        if wr10[i] is not None:
            row["WR10"] = wr10[i]  # type: ignore[assignment]
        if wr6[i] is not None:
            row["WR6"] = wr6[i]   # type: ignore[assignment]
        result[d] = row

    return result
