"""
数据库表模型定义（ORM）。

这里每个 class 对应数据库里的一张表。
SQLAlchemy 会根据这些定义自动建表、做 INSERT/SELECT/UPDATE。
字段类型后面括号里的数字是「整数位数」和「小数位数」，如 Numeric(18, 6)=最多18位、6位小数。
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Symbol(Base):
    """本地股票/指数池：用户关注并打算拉取日线的标的目录。

    ts_code 格式：'600000.SH'（沪市）或 '000001.SZ'（深市）。
    每只股票在 bars_daily 里有多条对应日线，通过 symbol_id 外键关联。
    """
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # 证券代码，如 600000.SH
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)      # 股票名称，如 浦发银行
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ORM 关联：通过 symbol.bars 可直接访问该股的全部日线列表
    bars: Mapped[List["BarDaily"]] = relationship(back_populates="symbol")


class BarDaily(Base):
    """日线行情：每行记录一只股票某一天的 OHLCV 及衍生字段。

    volume 单位：Tushare daily 接口返回的是「手」（100股=1手），注意与「股」的区别。
    amount 单位：元（Tushare 个股 daily 为元；指数 index_daily 原始为千元，入库时已 ×1000 统一）。
    consecutive_* 字段：由 derivatives.py 在同步后根据涨跌停规则重新计算写入，非 Tushare 直接返回。
    UniqueConstraint：同一只股票同一天只能有一条记录，重复同步会覆盖（upsert）而不是重复插入。
    """
    __tablename__ = "bars_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)  # 关联 symbols 表
    trade_date: Mapped[Date] = mapped_column(Date, index=True)           # 交易日
    open: Mapped[float] = mapped_column(Numeric(18, 6))                  # 开盘价
    high: Mapped[float] = mapped_column(Numeric(18, 6))                  # 最高价
    low: Mapped[float] = mapped_column(Numeric(18, 6))                   # 最低价
    close: Mapped[float] = mapped_column(Numeric(18, 6))                 # 收盘价
    volume: Mapped[int] = mapped_column(Integer)                         # 成交量（手），Tushare daily 返回手
    amount: Mapped[float] = mapped_column(Numeric(20, 4))                # 成交额（元）
    turnover_rate: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)  # 换手率%
    consecutive_limit_up_days: Mapped[int] = mapped_column(Integer, default=0)    # 连续涨停天数
    consecutive_limit_down_days: Mapped[int] = mapped_column(Integer, default=0)  # 连续跌停天数
    consecutive_up_days: Mapped[int] = mapped_column(Integer, default=0)          # 连续上涨天数（未必涨停）
    consecutive_down_days: Mapped[int] = mapped_column(Integer, default=0)        # 连续下跌天数
    source: Mapped[str] = mapped_column(String(32), default="tushare")            # 数据来源
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    symbol: Mapped["Symbol"] = relationship(back_populates="bars")

    __table_args__ = (UniqueConstraint("symbol_id", "trade_date", name="uq_symbol_trade_date"),)


class AdjFactorDaily(Base):
    """每日复权因子：用于将历史价格调整为前复权/后复权价格。

    复权原理：
    - 后复权价格 = 原始价格 × adj_factor（历史收益率不变，适合长期走势比较）
    - 前复权价格 = 原始价格 × adj_factor / 最新adj_factor（以当前价格为基准，适合看历史形态）
    - 每次分红、送股、配股后，Tushare 会更新历史各日的 adj_factor
    """
    __tablename__ = "adj_factors_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    adj_factor: Mapped[float] = mapped_column(Numeric(18, 8))  # 复权因子（≥1.0，分红后会重新计算历史值）
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("symbol_id", "trade_date", name="uq_symbol_adj_trade_date"),)


class SyncJob(Base):
    """定时同步任务配置：全局只有一条记录，控制「每天几点自动同步」。

    cron_expr 是 5 域 cron 表达式（分 时 日 月 周），如 "0 18 * * *"=每天18点整。
    enabled=False 时定时器不启动，但手动同步不受影响。
    last_* 字段由同步完成后自动更新，方便前端展示上次同步状态。
    """
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cron_expr: Mapped[str] = mapped_column(String(64), default="0 18 * * *")        # 默认每天18点
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)                     # 是否启用定时任务
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True) # 上次运行时间
    last_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)    # 上次状态：success/failed/cancelled
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)           # 上次失败原因
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncRun(Base):
    """同步运行记录：每次同步（手动或定时）创建一条，前端可查看进度与日志。

    协作式暂停/取消机制：
    - 前端调用 PATCH /api/sync/runs/{id}/pause 设置 pause_requested=True
    - 工作线程在每只股票开始拉取前读取此标志，进入 paused 状态并等待
    - resume 后工作线程恢复；cancel 后工作线程退出并将 status 置为 cancelled
    - 这种方式无法打断「正在进行中的单只股票请求」，只在两只股票之间检查
    """
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32))    # schedule（定时触发）或 manual（手动触发）
    status: Mapped[str] = mapped_column(String(32))     # queued / running / paused / success / failed / cancelled
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # 进度摘要，如 "progress 100/5000 [2%]"
    log_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)  # 日志文件路径（绝对路径）
    # 协作式控制：工作线程在「每只标的开始处理前」读库轮询；无法打断单标的内部的 Tushare 请求。
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)     # 前端请求暂停
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)    # 前端请求取消


class AppSetting(Base):
    """应用全局设置：键值对形式存储任意配置项（如 Tushare token、上次同步日期）。

    key 唯一，通过 db.query(AppSetting).filter(AppSetting.key == 'xxx') 读写。
    """
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)     # 配置键，如 tushare_token
    value: Mapped[str] = mapped_column(Text, default="")               # 配置值（均以字符串存储）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InstrumentMeta(Base):
    """证券元数据：包含个股和指数的基本信息，不含行情数据。

    个股来源：Tushare stock_basic（含 market/exchange/list_date）。
    指数来源：用户在数据后台「指数」页签从 Tushare index_basic 手动勾选加入。
    asset_type 区分：'stock' 个股，'index' 指数。
    市场分类（market）：Tushare 的 market 字段，如「主板」「创业板」「科创板」等。
    交易所（exchange）：SSE=上交所，SZSE=深交所，BSE=北交所。
    """
    __tablename__ = "instrument_meta"

    ts_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(16), default="stock")   # stock | index
    list_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True) # 上市日期
    # 个股：Tushare stock_basic 的 market（主板/创业板/科创板等）、exchange（SSE/SZSE/BSE）
    market: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Indicator(Base):
    """内置指标库：MA / KDJ / BOLL / MACD / EXPMA 等系统预置指标。

    这些指标只读（前端指标库「内置指标」页签），由 indicator_seed.py 初始化写入。
    每个指标有多个参数（IndicatorParam）和多条输出子线（IndicatorSubIndicator）。
    """
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)        # 英文标识，如 MA
    display_name: Mapped[str] = mapped_column(String(64))             # 展示名，如 移动平均线
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    params: Mapped[List["IndicatorParam"]] = relationship(back_populates="indicator", cascade="all, delete-orphan")
    sub_indicators: Mapped[List["IndicatorSubIndicator"]] = relationship(back_populates="indicator", cascade="all, delete-orphan")


class IndicatorParam(Base):
    """内置指标的可调参数，如 BOLL 的 N（周期）和 sigma（标准差倍数）。

    default_value 是字符串形式的默认值，前端展示时原样显示。
    目前内置指标参数不可由用户修改，仅展示说明。
    """
    __tablename__ = "indicator_params"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), index=True)
    name: Mapped[str] = mapped_column(String(32))                          # 参数名，如 N
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # 参数说明，如 计算周期
    default_value: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # 默认值字符串

    indicator: Mapped["Indicator"] = relationship(back_populates="params")


class IndicatorSubIndicator(Base):
    """内置指标的输出子线，如 BOLL 的上轨(UPPER)/中轨(MID)/下轨(LOWER)。

    can_be_price=True 的子线可以作为买入/卖出的价格基准（如 BOLL 上轨/下轨）。
    can_be_price=False 的子线不应用于价格比较（如 KDJ 的 K/D/J 值域是 0~100，不是价格）。
    """
    __tablename__ = "indicator_sub_indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))                          # 子线名，如 UPPER
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 是否可作为买入/卖出价格基准（False=不能，如成交量、KDJ K/D/J、MACD柱等）
    can_be_price: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    indicator: Mapped["Indicator"] = relationship(back_populates="sub_indicators")


class IndicatorPreDaily(Base):
    """日线指标预计算缓存：将 MA/KDJ/BOLL/MACD 等内置指标结果序列化存入 JSON，加速查询。

    adj_mode 目前仅写入 'qfq'（前复权）和 'none'（不复权），与全市场回测口径一致。
    payload 是 JSON 字典，键为子指标名（如 'MA5'、'K'），值为当日数值。
    同步时由 indicator_precompute.py 在每只股票拉取完成后自动重建。
    """
    __tablename__ = "indicator_pre_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    adj_mode: Mapped[str] = mapped_column(String(8), default="none")    # none | qfq | hfq
    payload: Mapped[str] = mapped_column(Text, default="{}")            # JSON 字典，键为子指标名

    __table_args__ = (
        UniqueConstraint("symbol_id", "trade_date", "adj_mode", name="uq_indicator_pre_symbol_date_adj"),
    )


class UserIndicator(Base):
    """用户自定义指标：支持两种形式。

    形式一（DSL，推荐）：definition_json 不为空，存储多参数、多子线、公式树的完整配置。
    形式二（旧版 legacy）：expr 不为空，单行 Python 风格四则运算表达式（功能受限）。
    保存时必须对一只股票试算通过才能入库，确保公式无误。
    code 是英文标识（创建后不可更改），display_name 是前端展示名。
    """
    __tablename__ = "user_indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # 唯一英文标识，如 my_ma_diff
    display_name: Mapped[str] = mapped_column(String(128))                   # 展示名，如 MA差值
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 旧版：单条四则表达式。新版可为空串，完整定义见 definition_json。
    expr: Mapped[str] = mapped_column(Text, default="")
    definition_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # DSL JSON 字符串
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DavStockWatch(Base):
    """大V看板：用户标注的 ABCD 分类股票，手动维护派息率与 EPS 供预期股息率计算。

    dav_class: A/B/C/D（Mr. Dang 分类框架）
    manual_payout_ratio: 手动填写的近两年平均派息率（%，如 33.95 表示 33.95%）
    manual_eps: 手动填写的预测全年 EPS（元）
    notes: 纠正依据（行业基准、大股东诉求、公告纠正等自由文本）
    积分升级后，payout_ratio 和 eps 将从 Tushare fina_indicator+dividend 自动填充，
    manual 字段作为人工覆盖优先级高于自动值。
    """
    __tablename__ = "dav_stock_watch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    dav_class: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)   # A/B/C/D
    manual_payout_ratio: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    manual_eps: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
