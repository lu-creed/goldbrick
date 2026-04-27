"""数据看板 API（路径前缀 /api/dashboard/）。

提供面向行情展示的接口，与「数据后台/数据池管理」解耦。

当前接口：
  GET /api/dashboard/daily-stocks
    - 按交易日列出全部 A 股的 OHLCV、涨跌幅等行情数据
    - 支持多维度筛选（代码、名称、价格区间、涨跌幅区间、换手率区间等）
    - 支持排序（涨跌幅/收盘价/成交量/成交额/换手率）和分页
    - 对应前端「个股列表」页（StockListPage）

与 /api/sync/data-center 的区别：
  - data-center：关注数据完整性（K 线条数、复权因子覆盖率、是否已同步）
  - daily-stocks：关注当日行情展示（价格、涨跌幅，面向投资分析）

业务逻辑在 app/services/daily_universe.py 中实现。
"""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.schemas import DailyUniverseOut, DailyUniverseRow
from app.services.daily_universe import list_daily_universe, parse_daily_universe_filters

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# 合法的排序字段集合，限制前端传入的 sort 参数（防止 SQL 注入或拼写错误）
SortField = Literal["ts_code", "pct_change", "close", "volume", "amount", "turnover_rate"]


@router.get("/daily-stocks", response_model=DailyUniverseOut)
def get_daily_stocks(
    trade_date: Optional[date] = Query(None, description="交易日；缺省为本地 bars_daily 最新日"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: SortField = Query("pct_change"),
    order: Literal["asc", "desc"] = Query("desc"),
    # 字符串筛选（模糊匹配）
    code_contains: Optional[str] = Query(None, description="证券代码子串，大小写不敏感"),
    name_contains: Optional[str] = Query(None, description="证券名称子串"),
    market_contains: Optional[str] = Query(None, description="市场字段子串"),
    exchange_contains: Optional[str] = Query(None, description="交易所字段子串"),
    # 数值区间筛选
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
    _user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按交易日列出全市场 A 股行情，支持筛选、排序、分页。

    默认行为（不传任何参数）：返回本地最新交易日、按涨跌幅降序排列的第 1 页（50 条）。

    筛选逻辑：
    - 字符串字段（code/name/market/exchange）：模糊匹配（包含即可）
    - 数值字段（价格/成交量/换手率/涨跌幅）：闭区间 [min, max]
    - 无昨日收盘价的股票（新股首日等）：若筛选了涨跌幅则被排除；无筛选时仍展示（pct_change=null）
    - 区间上下界填反时（min > max）：服务端自动交换，不会返回空集

    排序注意：
    - pct_change/turnover_rate 可能为 null，null 值永远排在末尾（无论升序还是降序）

    Returns:
        DailyUniverseOut，包含 trade_date/latest_bar_date/total/page/page_size/items。
    """
    # 将 HTTP 参数整理为结构化的筛选对象（parse_daily_universe_filters 会做清洁和规范化）
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
    # 将 service 层返回的 dict list 转换为 Pydantic 模型（触发字段校验和序列化）
    items = [DailyUniverseRow.model_validate(r) for r in raw["items"]]
    return DailyUniverseOut(
        trade_date=raw["trade_date"],
        latest_bar_date=raw["latest_bar_date"],
        total=raw["total"],
        page=raw["page"],
        page_size=raw["page_size"],
        items=items,
    )
