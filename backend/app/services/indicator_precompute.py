"""日线指标预计算落库（V1.0.9）：阶段一仅 adj_mode=qfq，供全市场回测优先读取。"""
from __future__ import annotations

import json

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import BarDaily, IndicatorPreDaily
from app.services.adj import apply_adj, build_adj_map, get_latest_factor
from app.services.indicator_compute import compute_indicators


def _qfq_adj_bars_for_symbol(db: Session, symbol_id: int):
    """将 bars_daily 转为前复权 OHLCV 序列，供 compute_indicators 使用。"""
    adj_map_f = build_adj_map(db, symbol_id)
    latest_factor = get_latest_factor(adj_map_f)

    def ap(price: float, td) -> float:
        return apply_adj(price, td, "qfq", adj_map_f, latest_factor)

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


def rebuild_indicator_pre_for_symbol(db: Session, symbol_id: int, adj_mode: str = "qfq") -> int:
    """删除该标的该复权口径的旧行，按全历史日线重算并写入。阶段一仅支持 qfq。返回写入行数。"""
    if adj_mode != "qfq":
        return 0
    db.execute(
        delete(IndicatorPreDaily).where(
            IndicatorPreDaily.symbol_id == symbol_id,
            IndicatorPreDaily.adj_mode == adj_mode,
        )
    )
    db.commit()
    adj_bars = _qfq_adj_bars_for_symbol(db, symbol_id)
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
    """若预计算覆盖足够则返回 {trade_date: {子指标: float}}；否则 None 回退现算。"""
    if adj_mode != "qfq":
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
