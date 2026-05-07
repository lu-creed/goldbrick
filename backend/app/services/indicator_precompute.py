"""日线指标预计算落库：支持 qfq（前复权）与 hfq（后复权）双口径，供全市场回测与图表优先读取。

历史背景：
- V1.0.9 阶段一仅落 qfq，hfq 路径靠 user_indicator_compute.load_adjusted_bar_sequence 内存现算。
- 0.0.4-dev：开放 hfq 预计算，长期收益率对比口径也能复用缓存，减少图表首屏延迟。
- 0.0.4-dev（救火）：新增「只预算近 N 天」模式（settings.indicator_pre_recent_days）。
  老数据由 load_indicator_map_from_pre 返回 None 时自动回退到内存现算，避免让这张表
  占用几 GB 磁盘（原来每只股票全量历史 × 多个子指标 × JSON 文本存储）。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.config import settings
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


def _recent_cutoff() -> Optional[date]:
    """读取配置里的 indicator_pre_recent_days。返回 None 表示保留全部；返回 date 表示只写此日期之后。

    0 表示「完全不预算」，调用方看到 0 应直接跳过落库。
    """
    n = int(getattr(settings, "indicator_pre_recent_days", 0) or 0)
    if n <= 0:
        return None
    return date.today() - timedelta(days=n)


def rebuild_indicator_pre_for_symbol(db: Session, symbol_id: int, adj_mode: str = "qfq") -> int:
    """删除该标的该复权口径的旧行，按全历史日线重算并只写入「近 N 天」结果。支持 qfq/hfq。

    - settings.indicator_pre_recent_days == 0 → 完全不落盘（返回 0）
    - settings.indicator_pre_recent_days  > 0 → 只写入 today - N 之后的交易日
    - settings.indicator_pre_recent_days 为负数/缺失 → 保留旧行为（全量写入）

    返回实际写入行数。
    """
    if adj_mode not in _SUPPORTED_ADJ:
        return 0
    # 受控模式：0 = 彻底不预算（老数据全部清，让 load_indicator_map_from_pre 走回退路径）
    recent_days_cfg = int(getattr(settings, "indicator_pre_recent_days", -1))
    if recent_days_cfg == 0:
        db.execute(
            delete(IndicatorPreDaily).where(
                IndicatorPreDaily.symbol_id == symbol_id,
                IndicatorPreDaily.adj_mode == adj_mode,
            )
        )
        db.commit()
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
    cutoff = _recent_cutoff()  # None 表示写全量；date 表示只写 >= cutoff 的
    n = 0
    for td, row in ind_map.items():
        if cutoff is not None and td < cutoff:
            continue
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
