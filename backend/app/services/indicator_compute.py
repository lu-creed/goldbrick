"""
后端内置指标计算：将日线序列转换为每日指标值字典。

使用 pandas + numpy 向量化计算，替代原有纯 Python 循环实现。
不依赖 pandas-ta（Python 3.9 无兼容版本），所有指标均自行实现。

支持的指标（参数固定）：
  MA5/10/20/30/60     移动平均线
  EXPMA12/26          指数移动平均（EMA）
  BOLL: UPPER/MID/LOWER  布林带，N=20，总体标准差
  MACD: DIF/DEA/MACD柱  参数 12/26/9
  KDJ: K/D/J          N=9，中国惯例（K/D 初始值=50，权重 1/3）
  RSI6/12/24          Wilder 平滑
  ATR14/ATR14_PCT     真实波动幅度（Wilder 平滑）
  WR10/WR6            威廉指标（-100~0）
  CCI14               商品通道指数
  BIAS6/12/24         乖离率
  ROC6/12             变化率
  PSY12               心理线
  VMA5/10/20          量能均线
  OBV                 能量潮
  DMA/DDMA            差动移动平均
  TRIX12/TRMA         三重指数平滑
  PDI/MDI/ADX         趋向指标，N=14
  STDDEV10/20         价格总体标准差（ddof=0）
  AR/BR               人气指标，N=26
  VWAP                20日成交量加权均价（新增）
  MFI14               资金流量指数，N=14（新增）
  StochRSI_K/D        随机相对强弱指数（新增）

返回格式：{trade_date: {"MA5": v, ..., "close": v, ...}}
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd


def compute_indicators(
    bars: Sequence,
    start_date: date | None = None,
) -> dict[date, dict[str, float]]:
    """计算全部内置指标并返回按日期索引的字典。

    Args:
        bars:       按 trade_date 升序的 K 线序列（BarDaily ORM 对象）。
                    可含冷启动数据（比 start_date 更早），用于初始化需要历史窗口的指标。
        start_date: 只返回 >= start_date 的结果；None 则返回全部。

    Returns:
        {trade_date: {"MA5": v, ..., "close": v, ...}}
        历史不足时不输出该 key（如前 N-1 根没有 MA_N）。
    """
    if not bars:
        return {}

    n = len(bars)

    # ---- 构建 DataFrame ----
    df = pd.DataFrame({
        "close":         [float(b.close)  for b in bars],
        "open":          [float(b.open)   for b in bars],
        "high":          [float(b.high)   for b in bars],
        "low":           [float(b.low)    for b in bars],
        "volume":        [float(b.volume) for b in bars],
        "turnover_rate": [
            float(b.turnover_rate)
            if hasattr(b, "turnover_rate") and b.turnover_rate is not None
            else 0.0
            for b in bars
        ],
    }, index=[b.trade_date for b in bars])

    c = df["close"].values
    h = df["high"].values
    lo = df["low"].values
    op = df["open"].values
    v  = df["volume"].values

    # ================================================================
    # 1. MA
    # ================================================================
    for p in [5, 10, 20, 30, 60]:
        df[f"MA{p}"] = df["close"].rolling(p).mean()

    # ================================================================
    # 2. EXPMA — ewm(adjust=False) 与手写 alpha=2/(N+1) 完全等价
    # ================================================================
    df["EXPMA12"] = df["close"].ewm(span=12, adjust=False).mean()
    df["EXPMA26"] = df["close"].ewm(span=26, adjust=False).mean()

    # ================================================================
    # 3. BOLL — 总体标准差（ddof=0）
    # ================================================================
    boll_mid = df["close"].rolling(20).mean()
    boll_std = df["close"].rolling(20).std(ddof=0)
    df["MID"]   = boll_mid
    df["UPPER"] = boll_mid + 2 * boll_std
    df["LOWER"] = boll_mid - 2 * boll_std

    # ================================================================
    # 4. MACD — ewm 与手写 _ema 完全一致，MACD柱 = 2×(DIF-DEA)
    # ================================================================
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    dif   = ema12 - ema26
    dea   = dif.ewm(span=9, adjust=False).mean()
    df["DIF"]    = dif
    df["DEA"]    = dea
    df["MACD柱"] = 2 * (dif - dea)

    # ================================================================
    # 5. KDJ — 中国惯例（K/D 初始值=50，权重 1/3），必须循环
    # ================================================================
    kdj_n = 9
    lo9 = df["low"].rolling(kdj_n,  min_periods=1).min().values
    hi9 = df["high"].rolling(kdj_n, min_periods=1).max().values
    hl   = hi9 - lo9
    rsv  = np.where(hl != 0, (c - lo9) / hl * 100, 50.0)

    k_arr = np.empty(n); d_arr = np.empty(n)
    kp, dp = 50.0, 50.0
    for i, rsv_i in enumerate(rsv):
        k = kp * (2.0 / 3) + float(rsv_i) * (1.0 / 3)
        d = dp * (2.0 / 3) + k * (1.0 / 3)
        k_arr[i] = k; d_arr[i] = d
        kp, dp = k, d

    df["K"] = k_arr
    df["D"] = d_arr
    df["J"] = 3 * df["K"] - 2 * df["D"]

    # ================================================================
    # 6. RSI — Wilder 平滑（SMA 初始化，与旧版一致）
    # ================================================================
    for period in [6, 12, 24]:
        df[f"RSI{period}"] = _rsi_wilder_pd(df["close"], period)

    # ================================================================
    # 7. ATR — Wilder 平滑，首根用 SMA 初始化
    # ================================================================
    tr = _true_range(h, lo, c)
    df["ATR14"] = _wilder_smooth_pd(pd.Series(tr, index=df.index), 14)
    df["ATR14_PCT"] = (df["ATR14"] / df["close"] * 100).round(4)

    # ================================================================
    # 8. WR — (N日最高 - 收盘) / (N日最高 - N日最低) × (-100)
    # ================================================================
    for period, key in [(10, "WR10"), (6, "WR6")]:
        hi_n = df["high"].rolling(period).max()
        lo_n = df["low"].rolling(period).min()
        hl_n = hi_n - lo_n
        df[key] = np.where(hl_n != 0, (hi_n - df["close"]) / hl_n * (-100.0), -50.0)

    # ================================================================
    # 9. CCI — TP 的均值偏差法
    # ================================================================
    df["CCI14"] = _cci_pd(df["high"], df["low"], df["close"], 14)

    # ================================================================
    # 10. BIAS
    # ================================================================
    for p, key in [(6, "BIAS6"), (12, "BIAS12"), (24, "BIAS24")]:
        ma = df["close"].rolling(p).mean()
        df[key] = (df["close"] - ma) / ma * 100

    # ================================================================
    # 11. ROC
    # ================================================================
    df["ROC6"]  = df["close"].pct_change(6)  * 100
    df["ROC12"] = df["close"].pct_change(12) * 100

    # ================================================================
    # 12. PSY12
    # ================================================================
    df["PSY12"] = (df["close"].diff() > 0).astype(float).rolling(12).sum() / 12 * 100

    # ================================================================
    # 13. VMA
    # ================================================================
    for p in [5, 10, 20]:
        df[f"VMA{p}"] = df["volume"].rolling(p).mean()

    # ================================================================
    # 14. OBV — 首根加全量，后续按涨跌加减
    # ================================================================
    direction = np.sign(np.diff(c, prepend=c[0]))
    direction[0] = 1.0
    df["OBV"] = np.cumsum(direction * v)

    # ================================================================
    # 15. DMA / DDMA
    # ================================================================
    df["DMA"]  = df["MA10"] - df["MA20"]
    df["DDMA"] = df["DMA"].rolling(10).mean()

    # ================================================================
    # 16. TRIX / TRMA — 三重 EMA 链
    # ================================================================
    ema1t = df["close"].ewm(span=12, adjust=False).mean()
    ema2t = ema1t.ewm(span=12, adjust=False).mean()
    ema3t = ema2t.ewm(span=12, adjust=False).mean()
    df["TRIX12"] = ema3t.pct_change() * 100
    df["TRMA"]   = df["TRIX12"].rolling(9).mean()

    # ================================================================
    # 17. DMI（PDI/MDI/ADX）
    # ================================================================
    _calc_dmi(df, h, lo, c, 14)

    # ================================================================
    # 18. STDDEV — 总体标准差（ddof=0）
    # ================================================================
    df["STDDEV10"] = df["close"].rolling(10).std(ddof=0)
    df["STDDEV20"] = df["close"].rolling(20).std(ddof=0)

    # ================================================================
    # 19. AR / BR — 手写，pandas-ta 无内置
    # ================================================================
    arbr_n = 26
    df["AR"] = (df["high"] - df["open"]).rolling(arbr_n).sum() / \
               (df["open"]  - df["low"]).rolling(arbr_n).sum() * 100

    prev_c = df["close"].shift(1)
    hc = np.maximum(0.0, (df["high"] - prev_c).fillna(0)).rolling(arbr_n).sum()
    cl = np.maximum(0.0, (prev_c - df["low"]).fillna(0)).rolling(arbr_n).sum()
    df["BR"] = np.where(cl != 0, hc / cl * 100, np.where(hc > 0, 100.0, 0.0))
    df.loc[df.index[: arbr_n - 1], "BR"] = np.nan

    # ================================================================
    # 20. VWAP（20日成交量加权均价）— 新增
    # ================================================================
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol_sum = df["volume"].rolling(20).sum()
    df["VWAP"] = (typical * df["volume"]).rolling(20).sum() / vol_sum

    # ================================================================
    # 21. MFI14（资金流量指数）— 新增
    # ================================================================
    df["MFI14"] = _mfi_pd(df["high"], df["low"], df["close"], df["volume"], 14)

    # ================================================================
    # 22. StochRSI_K / StochRSI_D — 新增
    # ================================================================
    _calc_stochrsi(df, df["close"], rsi_len=14, stoch_len=14, k=3, d=3)

    # ================================================================
    # 组装结果字典
    # ================================================================
    result: dict[date, dict[str, float]] = {}
    for trade_date, row in df.iterrows():
        if start_date and trade_date < start_date:
            continue
        row_dict: dict[str, float] = {}
        for key, val in row.items():
            if pd.notna(val):
                row_dict[key] = float(val)
        result[trade_date] = row_dict

    return result


# ================================================================
# 辅助函数
# ================================================================

def _rsi_wilder_pd(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI：SMA 初始化，后续用 Wilder 指数平滑（与旧版手写一致）。"""
    n = len(close)
    out = np.full(n, np.nan)
    if n < period + 1:
        return pd.Series(out, index=close.index)

    diff  = close.diff().values
    gains  = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)

    avg_g = gains[1 : period + 1].mean()
    avg_l = losses[1 : period + 1].mean()
    out[period] = 100.0 - 100.0 / (1.0 + avg_g / avg_l) if avg_l != 0 else 100.0

    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        out[i] = 100.0 - 100.0 / (1.0 + avg_g / avg_l) if avg_l != 0 else 100.0

    return pd.Series(out, index=close.index)


def _true_range(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    hl  = highs - lows
    hpc = np.abs(highs[1:] - closes[:-1])
    lpc = np.abs(lows[1:]  - closes[:-1])
    tr  = np.concatenate([[hl[0]], np.maximum(hl[1:], np.maximum(hpc, lpc))])
    return tr


def _wilder_smooth_pd(series: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑（首 period 根用 SMA 初始化）。"""
    n = len(series)
    out = np.full(n, np.nan)
    if n < period:
        return pd.Series(out, index=series.index)
    vals = series.values
    out[period - 1] = vals[:period].mean()
    for i in range(period, n):
        out[i] = (out[i - 1] * (period - 1) + vals[i]) / period
    return pd.Series(out, index=series.index)


def _cci_pd(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tp  = (high + low + close) / 3.0
    tp_mean = tp.rolling(period).mean()
    tp_mad  = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - tp_mean) / (0.015 * tp_mad)


def _calc_dmi(df: pd.DataFrame, h: np.ndarray, lo: np.ndarray, c: np.ndarray, period: int) -> None:
    """计算 ADX / PDI / MDI 并原地写入 df。"""
    n = len(h)
    up   = h[1:] - h[:-1]
    dn   = lo[:-1] - lo[1:]
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    mdm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr_  = _true_range(h, lo, c)[1:]

    # Wilder 平滑（首 period 根用 SMA 初始化）
    def _ws(arr: np.ndarray) -> np.ndarray:
        m  = len(arr)
        out = np.full(m, np.nan)
        if m < period:
            return out
        out[period - 1] = arr[:period].sum()
        for i in range(period, m):
            out[i] = out[i - 1] * (period - 1) / period + arr[i]
        return out

    spdm = _ws(pdm); smdm = _ws(mdm); str_ = _ws(tr_)

    pdi_a = np.where(str_ != 0, 100.0 * spdm / str_, np.nan)
    mdi_a = np.where(str_ != 0, 100.0 * smdm / str_, np.nan)
    pmdm  = pdi_a + mdi_a
    dx_a  = np.where(pmdm != 0, 100.0 * np.abs(pdi_a - mdi_a) / pmdm, np.nan)

    # ADX = Wilder 平滑的 DX，在 DX 开始有效后再初始化
    adx_a = np.full(n - 1, np.nan)
    first = next((i for i, v in enumerate(dx_a) if not np.isnan(v)), None)
    if first is not None:
        buf, init_end = [], -1
        for i in range(first, n - 1):
            if not np.isnan(dx_a[i]):
                buf.append(dx_a[i])
                if len(buf) == period:
                    init_end = i; break
        if init_end >= 0:
            adx_a[init_end] = np.mean(buf)
            for i in range(init_end + 1, n - 1):
                if not np.isnan(dx_a[i]) and not np.isnan(adx_a[i - 1]):
                    adx_a[i] = (adx_a[i - 1] * (period - 1) + dx_a[i]) / period

    # 对齐到 df 长度（第 0 根无 prev，数组长度是 n-1）
    df["PDI"] = pd.Series(np.concatenate([[np.nan], pdi_a]), index=df.index)
    df["MDI"] = pd.Series(np.concatenate([[np.nan], mdi_a]), index=df.index)
    df["ADX"] = pd.Series(np.concatenate([[np.nan], adx_a]), index=df.index)


def _mfi_pd(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    period: int,
) -> pd.Series:
    """资金流量指数（MFI）。"""
    tp  = (high + low + close) / 3.0
    mf  = tp * volume
    pos = np.where(tp.diff() > 0, mf, 0.0)
    neg = np.where(tp.diff() < 0, mf, 0.0)
    pos_sum = pd.Series(pos, index=close.index).rolling(period).sum()
    neg_sum = pd.Series(neg, index=close.index).rolling(period).sum()
    mfr = np.where(neg_sum != 0, pos_sum / neg_sum, 100.0)
    return pd.Series(100.0 - 100.0 / (1.0 + mfr), index=close.index)


def _calc_stochrsi(
    df: pd.DataFrame,
    close: pd.Series,
    rsi_len: int = 14,
    stoch_len: int = 14,
    k: int = 3,
    d: int = 3,
) -> None:
    """StochRSI_K / StochRSI_D，原地写入 df。"""
    rsi = _rsi_wilder_pd(close, rsi_len)
    lo_rsi = rsi.rolling(stoch_len).min()
    hi_rsi = rsi.rolling(stoch_len).max()
    hl     = hi_rsi - lo_rsi
    stoch  = np.where(hl != 0, (rsi - lo_rsi) / hl * 100, 50.0)
    stoch_s = pd.Series(stoch, index=close.index)
    df["StochRSI_K"] = stoch_s.rolling(k).mean()
    df["StochRSI_D"] = df["StochRSI_K"].rolling(d).mean()
