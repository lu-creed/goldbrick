"""数据看板：与「数据后台 / 数据池」解耦的行情向接口。

个股列表：按交易日列出全部 A 股（元数据中 asset_type=stock）的 OHLCV 与涨跌幅等，
不包含 K 线条数、是否已同步、复权因子同步情况——那些见 GET /api/sync/data-center。
"""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import DailyUniverseOut, DailyUniverseRow
from app.services.daily_universe import list_daily_universe, parse_daily_universe_filters

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

SortField = Literal["ts_code", "pct_change", "close", "volume", "amount", "turnover_rate"]


@router.get("/daily-stocks", response_model=DailyUniverseOut)
def get_daily_stocks(
    trade_date: Optional[date] = Query(None, description="交易日；缺省为本地 bars_daily 最新日"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: SortField = Query("pct_change"),
    order: Literal["asc", "desc"] = Query("desc"),
    code_contains: Optional[str] = Query(None, description="证券代码子串，大小写不敏感"),
    name_contains: Optional[str] = Query(None, description="证券名称子串"),
    market_contains: Optional[str] = Query(None, description="市场字段子串"),
    exchange_contains: Optional[str] = Query(None, description="交易所字段子串"),
    pct_min: Optional[float] = Query(None, description="涨跌幅%% 最小值（无昨收无法算涨跌幅的行会被排除）"),
    pct_max: Optional[float] = Query(None, description="涨跌幅%% 最大值"),
    open_min: Optional[float] = Query(None),
    open_max: Optional[float] = Query(None),
    high_min: Optional[float] = Query(None),
    high_max: Optional[float] = Query(None),
    low_min: Optional[float] = Query(None),
    low_max: Optional[float] = Query(None),
    close_min: Optional[float] = Query(None),
    close_max: Optional[float] = Query(None),
    volume_min: Optional[int] = Query(None, ge=0),
    volume_max: Optional[int] = Query(None, ge=0),
    amount_min: Optional[float] = Query(None, ge=0),
    amount_max: Optional[float] = Query(None, ge=0),
    turnover_min: Optional[float] = Query(None, ge=0),
    turnover_max: Optional[float] = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    flt = parse_daily_universe_filters(
        code_contains=code_contains,
        name_contains=name_contains,
        market_contains=market_contains,
        exchange_contains=exchange_contains,
        pct_min=pct_min,
        pct_max=pct_max,
        open_min=open_min,
        open_max=open_max,
        high_min=high_min,
        high_max=high_max,
        low_min=low_min,
        low_max=low_max,
        close_min=close_min,
        close_max=close_max,
        volume_min=volume_min,
        volume_max=volume_max,
        amount_min=amount_min,
        amount_max=amount_max,
        turnover_min=turnover_min,
        turnover_max=turnover_max,
    )
    raw = list_daily_universe(db, trade_date, page, page_size, sort, order, filters=flt)
    items = [DailyUniverseRow.model_validate(r) for r in raw["items"]]
    return DailyUniverseOut(
        trade_date=raw["trade_date"],
        latest_bar_date=raw["latest_bar_date"],
        total=raw["total"],
        page=raw["page"],
        page_size=raw["page_size"],
        items=items,
    )
