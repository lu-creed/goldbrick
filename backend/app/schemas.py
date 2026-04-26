"""
接口收发的数据形状（请求体、返回 JSON 的结构）。

Pydantic 的 BaseModel 类似「数据合同」：
  - 请求体（客户端发来的数据）：FastAPI 自动用它校验和解析
  - 返回体（服务端返回的 JSON）：FastAPI 自动用它序列化
改字段名要前后端一起对一下（前端 client.ts 里的类型定义对应这里）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ---- 股票池（symbols）----

class SymbolCreate(BaseModel):
    """添加股票到本地池的请求体。"""
    ts_code: str = Field(..., examples=["600000.SH"])  # 股票代码，格式 XXXXXX.SH 或 XXXXXX.SZ
    name: Optional[str] = None                          # 股票名称（可选，不填时留空）


class SymbolOut(BaseModel):
    """返回给前端的股票信息。"""
    id: int
    ts_code: str
    name: Optional[str]

    model_config = {"from_attributes": True}  # 允许从 ORM 对象直接构建


class SymbolPatch(BaseModel):
    """修改股票名称的请求体（目前只能改名称）。"""
    name: Optional[str] = None


# K 线周期类型：1d=日线 / 1w=周线 / 1M=月线 / 1Q=季线 / 1y=年线
Interval = Literal["1d", "1w", "1M", "1Q", "1y"]


class BarPoint(BaseModel):
    """一根 K 线的数据：包含时间、OHLCV 及衍生字段。

    time：区间结束的交易日（日线=当天，周线=周五，月线=月最后交易日），格式 YYYY-MM-DD。
    volume/amount：成交量（手）和成交额（元）。
    turnover_rate_avg：区间内日均换手率；日线=当日换手率，多周期聚合时取均值。
    consecutive_*：连续涨跌停/上涨/下跌天数，取区间最后一个交易日的值。
    """
    time: str   # YYYY-MM-DD，区间结束交易日
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


# ---- 同步任务（sync）----

class SyncJobOut(BaseModel):
    """定时任务配置的返回格式。"""
    id: int
    cron_expr: str        # 5 域 cron 表达式，如 "0 18 * * *"
    enabled: bool         # 是否启用定时同步
    last_run_at: Optional[datetime]   # 上次执行时间
    last_status: Optional[str]        # 上次执行结果：success / failed / cancelled
    last_error: Optional[str]         # 上次失败的错误信息

    model_config = {"from_attributes": True}


class SyncJobUpdate(BaseModel):
    """修改定时任务配置的请求体（PUT /api/sync/job）。"""
    cron_expr: Optional[str] = None   # 新的 cron 表达式（不填则不修改）
    enabled: Optional[bool] = None    # 是否启用（不填则不修改）


class SyncRunOut(BaseModel):
    """一次同步运行记录的返回格式（包含进度信息）。"""
    id: int
    started_at: datetime
    finished_at: Optional[datetime]
    trigger: str               # schedule（定时）/ manual（手动）
    status: str                # queued / running / paused / success / failed / cancelled
    message: Optional[str]     # 进度摘要，如 "progress 100/5000 [2%] ok=99 fail=1"
    log_path: Optional[str]    # 日志文件路径（前端可请求 /sync/runs/{id}/log 读取）
    pause_requested: bool = False    # 前端是否已请求暂停
    cancel_requested: bool = False   # 前端是否已请求取消

    model_config = {"from_attributes": True}


class ErrorBody(BaseModel):
    """统一错误返回体（前端可根据 code 判断错误类型）。"""
    code: str
    message: str
    detail: Optional[str] = None


class TushareSymbolOut(BaseModel):
    """从 Tushare 拉取的股票代码信息（供下拉选择）。"""
    ts_code: str
    name: Optional[str] = None

    model_config = {"from_attributes": True}


class ManualFetchRequest(BaseModel):
    """手动按股票列表+日期范围拉取日线的请求体（POST /api/sync/fetch）。

    ts_codes：要同步的股票代码列表，如 ["600000.SH", "000001.SZ"]。
    start_date：起始日期（None + from_listing=True 表示从上市日开始）。
    end_date：截止日期（含）。
    from_listing：为 True 时忽略 start_date，改为从各股上市日开始拉（需要元数据中有 list_date）。
    """
    ts_codes: list[str]
    start_date: Optional[date] = None
    end_date: date
    from_listing: bool = False


class ManualFetchAllRequest(BaseModel):
    """全市场手动拉取请求体（/sync/fetch-all 或 /sync/fetch-all-index）。

    与 ManualFetchRequest 日期字段语义一致，但标的范围由后端按 instrument_meta 自动确定。
    """
    start_date: Optional[date] = None
    end_date: date
    from_listing: bool = False


class UniverseSyncOut(BaseModel):
    """同步股票/指数元数据后的结果摘要。"""
    stock_count: int       # 本地 instrument_meta 中个股总数
    index_count: int       # 本地 instrument_meta 中指数总数
    total: int             # stock_count + index_count
    from_cache: bool = False        # 是否直接返回了本地缓存（未调 Tushare）
    last_sync_date: Optional[date] = None  # 上次完整同步日期
    inserted_stocks: int = 0       # 本次新增的个股数
    updated_stocks: int = 0        # 本次更新的个股数


class DataCenterRow(BaseModel):
    """数据后台：一只证券的数据同步状态概览。"""
    ts_code: str
    name: Optional[str]
    asset_type: str          # stock | index
    list_date: Optional[date]
    market: Optional[str] = None
    exchange: Optional[str] = None
    synced_once: bool        # 是否至少同步过一次（bar_count > 0）
    first_bar_date: Optional[date]   # 本地最早一根 K 线的日期
    last_bar_date: Optional[date]    # 本地最新一根 K 线的日期
    bar_count: int                   # 本地 K 线条数
    adj_factor_count: int = 0        # 本地复权因子条数
    adj_factor_coverage_ratio: float = 0.0   # 复权因子覆盖率（= adj_count / bar_count）
    adj_factor_synced: bool = False  # 是否完整同步了复权因子（覆盖率=100%）


class IndexCandidateRow(BaseModel):
    """从 Tushare 拉取的指数候选行（供前端弹窗勾选加入本地）。"""
    ts_code: str
    name: Optional[str] = None
    market: Optional[str] = None
    publisher: Optional[str] = None  # 发布机构，如 中证指数公司
    list_date: Optional[str] = None


class IndexMetaApplyItem(BaseModel):
    """用户勾选要加入本地的单条指数信息。"""
    ts_code: str
    name: Optional[str] = None
    list_date: Optional[date] = None


class IndexMetaApplyRequest(BaseModel):
    """批量将指数加入本地元数据的请求体（POST /api/sync/index-meta/apply）。"""
    items: list[IndexMetaApplyItem]


class IndexMetaApplyResult(BaseModel):
    """加入指数后的结果摘要。"""
    added: int    # 新增条数
    skipped: int  # 跳过条数（已存在则跳过）


class SymbolDailyRow(BaseModel):
    """数据后台「单股日 K 分页」中的一行。"""
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    turnover_rate: Optional[float]
    has_adj_factor: bool   # 该日是否有复权因子（可判断复权数据是否完整）


class SymbolDailyPage(BaseModel):
    """数据后台「单股日 K 分页」的分页返回体。"""
    total: int             # 总条数（用于前端计算页数）
    items: list[SymbolDailyRow]


class SingleDaySyncRequest(BaseModel):
    """补录/覆盖单日数据的请求体（POST /api/sync/single-day）。"""
    ts_code: str
    trade_date: date


# ---- 复盘（replay）----

class ReplayIndexCard(BaseModel):
    """单日复盘：三大股指（沪深300/上证指数/创业板指等）的当日数据卡片。"""
    ts_code: str
    name: str
    close: float
    pct_change: Optional[float] = None  # 涨跌幅%（无昨收时为 None）
    amount: float = 0.0                 # 成交额（元）
    data_ok: bool = True                # 数据是否正常（False 时显示 message）
    message: Optional[str] = None       # 数据异常说明


class ReplayBucket(BaseModel):
    """涨跌幅分布桶：将所有股票按涨跌幅分段统计数量。

    key：桶的代码标识（前端用来着色，如 "L+"=涨停 / "L-"=跌停）。
    label：展示标签，如 ">9%"、"+3~5%"。
    count：落在该区间的股票数量。
    """
    key: str
    label: str
    count: int


class ReplayStockRow(BaseModel):
    """复盘页：振幅前列个股的简要行情行。"""
    ts_code: str
    name: Optional[str] = None
    pct_change: float
    close: float
    turnover_rate: Optional[float] = None
    bucket: str   # 所在涨跌幅桶的 key（与 ReplayBucket.key 对应）


class ReplayDailyOut(BaseModel):
    """GET /api/replay/daily 复盘接口的完整返回体。"""
    trade_date: date
    latest_bar_date: Optional[date] = None   # 本地库中最新交易日（用于提示数据是否最新）
    universe_note: str        # 全市场说明文字，如 "全市场 5000 只个股，数据截至 2024-01-10"
    up_count: int             # 上涨家数
    down_count: int           # 下跌家数
    flat_count: int           # 平盘家数
    limit_up_count: int       # 涨停家数
    limit_down_count: int     # 跌停家数
    buckets: list[ReplayBucket]  # 涨跌幅分布（用于柱状图）
    turnover_avg_up: Optional[float] = None    # 上涨股平均换手率
    turnover_avg_down: Optional[float] = None  # 下跌股平均换手率
    indices: list[ReplayIndexCard]  # 三大股指卡片
    stocks: list[ReplayStockRow]    # 振幅前列个股


# ---- 数据看板（dashboard）个股列表 ----

class DailyUniverseRow(BaseModel):
    """单日全市场个股行情行（个股列表页）。

    pct_change：涨跌幅%，由后端用当日收盘/昨日收盘计算；无昨收时为 None。
    不含同步元数据（K 线条数、复权因子状态等）——那些见 DataCenterRow。
    """
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
    """GET /api/dashboard/daily-stocks 个股列表接口的分页返回体。"""
    trade_date: Optional[date] = None       # 实际查询日期（可能因未指定而使用最新日）
    latest_bar_date: Optional[date] = None  # 本地库中最新交易日（提示信息）
    total: int       # 过滤后的总条数（用于前端计算总页数）
    page: int
    page_size: int
    items: list[DailyUniverseRow]


# ---- 用户自定义指标（PRD DSL + 旧版 expr）----

class UserIndicatorParamDef(BaseModel):
    """自定义指标的一个可调参数（如 MA 的周期 N）。

    default_value 是字符串，前端显示默认值；引擎计算时将其转为数值。
    """
    name: str = Field(..., min_length=1, max_length=32)
    description: Optional[str] = None
    default_value: Optional[str] = None


class UserSubIndicatorDef(BaseModel):
    """自定义指标的一条输出子线（如 BOLL 有上中下三条子线）。

    key：子线的英文唯一标识（小写字母+数字+下划线）。
    formula：JSON 公式树，由公式构建器生成，求值引擎递归计算。
    auxiliary_only=True：辅助计算，不展示、不参与选股（如中间变量）。
    use_in_screening：是否出现在选股子线下拉中。
    use_in_chart：是否在 K 线副图中展示。
    chart_kind：副图展示形式，line=折线图，bar=柱状图。
    initial_value：该子线第一根 K 线的初始值（None=无初始值，计算失败则该日为 None）。
    """
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
    """完整的自定义指标 DSL 定义体（version=1 固定）。"""
    version: Literal[1] = 1
    params: list[UserIndicatorParamDef] = Field(default_factory=list)
    periods: list[str] = Field(default_factory=lambda: ["1d"])   # 支持的周期，如 ["1d", "1w"]
    sub_indicators: list[UserSubIndicatorDef] = Field(default_factory=list)


class UserIndicatorCreate(BaseModel):
    """创建自定义指标的请求体（POST /api/indicators/custom）。

    必须提供 definition（DSL 模式）或 expr（旧版单条表达式）二选一。
    trial_ts_code：保存前用这只股票试算，确保公式无误（需本地已同步该股日线）。
    """
    code: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = None
    definition: Optional[dict[str, Any]] = None   # DSL 模式：公式树 JSON
    expr: Optional[str] = Field(None, max_length=4000)  # 旧版模式：单行表达式字符串
    # 与 PRD 一致：保存前用该标的试算；请保证本地已同步其日线。
    trial_ts_code: str = Field("600000.SH", min_length=6, max_length=32)

    @model_validator(mode="after")
    def need_definition_or_expr(self):
        """校验：必须提供 definition 或 expr 至少一个。"""
        if self.definition is None and (self.expr is None or not str(self.expr).strip()):
            raise ValueError("请提供 definition（PRD 指标 DSL）或 expr（旧版单条表达式）")
        return self


class UserIndicatorPatch(BaseModel):
    """修改自定义指标的请求体（PATCH /api/indicators/custom/{id}）。

    字段均为可选：只传要修改的字段，其余不变。
    修改 definition 或 expr 时都要重新试算（trial_ts_code）。
    """
    display_name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    definition: Optional[dict[str, Any]] = None
    expr: Optional[str] = Field(None, max_length=4000)
    trial_ts_code: Optional[str] = Field(None, min_length=6, max_length=32)


class UserIndicatorOut(BaseModel):
    """返回给前端的自定义指标信息。

    kind="dsl"：新版 PRD 指标，definition 字段包含完整公式树。
    kind="legacy"：旧版单条表达式，expr 字段包含表达式字符串。
    """
    id: int
    code: str
    display_name: str
    description: Optional[str]
    kind: Literal["dsl", "legacy"]
    definition: Optional[dict[str, Any]] = None   # DSL 模式的完整定义
    expr: Optional[str] = None                    # 旧版模式的表达式
    created_at: datetime
    updated_at: datetime


class CustomIndicatorVariableNamesOut(BaseModel):
    """旧版 expr 中可用的变量名列表（如 MA5、MACD柱、close 等）。"""
    names: list[str]


class BuiltinCatalogItem(BaseModel):
    """内置指标及其子线目录（供公式构建器「引用内置」下拉选择）。"""
    name: str            # 指标英文标识，如 MA
    display_name: str    # 指标展示名，如 移动平均线
    subs: list[dict[str, Any]]   # 子线列表，每项含 name 和 description


class UserIndicatorValidateRequest(BaseModel):
    """对已保存指标做试算的请求体（POST /api/indicators/custom/{id}/validate）。"""
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None   # None=最近几日，指定日期=只算那天


class UserIndicatorValidateExprRequest(BaseModel):
    """保存前对旧版表达式做试算的请求体（POST /api/indicators/custom/validate-expr）。"""
    expr: str = Field(..., min_length=1, max_length=4000)
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None


class UserIndicatorValidateDefinitionRequest(BaseModel):
    """保存前对 DSL 定义做试算的请求体（POST /api/indicators/custom/validate-definition）。"""
    definition: dict[str, Any]
    ts_code: str = Field(..., min_length=6, max_length=32)
    trade_date: Optional[date] = None


class UserIndicatorSampleRow(BaseModel):
    """试算结果中的单行样本数据。

    DSL 模式：values 字段包含各子线的值（{子线key: 数值}）。
    旧版模式：value 字段包含单个数值。
    diagnostics：计算失败时的诊断信息列表（包含失败原因和位置）。
    """
    trade_date: str
    # DSL 多子线结果
    values: Optional[dict[str, Optional[float]]] = None
    # 旧版 legacy 单值结果
    value: Optional[float] = None
    error: Optional[str] = None
    diagnostics: Optional[list[dict[str, Any]]] = None


class UserIndicatorValidateOut(BaseModel):
    """试算结果的完整返回体。

    ok=True：公式正确，所有样本日期都成功计算。
    ok=False：至少有一行计算失败，前端展示 message 和 sample_rows 中的 diagnostics。
    report_keys：DSL 模式下参与展示的子线 key 列表（用于前端渲染表头）。
    """
    ok: bool
    message: str
    sample_rows: list[UserIndicatorSampleRow]
    error_detail: Optional[str] = None     # 第一个失败项的详细说明
    report_keys: Optional[list[str]] = None  # DSL 子线列（None 表示旧版）


# ---- 条件选股（自定义指标 DSL / 旧版 expr）----

class ScreeningRunIn(BaseModel):
    """条件选股的请求体（POST /api/screening/run）。

    user_indicator_id：使用哪个已保存的自定义指标。
    sub_key：DSL 指标选择哪条子线；旧版 expr 指标不需要（留 None）。
    compare_op：指标值与阈值的比较运算符（gt/lt/eq/gte/le/ne）。
    threshold：阈值，如「MA5 大于 0」中的 0。
    max_scan：最多扫描多少只股票（防止全市场扫描超时）。
    """
    trade_date: date
    user_indicator_id: int = Field(..., ge=1)
    sub_key: Optional[str] = Field(None, max_length=64)
    compare_op: str = Field("gt", description="gt|lt|eq|gte|le|ne")
    threshold: float = 0.0
    max_scan: int = Field(6000, ge=100, le=8000)


class ScreeningStockRow(BaseModel):
    """选股结果中的一只股票。"""
    ts_code: str
    name: Optional[str] = None
    close: float
    pct_change: Optional[float] = None   # 当日涨跌幅%
    indicator_value: float               # 该股在当日的指标数值


class ScreeningRunOut(BaseModel):
    """条件选股的结果返回体。"""
    trade_date: str
    user_indicator_id: Optional[int] = None
    sub_key: Optional[str] = None
    compare_op: Optional[str] = None
    threshold: Optional[float] = None
    scanned: int     # 实际扫描了多少只（受 max_scan 限制）
    matched: int     # 命中条件的股票数
    note: Optional[str] = None   # 后端警告（如数据不完整时的提示）
    items: list[ScreeningStockRow]


# ---- K 线副图：自定义指标子线序列 ----

class CustomIndicatorPoint(BaseModel):
    """副图指标的单个数据点。"""
    time: str               # YYYY-MM-DD
    value: Optional[float] = None   # 当日指标值（None=该日无数据）


class CustomIndicatorSeriesOut(BaseModel):
    """K 线副图：与日线时间对齐的自定义子线序列（仅 1d 日线）。"""
    ts_code: str
    user_indicator_id: int
    sub_key: str            # 子线 key
    display_name: str       # 指标展示名（前端图例用）
    points: list[CustomIndicatorPoint]


# ---- 股票回测 ----

class BacktestRunIn(BaseModel):
    """回测请求体（POST /api/backtest/run）。

    start_date / end_date：回测时间范围（闭区间）。
    user_indicator_id：使用哪个已保存的自定义指标。
    sub_key：DSL 指标选择哪条子线；旧版 expr 指标留 None。
    buy_op / buy_threshold：买入条件（指标值 buy_op buy_threshold 时建仓）。
    sell_op / sell_threshold：卖出条件（指标值 sell_op sell_threshold 时平仓）。
    initial_capital：初始资金（元）。
    max_positions：最多同时持有几只（等额分配资金）。
    max_scan：每个交易日最多扫描几只股票（防止超时）。
    """
    start_date: date
    end_date: date
    user_indicator_id: int = Field(..., ge=1)
    sub_key: Optional[str] = Field(None, max_length=64)
    buy_op: str = Field("gt", description="gt|lt|eq|gte|le|ne")
    buy_threshold: float = 0.0
    sell_op: str = Field("lt", description="gt|lt|eq|gte|le|ne")
    sell_threshold: float = 0.0
    initial_capital: float = Field(100_000.0, ge=1000)
    max_positions: int = Field(3, ge=1, le=10)
    max_scan: int = Field(3000, ge=100, le=8000)


class BacktestTradeRow(BaseModel):
    """回测结果中的一笔交易记录。

    buy_date / buy_price：建仓日期和价格。
    sell_date / sell_price：平仓日期和价格（None 表示回测结束时仍持有）。
    shares：持有的股数（小数，不考虑 A 股整手限制）。
    pnl：本笔盈亏金额（元）。
    pnl_pct：本笔盈亏百分比（%）。
    """
    ts_code: str
    name: Optional[str] = None
    buy_date: str
    buy_price: float
    shares: float
    sell_date: Optional[str] = None
    sell_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    buy_trigger_val: Optional[float] = None
    sell_trigger_val: Optional[float] = None


class BacktestEquityPoint(BaseModel):
    """资金曲线上的一个数据点（每个交易日一个点）。

    equity：当日收盘时的总资产（现金 + 所有持仓市值）。
    drawdown_pct：距历史最高点的回撤幅度（%，负数，如 -5.3 表示回撤了 5.3%）。
    """
    date: str
    equity: float
    drawdown_pct: float


class BacktestRunOut(BaseModel):
    """回测结果返回体。

    total_return_pct：总收益率（%），如 23.5 表示盈利 23.5%。
    max_drawdown_pct：最大回撤（%），如 -12.1 表示最大曾亏损 12.1%。
    win_rate：胜率（%），已平仓交易中盈利的比例；None 表示无已平仓交易。
    scanned_stocks：回测期间共扫描的股票总数（去重后）。
    equity_curve：逐日权益曲线。
    trades：全部交易记录（包含仍持有的开仓记录）。

    高级绩效指标（基于已平仓交易计算）：
    annualized_return：年化收益率（%），以 252 交易日/年折算。
    sharpe_ratio：夏普比率，日超额收益均值 / 日收益标准差 × sqrt(252)。
    calmar_ratio：卡玛比率 = 年化收益率 / |最大回撤|，衡量单位回撤所对应的收益。
    profit_factor：盈亏比 = 全部盈利总额 / |全部亏损总额|，> 1 表示整体盈利。
    avg_win_pct：平均每笔盈利幅度（%）。
    avg_loss_pct：平均每笔亏损幅度（%，正数表示亏损）。
    max_win_pct：单笔最大盈利（%）。
    max_loss_pct：单笔最大亏损（%，负数）。
    avg_holding_days：平均持仓自然日天数。
    total_win / total_loss：已平仓中盈利/亏损的笔数。
    """
    start_date: str
    end_date: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    win_rate: Optional[float] = None
    scanned_stocks: int
    equity_curve: list[BacktestEquityPoint]
    trades: list[BacktestTradeRow]
    note: Optional[str] = None
    # 高级绩效指标
    annualized_return: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    avg_win_pct: Optional[float] = None
    avg_loss_pct: Optional[float] = None
    max_win_pct: Optional[float] = None
    max_loss_pct: Optional[float] = None
    avg_holding_days: Optional[float] = None
    total_win: int = 0
    total_loss: int = 0


# ---- 回测交易K线详情 ----

class TradeChartBarPoint(BaseModel):
    """K 线蜡烛图中的单根 K 线（精简版，仅包含绘图必需字段）。"""
    time: str
    open: float
    high: float
    low: float
    close: float


class TradeChartIndicatorPoint(BaseModel):
    """指标副图中的单个数据点。"""
    time: str
    value: Optional[float] = None


class TradeChartOut(BaseModel):
    """回测交易K线详情：K线 + 指标子线，供Drawer中的验证图使用。"""
    bars: list[TradeChartBarPoint]
    indicator: list[TradeChartIndicatorPoint]
    sub_key: str
    sub_display_name: str


# ---- 情绪趋势（大V视角仪表盘）----

class SentimentTrendPoint(BaseModel):
    """情绪趋势中的单日数据点。

    up_ratio：上涨家数占有效参与统计标的的比例（%）。
    limit_up_ratio：涨停数占当日上涨家数的比例（%），反映涨停板热度。
    sentiment_score：综合情绪分（0~100），越高越乐观。
        计算方式 = 50 + (up_count - down_count) / (up_count + down_count + flat_count + 1) × 50
        再叠加涨停溢价：+ limit_up_count / (total + 1) × 20（最终 clip 到 0~100）
    """
    trade_date: str
    up_count: int
    down_count: int
    flat_count: int
    limit_up_count: int
    limit_down_count: int
    total: int
    up_ratio: float
    limit_up_ratio: float
    sentiment_score: float


class SentimentTrendOut(BaseModel):
    """GET /api/replay/sentiment-trend 的返回体。"""
    days: int
    points: list[SentimentTrendPoint]
    latest_date: Optional[str] = None
