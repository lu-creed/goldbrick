"""
由日 K 聚合多周期 K 线（查询时动态计算，不落库）。

聚合逻辑：
  日线 → 按交易日的「自然周/月/季/年」分组 → 取首开、末收、区间最高/最低、区间总量/额
  时间戳：取区间内「最后一个」交易日作为该根 K 线的时间（与大多数行情软件一致）
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from app.schemas import BarPoint, Interval


@dataclass
class DailyRow:
    """单根日线的原始数据（用于 aggregate_bars 入参，与 BarDaily ORM 解耦）。"""
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    turnover_rate: Optional[float]
    consecutive_limit_up_days: int
    consecutive_limit_down_days: int
    consecutive_up_days: int
    consecutive_down_days: int


def _period_key(d: date, interval: Interval) -> tuple[str, str]:
    """将交易日映射为所属周期的唯一键（用于 defaultdict 分组）。

    1d：日线，每天一根，键 = ISO 日期字符串
    1w：周线，以自然周为单位（ISO 周历：周一开始），键 = 年-W周数
    1M：月线，以自然月为单位，键 = 年-月（如 "2024-01"）
    1Q：季线，以自然季度为单位，键 = 年-Q季（如 "2024-Q1"）
    1y：年线，以自然年为单位，键 = 年份字符串
    """
    if interval == "1d":
        return ("1d", d.isoformat())
    if interval == "1w":
        iso = d.isocalendar()
        return ("1w", f"{iso.year}-W{iso.week:02d}")
    if interval == "1M":
        return ("1M", f"{d.year}-{d.month:02d}")
    if interval == "1Q":
        q = (d.month - 1) // 3 + 1
        return ("1Q", f"{d.year}-Q{q}")
    if interval == "1y":
        return ("1y", str(d.year))
    raise ValueError(f"unsupported interval: {interval}")


def aggregate_bars(rows: Iterable[DailyRow], interval: Interval) -> list[BarPoint]:
    """将日线序列聚合成指定周期的 K 线列表。

    Args:
        rows: 日线序列（任意顺序，内部会重新排序）。
        interval: 目标周期，见 Interval 类型定义。

    Returns:
        按时间升序排列的 BarPoint 列表：
        - open：区间第一个交易日的开盘价
        - close：区间最后一个交易日的收盘价
        - high/low：区间内的极值
        - volume/amount：区间累计成交量/额
        - turnover_rate_avg：区间内日均换手率（无换手率数据则为 None）
        - consecutive_*：取区间最后一个交易日的值（延续性字段）
        - time：区间最后一个交易日的日期（K 线时间戳）
    """
    sorted_rows = sorted(rows, key=lambda r: r.trade_date)
    if interval == "1d":
        # 日线直接转换，不需要聚合
        return [
            BarPoint(
                time=r.trade_date.isoformat(),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                amount=r.amount,
                turnover_rate_avg=r.turnover_rate,
                consecutive_limit_up_days=r.consecutive_limit_up_days,
                consecutive_limit_down_days=r.consecutive_limit_down_days,
                consecutive_up_days=r.consecutive_up_days,
                consecutive_down_days=r.consecutive_down_days,
            )
            for r in sorted_rows
        ]

    # 多周期聚合：先按周期键分组，再对每组计算 OHLCV
    groups: dict[tuple[str, str], list[DailyRow]] = defaultdict(list)
    for r in sorted_rows:
        groups[_period_key(r.trade_date, interval)].append(r)

    out: list[BarPoint] = []
    # 按周期键排序（保证输出按时间升序）
    for key in sorted(groups.keys(), key=lambda k: k[1]):
        g = groups[key]
        g.sort(key=lambda x: x.trade_date)
        first, last = g[0], g[-1]  # 取首尾两个交易日
        high = max(x.high for x in g)
        low = min(x.low for x in g)
        vol = sum(x.volume for x in g)
        amt = sum(x.amount for x in g)
        # 换手率取有效值的平均（某些日期可能无换手率数据）
        turns = [x.turnover_rate for x in g if x.turnover_rate is not None]
        avg_turn = sum(turns) / len(turns) if turns else None
        out.append(
            BarPoint(
                time=last.trade_date.isoformat(),   # K 线时间戳 = 区间末日
                open=float(first.open),
                high=float(high),
                low=float(low),
                close=float(last.close),
                volume=float(vol),
                amount=float(amt),
                turnover_rate_avg=float(avg_turn) if avg_turn is not None else None,
                consecutive_limit_up_days=last.consecutive_limit_up_days,
                consecutive_limit_down_days=last.consecutive_limit_down_days,
                consecutive_up_days=last.consecutive_up_days,
                consecutive_down_days=last.consecutive_down_days,
            )
        )
    return out
