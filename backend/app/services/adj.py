"""复权因子工具：加载与价格换算（内存计算，不落库）。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.models import AdjFactorDaily

AdjType = Literal["none", "qfq", "hfq"]


def build_adj_map(db: Session, symbol_id: int) -> dict[date, float]:
    """从 adj_factors_daily 表加载该标的全部复权因子。"""
    rows = db.query(AdjFactorDaily).filter(AdjFactorDaily.symbol_id == symbol_id).all()
    return {r.trade_date: float(r.adj_factor) for r in rows}


def apply_adj(
    price: float,
    trade_date: date,
    adj: AdjType,
    adj_map: dict[date, float],
    latest_factor: float,
) -> float:
    """对单个价格做前/后复权换算，保留4位小数。

    - hfq（后复权）：price × adj_factor
      历史价格等比放大，保持区间收益率不变，适合长期对比走势。
    - qfq（前复权）：price × adj_factor / latest_factor
      以最新价格为基准调整历史价格，适合看当前价位的历史形态。
    - none：原样返回。
    """
    if adj == "none" or not adj_map:
        return price
    af = adj_map.get(trade_date, 1.0)
    if adj == "hfq":
        return round(price * af, 4)
    # qfq
    return round(price * af / latest_factor, 4)


def get_latest_factor(adj_map: dict[date, float]) -> float:
    """取日期最新的复权因子（前复权基准）。"""
    if not adj_map:
        return 1.0
    return adj_map[max(adj_map.keys())]
