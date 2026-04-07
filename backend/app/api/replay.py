"""股票复盘（V2.0.1）：单日市场情绪与三大股指。"""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ReplayDailyOut, ReplayBucket, ReplayIndexCard, ReplayStockRow
from app.services.replay_daily import _max_bar_date, build_replay_daily

router = APIRouter(prefix="/replay", tags=["replay"])


@router.get("/daily", response_model=ReplayDailyOut)
def replay_daily(
    trade_date: Optional[date] = None,
    list_limit: int = Query(300, ge=50, le=2000),
    db: Session = Depends(get_db),
):
    """
    单日复盘聚合：涨跌家数、涨跌幅分布桶、涨跌停家数、涨跌平均换手、三大股指、振幅前列股票列表。
    未传 trade_date 时使用本地 bars_daily 中最新交易日。
    """
    d = trade_date
    if d is None:
        d = _max_bar_date(db)
        if d is None:
            raise HTTPException(status_code=400, detail="本地无任何日线数据，请先同步")
    raw = build_replay_daily(db, d, list_limit=list_limit)
    return ReplayDailyOut(
        trade_date=raw["trade_date"],
        latest_bar_date=raw["latest_bar_date"],
        universe_note=raw["universe_note"],
        up_count=raw["up_count"],
        down_count=raw["down_count"],
        flat_count=raw["flat_count"],
        limit_up_count=raw["limit_up_count"],
        limit_down_count=raw["limit_down_count"],
        buckets=[ReplayBucket(**x) for x in raw["buckets"]],
        turnover_avg_up=raw["turnover_avg_up"],
        turnover_avg_down=raw["turnover_avg_down"],
        indices=[ReplayIndexCard(**x) for x in raw["indices"]],
        stocks=[ReplayStockRow(**x) for x in raw["stocks"]],
    )
