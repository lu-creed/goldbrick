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
  CCI14            商品通道指数，N=14
  BIAS6/12/24      乖离率，N=6/12/24
  ROC6/12          变化率，N=6/12
  PSY12            心理线，N=12
  VMA5/10/20       量能均线，N=5/10/20
  OBV              能量潮
  DMA/DDMA         差动移动平均（MA10-MA20）及10日均线
  TRIX12/TRMA      三重指数平滑及9日信号线
  PDI/MDI/ADX      趋向指标，N=14
  STDDEV10/20      价格总体标准差
  AR/BR            人气指标，N=26

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

    # ---- CCI（商品通道指数）N=14 ----
    # TP = (High+Low+Close)/3；CCI = (TP - SMA(TP,N)) / (0.015 × 平均绝对偏差)
    cci_n = 14
    tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    cci14: List[Optional[float]] = []
    for i in range(n):
        if i < cci_n - 1:
            cci14.append(None)
        else:
            sl = tp[i - cci_n + 1 : i + 1]
            avg = sum(sl) / cci_n
            md = sum(abs(x - avg) for x in sl) / cci_n
            cci14.append((tp[i] - avg) / (0.015 * md) if md != 0 else 0.0)

    # ---- BIAS（乖离率）N=6/12/24 ----
    # BIAS_N = (close - MA_N) / MA_N × 100
    def _bias(period: int) -> List[Optional[float]]:
        out_: List[Optional[float]] = []
        for i in range(n):
            if i < period - 1:
                out_.append(None)
            else:
                avg = sum(closes[i - period + 1 : i + 1]) / period
                out_.append((closes[i] - avg) / avg * 100.0 if avg != 0 else None)
        return out_

    bias6  = _bias(6)
    bias12 = _bias(12)
    bias24 = _bias(24)

    # ---- ROC（变化率）N=6/12 ----
    # ROC_N = (close - close[i-N]) / close[i-N] × 100
    def _roc(period: int) -> List[Optional[float]]:
        out_: List[Optional[float]] = []
        for i in range(n):
            if i < period:
                out_.append(None)
            else:
                prev = closes[i - period]
                out_.append((closes[i] - prev) / prev * 100.0 if prev != 0 else None)
        return out_

    roc6  = _roc(6)
    roc12 = _roc(12)

    # ---- PSY（心理线）N=12 ----
    # PSY = 近N日中收盘价上涨天数 / N × 100
    psy_n = 12
    psy12: List[Optional[float]] = []
    for i in range(n):
        if i < psy_n:
            psy12.append(None)
        else:
            up = sum(1 for j in range(i - psy_n + 1, i + 1) if closes[j] > closes[j - 1])
            psy12.append(up / psy_n * 100.0)

    # ---- VOLS（量能均线）N=5/10/20 ----
    def _vma(period: int) -> List[Optional[float]]:
        out_: List[Optional[float]] = []
        for i in range(n):
            if i < period - 1:
                out_.append(None)
            else:
                out_.append(sum(vols[i - period + 1 : i + 1]) / period)
        return out_

    vma5  = _vma(5)
    vma10 = _vma(10)
    vma20 = _vma(20)

    # ---- OBV（能量潮）----
    # OBV(0) = vol[0]；之后按涨跌加减成交量
    obv: List[float] = [vols[0]]
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - vols[i])
        else:
            obv.append(obv[-1])

    # ---- DMA（差动移动平均）----
    # DMA = MA10 - MA20；DDMA = DMA 的 10 日简单均线
    dma_v: List[Optional[float]] = []
    for i in range(n):
        m10 = ma_vals[10][i]
        m20 = ma_vals[20][i]
        dma_v.append(m10 - m20 if m10 is not None and m20 is not None else None)

    dma_ma_n = 10
    ddma: List[Optional[float]] = []
    for i in range(n):
        win = [dma_v[j] for j in range(max(0, i - dma_ma_n + 1), i + 1) if dma_v[j] is not None]
        ddma.append(sum(win) / len(win) if len(win) == dma_ma_n else None)

    # ---- TRIX（三重指数平滑）N=12，信号线 N=9 ----
    # EMA3 = EMA(EMA(EMA(close, N), N), N)
    # TRIX = (EMA3[i] - EMA3[i-1]) / EMA3[i-1] × 100；TRMA = MA(TRIX, 9)
    trix_n = 12
    _ema1t = _ema(closes, trix_n)
    _ema2t = _ema(_ema1t, trix_n)
    _ema3t = _ema(_ema2t, trix_n)
    trix12: List[Optional[float]] = [None]
    for i in range(1, n):
        pe3 = _ema3t[i - 1]
        trix12.append((_ema3t[i] - pe3) / pe3 * 100.0 if pe3 != 0 else None)

    trma_n = 9
    trma: List[Optional[float]] = []
    for i in range(n):
        win = [trix12[j] for j in range(max(0, i - trma_n + 1), i + 1) if trix12[j] is not None]
        trma.append(sum(win) / len(win) if len(win) == trma_n else None)

    # ---- DMI（趋向指标）N=14 ----
    # 计算 +DM/-DM/TR → Wilder 平滑 → +DI/-DI/DX → ADX
    dmi_n = 14
    pdm_arr: List[float] = [0.0]
    mdm_arr: List[float] = [0.0]
    tr_dmi:  List[float] = [float(highs[0] - lows[0])]
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        dn_move = lows[i - 1] - lows[i]
        pdm_arr.append(max(up_move, 0.0) if up_move > dn_move and up_move > 0 else 0.0)
        mdm_arr.append(max(dn_move, 0.0) if dn_move > up_move and dn_move > 0 else 0.0)
        tr_dmi.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def _wilder(src: List[float], period: int) -> List[Optional[float]]:
        out_: List[Optional[float]] = [None] * n
        if n < period:
            return out_
        out_[period - 1] = float(sum(src[:period]))
        for i in range(period, n):
            out_[i] = out_[i - 1] * (period - 1) / period + src[i]  # type: ignore[operator]
        return out_

    spdm = _wilder(pdm_arr, dmi_n)
    smdm = _wilder(mdm_arr, dmi_n)
    str_w = _wilder(tr_dmi, dmi_n)

    pdi: List[Optional[float]] = []
    mdi: List[Optional[float]] = []
    dx:  List[Optional[float]] = []
    for i in range(n):
        sp = spdm[i]; sm = smdm[i]; st = str_w[i]
        if sp is None or sm is None or st is None or st == 0:
            pdi.append(None); mdi.append(None); dx.append(None)
        else:
            p = 100.0 * sp / st
            m = 100.0 * sm / st
            pdi.append(p); mdi.append(m)
            s = p + m
            dx.append(100.0 * abs(p - m) / s if s != 0 else 0.0)

    adx: List[Optional[float]] = [None] * n
    first_dx = next((i for i, v in enumerate(dx) if v is not None), None)
    if first_dx is not None:
        buf: List[float] = []
        init_end = -1
        for i in range(first_dx, n):
            if dx[i] is not None:
                buf.append(dx[i])  # type: ignore[arg-type]
                if len(buf) == dmi_n:
                    init_end = i
                    break
        if init_end >= 0:
            adx[init_end] = sum(buf) / dmi_n
            for i in range(init_end + 1, n):
                dv = dx[i]; pv = adx[i - 1]
                if dv is not None and pv is not None:
                    adx[i] = (pv * (dmi_n - 1) + dv) / dmi_n

    # ---- STDDEV（价格总体标准差）N=10/20 ----
    def _stddev(period: int) -> List[Optional[float]]:
        out_: List[Optional[float]] = []
        for i in range(n):
            if i < period - 1:
                out_.append(None)
            else:
                sl = closes[i - period + 1 : i + 1]
                avg = sum(sl) / period
                out_.append((sum((x - avg) ** 2 for x in sl) / period) ** 0.5)
        return out_

    stddev10 = _stddev(10)
    stddev20 = _stddev(20)

    # ---- ARBR（人气指标）N=26 ----
    # AR = sum(high-open, N) / sum(open-low, N) × 100
    # BR = sum(max(0,high-prev_close), N) / sum(max(0,prev_close-low), N) × 100
    arbr_n = 26
    ar_vals: List[Optional[float]] = []
    br_vals: List[Optional[float]] = []
    for i in range(n):
        if i < arbr_n - 1:
            ar_vals.append(None); br_vals.append(None)
        else:
            ho = sum(highs[j] - opens[j] for j in range(i - arbr_n + 1, i + 1))
            ol = sum(opens[j] - lows[j]  for j in range(i - arbr_n + 1, i + 1))
            ar_vals.append(ho / ol * 100.0 if ol != 0 else None)
            hc = sum(max(0.0, highs[j] - closes[j - 1]) for j in range(i - arbr_n + 1, i + 1) if j > 0)
            cl = sum(max(0.0, closes[j - 1] - lows[j])  for j in range(i - arbr_n + 1, i + 1) if j > 0)
            br_vals.append(hc / cl * 100.0 if cl != 0 else (100.0 if hc > 0 else 0.0))

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
        # CCI
        if cci14[i] is not None:
            row["CCI14"] = cci14[i]  # type: ignore[assignment]
        # BIAS
        if bias6[i] is not None:
            row["BIAS6"] = bias6[i]  # type: ignore[assignment]
        if bias12[i] is not None:
            row["BIAS12"] = bias12[i]  # type: ignore[assignment]
        if bias24[i] is not None:
            row["BIAS24"] = bias24[i]  # type: ignore[assignment]
        # ROC
        if roc6[i] is not None:
            row["ROC6"] = roc6[i]  # type: ignore[assignment]
        if roc12[i] is not None:
            row["ROC12"] = roc12[i]  # type: ignore[assignment]
        # PSY
        if psy12[i] is not None:
            row["PSY12"] = psy12[i]  # type: ignore[assignment]
        # VOLS
        if vma5[i] is not None:
            row["VMA5"] = vma5[i]  # type: ignore[assignment]
        if vma10[i] is not None:
            row["VMA10"] = vma10[i]  # type: ignore[assignment]
        if vma20[i] is not None:
            row["VMA20"] = vma20[i]  # type: ignore[assignment]
        # OBV
        row["OBV"] = obv[i]
        # DMA
        if dma_v[i] is not None:
            row["DMA"] = dma_v[i]  # type: ignore[assignment]
        if ddma[i] is not None:
            row["DDMA"] = ddma[i]  # type: ignore[assignment]
        # TRIX
        if trix12[i] is not None:
            row["TRIX12"] = trix12[i]  # type: ignore[assignment]
        if trma[i] is not None:
            row["TRMA"] = trma[i]  # type: ignore[assignment]
        # DMI
        if pdi[i] is not None:
            row["PDI"] = pdi[i]  # type: ignore[assignment]
        if mdi[i] is not None:
            row["MDI"] = mdi[i]  # type: ignore[assignment]
        if adx[i] is not None:
            row["ADX"] = adx[i]  # type: ignore[assignment]
        # STDDEV
        if stddev10[i] is not None:
            row["STDDEV10"] = stddev10[i]  # type: ignore[assignment]
        if stddev20[i] is not None:
            row["STDDEV20"] = stddev20[i]  # type: ignore[assignment]
        # ARBR
        if ar_vals[i] is not None:
            row["AR"] = ar_vals[i]  # type: ignore[assignment]
        if br_vals[i] is not None:
            row["BR"] = br_vals[i]  # type: ignore[assignment]
        result[d] = row

    return result
