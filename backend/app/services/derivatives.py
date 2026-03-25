"""日 K 派生字段：连涨/连跌与简化版涨跌停连板。"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import BarDaily


def recompute_consecutive_for_symbol(db: Session, symbol_id: int) -> None:
    bars: Sequence[BarDaily] = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == symbol_id)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        return

    up_streak = 0
    down_streak = 0
    limit_up_streak = 0
    limit_down_streak = 0
    prev_close: Optional[float] = None

    for b in bars:
        c = float(b.close)
        if prev_close is None:
            up_streak = down_streak = 0
            limit_up_streak = limit_down_streak = 0
        else:
            if c > prev_close:
                up_streak += 1
                down_streak = 0
            elif c < prev_close:
                down_streak += 1
                up_streak = 0
            else:
                up_streak = down_streak = 0

            change_ratio = (c - prev_close) / prev_close if prev_close != 0 else 0.0
            # V1.0.0 简化规则：非 ST 近似按 10% 涨跌停处理，使用 9.8% 阈值留误差空间。
            if change_ratio >= 0.098:
                limit_up_streak += 1
                limit_down_streak = 0
            elif change_ratio <= -0.098:
                limit_down_streak += 1
                limit_up_streak = 0
            else:
                limit_up_streak = 0
                limit_down_streak = 0
        b.consecutive_up_days = up_streak
        b.consecutive_down_days = down_streak
        b.consecutive_limit_up_days = limit_up_streak
        b.consecutive_limit_down_days = limit_down_streak
        prev_close = c

    db.commit()


def daterange_start_default() -> date:
    """默认同步起点：约 5 年日线。"""
    from datetime import timedelta

    return date.today() - timedelta(days=365 * 5)
