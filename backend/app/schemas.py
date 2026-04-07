"""接口收发的数据形状（请求体、返回 JSON 的结构）。

各 api 文件里的函数参数和返回值会引用这里的类；改字段名要前后端一起对一下。
"""

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SymbolCreate(BaseModel):
    ts_code: str = Field(..., examples=["600000.SH"])
    name: Optional[str] = None


class SymbolOut(BaseModel):
    id: int
    ts_code: str
    name: Optional[str]

    model_config = {"from_attributes": True}


class SymbolPatch(BaseModel):
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
    pause_requested: bool = False
    cancel_requested: bool = False

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


class ManualFetchAllRequest(BaseModel):
    """全市场手动拉取请求体：`/sync/fetch-all` 用全部个股；`/sync/fetch-all-index` 用已登记指数。与 /fetch 日期字段语义一致。"""

    start_date: Optional[date] = None
    end_date: date
    from_listing: bool = False


class UniverseSyncOut(BaseModel):
    stock_count: int
    index_count: int
    total: int
    from_cache: bool = False
    last_sync_date: Optional[date] = None
    inserted_stocks: int = 0
    updated_stocks: int = 0


class DataCenterRow(BaseModel):
    ts_code: str
    name: Optional[str]
    asset_type: str
    list_date: Optional[date]
    market: Optional[str] = None
    exchange: Optional[str] = None
    synced_once: bool
    first_bar_date: Optional[date]
    last_bar_date: Optional[date]
    bar_count: int
    adj_factor_count: int = 0
    adj_factor_coverage_ratio: float = 0.0
    adj_factor_synced: bool = False


class IndexCandidateRow(BaseModel):
    ts_code: str
    name: Optional[str] = None
    market: Optional[str] = None
    publisher: Optional[str] = None
    list_date: Optional[str] = None


class IndexMetaApplyItem(BaseModel):
    ts_code: str
    name: Optional[str] = None
    list_date: Optional[date] = None


class IndexMetaApplyRequest(BaseModel):
    items: list[IndexMetaApplyItem]


class IndexMetaApplyResult(BaseModel):
    added: int
    skipped: int


class SymbolDailyRow(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    turnover_rate: Optional[float]
    has_adj_factor: bool


class SymbolDailyPage(BaseModel):
    total: int
    items: list[SymbolDailyRow]


class SingleDaySyncRequest(BaseModel):
    ts_code: str
    trade_date: date


class ReplayIndexCard(BaseModel):
    """单日复盘：三大股指卡片。"""

    ts_code: str
    name: str
    close: float
    pct_change: Optional[float] = None
    amount: float = 0.0
    data_ok: bool = True
    message: Optional[str] = None


class ReplayBucket(BaseModel):
    key: str
    label: str
    count: int


class ReplayStockRow(BaseModel):
    ts_code: str
    name: Optional[str] = None
    pct_change: float
    close: float
    turnover_rate: Optional[float] = None
    bucket: str


class ReplayDailyOut(BaseModel):
    """GET /api/replay/daily 返回体。"""

    trade_date: date
    latest_bar_date: Optional[date] = None
    universe_note: str
    up_count: int
    down_count: int
    flat_count: int
    limit_up_count: int
    limit_down_count: int
    buckets: list[ReplayBucket]
    turnover_avg_up: Optional[float] = None
    turnover_avg_down: Optional[float] = None
    indices: list[ReplayIndexCard]
    stocks: list[ReplayStockRow]


class DailyUniverseRow(BaseModel):
    """单日全市场个股行情行（个股列表页；不含同步元数据）。"""

    ts_code: str
    name: Optional[str] = None
    market: Optional[str] = None
    exchange: Optional[str] = None
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    turnover_rate: Optional[float] = None
    pct_change: Optional[float] = None


class DailyUniverseOut(BaseModel):
    """GET /api/dashboard/daily-stocks 返回体。"""

    trade_date: Optional[date] = None
    latest_bar_date: Optional[date] = None
    total: int
    page: int
    page_size: int
    items: list[DailyUniverseRow]


# ---- 用户自定义指标（PRD DSL + 旧版 expr）----


class UserIndicatorParamDef(BaseModel):
    """本指标的可调参数（默认值在配置页设置；K 线/选股可再改，引擎后续扩展）。"""

    name: str = Field(..., min_length=1, max_length=32)
    description: Optional[str] = None
    default_value: Optional[str] = None


class UserSubIndicatorDef(BaseModel):
    """单条子线：公式树 + 展示/选股开关。"""

    key: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    auxiliary_only: bool = False
    use_in_screening: bool = True
    use_in_chart: bool = True
    chart_kind: Optional[Literal["line", "bar"]] = None
    initial_value: Optional[str] = None
    formula: dict[str, Any] = Field(default_factory=dict)


class UserIndicatorDefinitionBody(BaseModel):
    version: Literal[1] = 1
    params: list[UserIndicatorParamDef] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=lambda: ["1d"])
    sub_indicators: list[UserSubIndicatorDef] = Field(default_factory=list)


class UserIndicatorCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    definition: Optional[dict[str, Any]] = None
    expr: Optional[str] = Field(None, max_length=4000)
    # 与 PRD 一致：保存前用该标的试算；请保证本地已同步其日线。
    trial_ts_code: str = Field("600000.SH", min_length=6, max_length=32)

    @model_validator(mode="after")
    def need_definition_or_expr(self):
        if self.definition is None and (self.expr is None or not str(self.expr).strip()):
            raise ValueError("请提供 definition（PRD 指标 DSL）或 expr（旧版单条表达式）")
        return self


class UserIndicatorPatch(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    definition: Optional[dict[str, Any]] = None
    expr: Optional[str] = Field(None, max_length=4000)
    trial_ts_code: Optional[str] = Field(None, min_length=6, max_length=32)


class UserIndicatorOut(BaseModel):
    id: int
    code: str
    display_name: str
    description: Optional[str]
    kind: Literal["dsl", "legacy"]
    definition: Optional[dict[str, Any]] = None
    expr: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CustomIndicatorVariableNamesOut(BaseModel):
    names: list[str]


class BuiltinCatalogItem(BaseModel):
    """内置指标 + 子线列表（公式编辑器「引用内置」用）。"""

    name: str
    display_name: str
    subs: list[dict[str, Any]]


class UserIndicatorValidateRequest(BaseModel):
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None


class UserIndicatorValidateExprRequest(BaseModel):
    """保存前试算：仅传表达式 + 标的。"""

    expr: str = Field(..., min_length=1, max_length=4000)
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None


class UserIndicatorValidateDefinitionRequest(BaseModel):
    """保存前试算：完整 definition JSON + 标的。"""

    definition: dict[str, Any]
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None


class UserIndicatorSampleRow(BaseModel):
    trade_date: str
    # DSL：多子线；legacy：仅用 value
    values: Optional[dict[str, Optional[float]]] = None
    value: Optional[float] = None
    error: Optional[str] = None
    diagnostics: Optional[list[dict[str, Any]]] = None


class UserIndicatorValidateOut(BaseModel):
    ok: bool
    message: str
    sample_rows: list[UserIndicatorSampleRow]
    error_detail: Optional[str] = None
    report_keys: Optional[list[str]] = None


# ---- 条件选股（自定义指标 DSL / 旧版 expr）----


class ScreeningRunIn(BaseModel):
    """指定交易日，用已保存的自定义指标在当日截面上筛选标的。"""

    trade_date: date
    user_indicator_id: int = Field(..., ge=1)
    sub_key: Optional[str] = Field(None, max_length=64)
    compare_op: str = Field("gt", description="gt|lt|eq|gte|le|ne")
    threshold: float = 0.0
    max_scan: int = Field(6000, ge=100, le=8000)


class ScreeningStockRow(BaseModel):
    ts_code: str
    name: Optional[str] = None
    close: float
    pct_change: Optional[float] = None
    indicator_value: float


class ScreeningRunOut(BaseModel):
    trade_date: str
    user_indicator_id: Optional[int] = None
    sub_key: Optional[str] = None
    compare_op: Optional[str] = None
    threshold: Optional[float] = None
    scanned: int
    matched: int
    note: Optional[str] = None
    items: list[ScreeningStockRow]


class CustomIndicatorPoint(BaseModel):
    time: str
    value: Optional[float] = None


class CustomIndicatorSeriesOut(BaseModel):
    """K 线副图：与日线时间对齐的自定义子线序列（仅 1d）。"""

    ts_code: str
    user_indicator_id: int
    sub_key: str
    display_name: str
    points: list[CustomIndicatorPoint]

