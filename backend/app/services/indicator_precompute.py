"""日线指标预计算落库：支持 qfq（前复权）与 hfq（后复权）双口径，供全市场回测与图表优先读取。

历史背景：
- V1.0.9 阶段一仅落 qfq，hfq 路径靠 user_indicator_compute.load_adjusted_bar_sequence 内存现算。
- 0.0.4-dev：开放 hfq 预计算，长期收益率对比口径也能复用缓存，减少图表首屏延迟。
"""
from __future__ import annotations

import json

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import BarDaily, IndicatorPreDaily
from app.services.adj import AdjType, apply_adj, build_adj_map, get_latest_factor
from app.services.indicator_compute import compute_indicators


_SUPPORTED_ADJ: tuple[str, ...] = ("qfq", "hfq")


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
    """删除该标的该复权口径的旧行，按全历史日线重算并写入。支持 qfq/hfq。返回写入行数。"""
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
        payload = json.dumps({k: float(v) for k, v in row.items() if isinstance(v, (int, float))})
        db.add(
            IndicatorPreDaily(
                symbol_id=symbol_id,
                trade_date=td,
                adj_mode=adj_mode,
                payload=payload,
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
    """若预计算覆盖足够则返回 {trade_date: {子指标: float}}；否则 None 回退现算。

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
    out = {}
    for r in rows:
        try:
            raw = json.loads(r.payload or "{}")
            out[r.trade_date] = {k: float(v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return out if out else None
