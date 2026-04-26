"""复权因子工具：从数据库加载复权因子，对单个价格做前/后复权换算（内存计算，不修改库中原始数据）。

复权的意义：
  A 股分红、配股、送股会导致股价发生跳空，如果直接看原始（未复权）价格，K 线会出现断崖式下跌，
  并不反映真实的价格走势。复权就是把历史价格统一调整，让走势连续、可比。

复权类型：
  - none（不复权）：显示交易所挂牌的原始价格
  - qfq（前复权）：以今天价格为基准，调低历史价格，使形态连续；
                   公式：调整后价格 = 原始价格 × 当日因子 / 最新因子
                   适合看历史形态和技术分析
  - hfq（后复权）：以最早价格为基准，调高历史价格；
                   公式：调整后价格 = 原始价格 × 当日因子
                   适合长期收益率对比，但股价数字可能很大（远超现价）
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from sqlalchemy.orm import Session

from app.models import AdjFactorDaily

# AdjType 是类型别名，限制复权参数只能是这三个字面量字符串
AdjType = Literal["none", "qfq", "hfq"]


def build_adj_map(db: Session, symbol_id: int) -> dict[date, float]:
    """从数据库加载该标的的全部复权因子，返回 {交易日: 复权因子} 字典。

    复权因子由 Tushare 计算并存入 adj_factors_daily 表。
    因子值通常在 1.0 附近，每次分红/送股时会阶梯式跳变。
    """
    rows = db.query(AdjFactorDaily).filter(AdjFactorDaily.symbol_id == symbol_id).all()
    return {r.trade_date: float(r.adj_factor) for r in rows}


def apply_adj(
    price: float,
    trade_date: date,
    adj: AdjType,
    adj_map: dict[date, float],
    latest_factor: float,
) -> float:
    """对单个价格值做前/后复权换算，保留 4 位小数。

    Args:
        price: 原始价格（未复权，如数据库中存储的 close）。
        trade_date: 该价格对应的交易日（用于查找当日复权因子）。
        adj: 复权类型：none/qfq/hfq。
        adj_map: {交易日: 复权因子}，由 build_adj_map 获得。
        latest_factor: 最新日期的复权因子，前复权用于归一化（确保最新价不变）。

    Returns:
        复权后的价格（4 位小数）。none 时原样返回。
    """
    if adj == "none" or not adj_map:
        return price  # 不复权，直接返回原始价格

    # 若当日没有因子记录（停牌、数据缺失等），默认用 1.0（不调整）
    af = adj_map.get(trade_date, 1.0)

    if adj == "hfq":
        # 后复权：价格 × 当日因子。历史价格被等比放大，适合看长期收益
        return round(price * af, 4)

    # qfq（前复权）：价格 × 当日因子 / 最新因子
    # 除以 latest_factor 确保最新日价格不变（其他日期被等比缩小）
    return round(price * af / latest_factor, 4)


def get_latest_factor(adj_map: dict[date, float]) -> float:
    """取日期最新的复权因子，用作前复权的基准分母。

    Returns:
        最新日期对应的因子值；若因子表为空（如指数无复权），返回 1.0（不调整）。
    """
    if not adj_map:
        return 1.0
    return adj_map[max(adj_map.keys())]
