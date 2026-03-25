from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SymbolCreate(BaseModel):
    ts_code: str = Field(..., examples=["600000.SH"])
    name: Optional[str] = None


class SymbolOut(BaseModel):
    id: int
    ts_code: str
    name: Optional[str]
    enabled: bool

    model_config = {"from_attributes": True}


class SymbolPatch(BaseModel):
    enabled: Optional[bool] = None
    name: Optional[str] = None


Interval = Literal["1d", "1w", "1M", "1Q", "1y"]


class BarPoint(BaseModel):
    time: str  # YYYY-MM-DD 区间结束交易日
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    turnover_rate_avg: Optional[float] = None
    consecutive_limit_up_days: Optional[int] = None
    consecutive_limit_down_days: Optional[int] = None
    consecutive_up_days: Optional[int] = None
    consecutive_down_days: Optional[int] = None


class SyncJobOut(BaseModel):
    id: int
    cron_expr: str
    enabled: bool
    last_run_at: Optional[datetime]
    last_status: Optional[str]
    last_error: Optional[str]

    model_config = {"from_attributes": True}


class SyncJobUpdate(BaseModel):
    cron_expr: Optional[str] = None
    enabled: Optional[bool] = None


class SyncRunOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: Optional[datetime]
    trigger: str
    status: str
    message: Optional[str]
    log_path: Optional[str]

    model_config = {"from_attributes": True}


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: Optional[str] = None


class TushareSymbolOut(BaseModel):
    ts_code: str
    name: Optional[str] = None

    model_config = {"from_attributes": True}


class ManualFetchRequest(BaseModel):
    ts_codes: list[str]
    start_date: Optional[date] = None
    end_date: date
    from_listing: bool = False


class BuyOnceBacktestRequest(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    buy_date: date
    buy_price: float
    buy_qty: int = Field(..., ge=1)
    initial_cash: float = Field(100000.0, ge=0)
    adj: Literal["none", "qfq", "hfq"] = "none"


class BacktestDailyPoint(BaseModel):
    trade_date: date
    close: float
    stock_value: float
    cash_value: float
    total_asset: float
    daily_pnl: float
    cum_return: float


class BuyOnceBacktestResponse(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    buy_date: date
    buy_price: float
    buy_qty: int
    initial_cash: float
    remaining_cash: float
    max_drawdown: float
    daily: list[BacktestDailyPoint]


class BuySellBacktestRequest(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    buy_date: date
    buy_price: float
    buy_qty: int = Field(..., ge=1)
    initial_cash: float = Field(100000.0, ge=0)
    sell_target_price: Optional[float] = None
    sell_target_return: Optional[float] = None
    sell_target_date: Optional[date] = None
    sell_logic: Literal["or", "and"] = "or"
    adj: Literal["none", "qfq", "hfq"] = "none"


class BuySellBacktestResponse(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    buy_date: date
    sell_date: Optional[date]
    sell_price: Optional[float]
    sell_reason: Optional[str]
    buy_price: float
    buy_qty: int
    initial_cash: float
    remaining_cash: float
    max_drawdown: float
    daily: list[BacktestDailyPoint]


# ---- V1.0.6 条件买入 ----

class IndicatorRef(BaseModel):
    """指标/数字引用，用于条件表达式的左边或右边。"""
    kind: Literal["number", "indicator"]
    value: Optional[float] = None      # kind=number 时使用
    sub_name: Optional[str] = None     # kind=indicator 时使用，如 "MA5" / "K" / "close"


class BuyTimingConfig(BaseModel):
    """买入时机：时间偏移 + 条件判断。"""
    time_offset: int = 0               # 0=当日T，-1=T-1交易日，以此类推（≤0）
    condition_type: Literal["price", "indicator"]
    price: Optional[float] = None      # condition_type=price 时：当日 low < price < high
    left: Optional[IndicatorRef] = None
    operator: Optional[Literal["gt", "eq", "lt"]] = None
    right: Optional[IndicatorRef] = None


class BuyPriceConfig(BaseModel):
    """买入价格：定价 or 指标价（取 T 日值）。"""
    type: Literal["fixed", "indicator"]
    fixed_price: Optional[float] = None
    sub_name: Optional[str] = None     # type=indicator 时用，取 T 日对应子指标值


class BuyQtyConfig(BaseModel):
    """买入数量：定量 or 比例（均以 100 股为单位向下取整）。"""
    type: Literal["fixed", "ratio"]
    fixed_qty: Optional[int] = None    # 股数，需为 100 的倍数
    ratio: Optional[float] = None      # 0~1，占当前现金比例


class ConditionBuyRequest(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    initial_cash: float = Field(100000.0, ge=0)
    adj: Literal["none", "qfq", "hfq"] = "none"
    buy_timing: BuyTimingConfig
    buy_price: BuyPriceConfig
    buy_qty: BuyQtyConfig
    # 卖出条件（沿用 V1.0.3）
    sell_target_price: Optional[float] = None
    sell_target_return: Optional[float] = None
    sell_target_date: Optional[date] = None
    sell_logic: Literal["or", "and"] = "or"


class ConditionBuyDailyPoint(BaseModel):
    trade_date: date
    close: float
    holding_qty: int
    stock_value: float
    cash_value: float
    total_asset: float
    daily_pnl: float
    cum_return: float


class ConditionBuyResponse(BaseModel):
    ts_code: str
    start_date: date
    end_date: date
    initial_cash: float
    remaining_cash: float
    buy_count: int
    sell_date: Optional[date] = None
    sell_price: Optional[float] = None
    sell_reason: Optional[str] = None
    max_drawdown: float
    daily: list[ConditionBuyDailyPoint]


class UniverseSyncOut(BaseModel):
    stock_count: int
    index_count: int
    total: int
    from_cache: bool = False
    last_sync_date: Optional[date] = None


class DataCenterRow(BaseModel):
    ts_code: str
    name: Optional[str]
    asset_type: str
    list_date: Optional[date]
    synced_once: bool
    first_bar_date: Optional[date]
    last_bar_date: Optional[date]
    bar_count: int
    adj_factor_count: int = 0
    adj_factor_coverage_ratio: float = 0.0
    adj_factor_synced: bool = False
