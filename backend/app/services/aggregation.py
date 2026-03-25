"""由日 K 聚合多周期 K 线（查询时计算，不落库）。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from app.schemas import BarPoint, Interval


@dataclass
class DailyRow:
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
    """rows 需按 trade_date 升序。"""
    sorted_rows = sorted(rows, key=lambda r: r.trade_date)
    if interval == "1d":
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

    groups: dict[tuple[str, str], list[DailyRow]] = defaultdict(list)
    for r in sorted_rows:
        groups[_period_key(r.trade_date, interval)].append(r)

    out: list[BarPoint] = []
    for key in sorted(groups.keys(), key=lambda k: k[1]):
        g = groups[key]
        g.sort(key=lambda x: x.trade_date)
        first, last = g[0], g[-1]
        high = max(x.high for x in g)
        low = min(x.low for x in g)
        vol = sum(x.volume for x in g)
        amt = sum(x.amount for x in g)
        turns = [x.turnover_rate for x in g if x.turnover_rate is not None]
        avg_turn = sum(turns) / len(turns) if turns else None
        out.append(
            BarPoint(
                time=last.trade_date.isoformat(),
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
