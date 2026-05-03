"""日线指标预计算落库：列式存储版本（P1a 起）。

历史背景：
- V1.0.9：只落 qfq，payload TEXT JSON。
- 0.0.4-dev：开放 hfq 预计算。
- P1a（当前）：把 payload JSON 拆成独立 REAL 列，省约 5-7 GB 磁盘，
  并且读/写路径都更快（无需 json.dumps / json.loads）。

外部接口保持不变：
- rebuild_indicator_pre_for_symbol(db, symbol_id, adj_mode) → 写入行数
- load_indicator_map_from_pre(...) → {trade_date: {指标名: float}}，dict 键仍是
  compute_indicators 原始的 "MA5" / "K" / "MACD柱" 等展示名（不是 SQL 列名）
"""
from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import BarDaily, IndicatorPreDaily
from app.services.adj import AdjType, apply_adj, build_adj_map, get_latest_factor
from app.services.indicator_compute import compute_indicators


_SUPPORTED_ADJ: tuple[str, ...] = ("qfq", "hfq")


# 将 compute_indicators 返回 dict 的原始 key → IndicatorPreDaily SQL 列名的映射。
# 唯一来源（single source of truth），写入与读取两头共用，保证列式编解码对称。
# 加新指标时：1) 加列到 models.IndicatorPreDaily  2) 加映射到这里  3) alembic add_column  4) 重填缓存。
INDICATOR_COLUMN_MAP: dict[str, str] = {
    # OHLCV 透传（compute_indicators 也会在 row dict 里返回）
    "close":         "close",
    "open":          "open",
    "high":          "high",
    "low":           "low",
    "volume":        "volume",
    "turnover_rate": "turnover_rate",
    # MA 移动平均
    "MA5":  "ma5",
    "MA10": "ma10",
    "MA20": "ma20",
    "MA30": "ma30",
    "MA60": "ma60",
    # EXPMA 指数移动平均
    "EXPMA12": "expma12",
    "EXPMA26": "expma26",
    # MACD
    "DIF":    "dif",
    "DEA":    "dea",
    "MACD柱": "macd_bar",   # 中文 key 不能当 SQL 列名，改 snake_case 英文
    # KDJ（单字母 key 加前缀，避免与 DMA 的 "D" 等语义混淆）
    "K": "kdj_k",
    "D": "kdj_d",
    "J": "kdj_j",
    # BOLL 布林带
    "MID":   "boll_mid",
    "UPPER": "boll_upper",
    "LOWER": "boll_lower",
    # RSI
    "RSI6":  "rsi6",
    "RSI12": "rsi12",
    "RSI24": "rsi24",
    # ATR
    "ATR14":     "atr14",
    "ATR14_PCT": "atr14_pct",
    # WR
    "WR6":  "wr6",
    "WR10": "wr10",
    # CCI
    "CCI14": "cci14",
    # BIAS
    "BIAS6":  "bias6",
    "BIAS12": "bias12",
    "BIAS24": "bias24",
    # ROC
    "ROC6":  "roc6",
    "ROC12": "roc12",
    # PSY
    "PSY12": "psy12",
    # VMA
    "VMA5":  "vma5",
    "VMA10": "vma10",
    "VMA20": "vma20",
    # OBV
    "OBV": "obv",
    # DMA
    "DMA":  "dma",
    "DDMA": "ddma",
    # TRIX
    "TRIX12": "trix12",
    "TRMA":   "trma",
    # DMI
    "PDI": "pdi",
    "MDI": "mdi",
    "ADX": "adx",
    # STDDEV
    "STDDEV10": "stddev10",
    "STDDEV20": "stddev20",
    # ARBR
    "AR": "ar",
    "BR": "br",
}

# 反向映射（SQL 列名 → 展示 key），load 时用
_COLUMN_INDICATOR_MAP: dict[str, str] = {v: k for k, v in INDICATOR_COLUMN_MAP.items()}
# 所有参与列式存储的 SQL 列名元组，遍历时用
_COLUMN_NAMES: tuple[str, ...] = tuple(_COLUMN_INDICATOR_MAP.keys())


def _adj_bars_for_symbol(db: Session, symbol_id: int, adj: AdjType):
    """将 bars_daily 转为指定复权模式的 OHLCV 序列，供 compute_indicators 使用。

    adj:
      - "qfq"：前复权（与 K 线副图同口径）
      - "hfq"：后复权（长期收益率对比；价格等比放大，不适合看绝对价位）
      - "none"：原样返回 bars（虽然本模块只在 qfq/hfq 下调用）
    """
    adj_map_f = build_adj_map(db, symbol_id)
    latest_factor = get_latest_factor(adj_map_f)

    def ap(price: float, td) -> float:
        return apply_adj(price, td, adj, adj_map_f, latest_factor)

    bars = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == symbol_id)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        return []

    class _AdjBar:
        __slots__ = ("trade_date", "open", "high", "low", "close", "volume", "turnover_rate")

        def __init__(self, b):
            self.trade_date = b.trade_date
            self.open = ap(float(b.open), b.trade_date)
            self.high = ap(float(b.high), b.trade_date)
            self.low = ap(float(b.low), b.trade_date)
            self.close = ap(float(b.close), b.trade_date)
            self.volume = float(b.volume)
            self.turnover_rate = float(b.turnover_rate) if b.turnover_rate is not None else None

    return [_AdjBar(b) for b in bars]


# 保留旧名作向后兼容（某些测试/脚本可能直接 import）
def _qfq_adj_bars_for_symbol(db: Session, symbol_id: int):
    """向后兼容别名：等价 `_adj_bars_for_symbol(db, symbol_id, 'qfq')`。"""
    return _adj_bars_for_symbol(db, symbol_id, "qfq")


def rebuild_indicator_pre_for_symbol(db: Session, symbol_id: int, adj_mode: str = "qfq") -> int:
    """删除该标的该复权口径的旧行，按全历史日线重算并写入。支持 qfq/hfq。返回写入行数。

    P1a：不再走 JSON，直接把 compute_indicators 返回 dict 按 INDICATOR_COLUMN_MAP
    映射成 ORM 构造器 kwargs。未命中映射的 key（例如未来 compute_indicators 新加
    一个指标而 models 还没加列）会被静默忽略 —— 对缓存写入路径这是"降级兼容"，
    新指标读不到缓存会 fallback 现算，不会报错。
    """
    if adj_mode not in _SUPPORTED_ADJ:
        return 0
    db.execute(
        delete(IndicatorPreDaily).where(
            IndicatorPreDaily.symbol_id == symbol_id,
            IndicatorPreDaily.adj_mode == adj_mode,
        )
    )
    db.commit()
    adj_bars = _adj_bars_for_symbol(db, symbol_id, adj_mode)  # type: ignore[arg-type]
    if not adj_bars:
        return 0
    ind_map = compute_indicators(adj_bars, start_date=None)
    n = 0
    for td, row in ind_map.items():
        # 按 INDICATOR_COLUMN_MAP 投射到 SQL 列；过滤 None/非数值
        col_values = {
            INDICATOR_COLUMN_MAP[k]: float(v)
            for k, v in row.items()
            if k in INDICATOR_COLUMN_MAP and isinstance(v, (int, float))
        }
        db.add(
            IndicatorPreDaily(
                symbol_id=symbol_id,
                trade_date=td,
                adj_mode=adj_mode,
                **col_values,
            )
        )
        n += 1
    db.commit()
    return n


def load_indicator_map_from_pre(
    db: Session,
    symbol_id: int,
    adj_mode: str,
    start,
    end,
):
    """若预计算覆盖足够则返回 {trade_date: {展示指标名: float}}；否则 None 回退现算。

    返回的 dict key 是 compute_indicators 的原始展示名（"MA5"/"K"/"MACD柱" 等），
    不是 SQL 列名 —— 调用方对"原来 JSON 里是什么键"的约定不感知列式化改动。

    adj_mode 支持 qfq 与 hfq；其他值（如 none）直接返回 None。
    """
    if adj_mode not in _SUPPORTED_ADJ:
        return None
    rows = (
        db.query(IndicatorPreDaily)
        .filter(
            IndicatorPreDaily.symbol_id == symbol_id,
            IndicatorPreDaily.adj_mode == adj_mode,
            IndicatorPreDaily.trade_date >= start,
            IndicatorPreDaily.trade_date <= end,
        )
        .all()
    )
    if not rows:
        return None
    bar_cnt = (
        db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == symbol_id,
            BarDaily.trade_date >= start,
            BarDaily.trade_date <= end,
        )
        .count()
    )
    if bar_cnt > 0 and len(rows) < bar_cnt * 0.95:
        return None
    out: dict = {}
    for r in rows:
        row_dict: dict[str, float] = {}
        for col in _COLUMN_NAMES:
            v = getattr(r, col, None)
            if v is not None:
                row_dict[_COLUMN_INDICATOR_MAP[col]] = float(v)
        if row_dict:
            out[r.trade_date] = row_dict
    return out if out else None
