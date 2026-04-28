"""日 K 派生字段：连涨/连跌与按板块限价的连涨停/连跌停（口径与 limit_rules 对齐）。

连板口径（0.0.4-dev 升级）：
- 从「收盘涨幅 ≥ 9.8%」改为「最高价触及板块限价容差（容差 0.98）」，与复盘、回测一字板判定统一。
- 按板块分档：主板 10% / 创业板、科创板 20% / 北交所 30%；ST 走 5%。
- 新股上市后无涨跌幅窗口（主板/北交所 1 日；创业板/科创板 5 日）内不计入连板。

连涨/连跌（close > prev_close）语义不变。
"""
from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.models import BarDaily, InstrumentMeta, Symbol
from app.services.limit_rules import (
    effective_limit_pct,
    hits_limit_down,
    hits_limit_up,
)


def recompute_consecutive_for_symbol(db: Session, symbol_id: int) -> None:
    """按 bars_daily 升序遍历单只股票，回写 consecutive_* 四字段。

    连板新口径需要该股的板块元数据（market/exchange/list_date/name）。
    找不到元数据时退回保守规则（主板 10%，不做新股豁免）。
    """
    bars: Sequence[BarDaily] = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == symbol_id)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        return

    # 反查板块元数据（ts_code → instrument_meta）。连板口径依赖 market/exchange/list_date。
    sym = db.query(Symbol).filter(Symbol.id == symbol_id).one_or_none()
    meta: Optional[InstrumentMeta] = None
    if sym:
        meta = (
            db.query(InstrumentMeta)
            .filter(InstrumentMeta.ts_code == sym.ts_code)
            .one_or_none()
        )
    ts_code = sym.ts_code if sym else ""
    name = meta.name if meta else None
    market = meta.market if meta else None
    exchange = meta.exchange if meta else None
    list_date = meta.list_date if meta else None

    up_streak = 0
    down_streak = 0
    limit_up_streak = 0
    limit_down_streak = 0
    prev_close: Optional[float] = None
    # 上市后第几个交易日（从 1 开始）；仅在 trade_date >= list_date 时计数
    ipo_trade_idx = 0

    for b in bars:
        c = float(b.close)
        if list_date is None or b.trade_date >= list_date:
            ipo_trade_idx += 1

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

            limit_pct = effective_limit_pct(
                name,
                market,
                exchange,
                ts_code,
                b.trade_date,
                list_date,
                days_since_ipo_trade=(ipo_trade_idx if ipo_trade_idx > 0 else None),
            )
            high = float(b.high)
            low = float(b.low)
            # 新股豁免日（limit_pct=None）或 ST 边界都不累计连板
            if hits_limit_up(high, prev_close, limit_pct):
                limit_up_streak += 1
                limit_down_streak = 0
            elif hits_limit_down(low, prev_close, limit_pct):
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
