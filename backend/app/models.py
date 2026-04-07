from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Symbol(Base):
    __tablename__ = "symbols"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bars: Mapped[List["BarDaily"]] = relationship(back_populates="symbol")


class BarDaily(Base):
    __tablename__ = "bars_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Numeric(18, 6))
    high: Mapped[float] = mapped_column(Numeric(18, 6))
    low: Mapped[float] = mapped_column(Numeric(18, 6))
    close: Mapped[float] = mapped_column(Numeric(18, 6))
    volume: Mapped[int] = mapped_column(Integer)  # 股 / 手依数据源，Tushare daily 为手需注意
    amount: Mapped[float] = mapped_column(Numeric(20, 4))
    turnover_rate: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)
    consecutive_limit_up_days: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_limit_down_days: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_up_days: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_down_days: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    symbol: Mapped["Symbol"] = relationship(back_populates="bars")

    __table_args__ = (UniqueConstraint("symbol_id", "trade_date", name="uq_symbol_trade_date"),)


class AdjFactorDaily(Base):
    __tablename__ = "adj_factors_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    adj_factor: Mapped[float] = mapped_column(Numeric(18, 8))
    source: Mapped[str] = mapped_column(String(32), default="tushare")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("symbol_id", "trade_date", name="uq_symbol_adj_trade_date"),)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cron_expr: Mapped[str] = mapped_column(String(64), default="0 18 * * *")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32))  # schedule | manual
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # 协作式控制：工作线程在「每只标的开始处理前」读库轮询；无法打断单标的内部的 Tushare 请求。
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InstrumentMeta(Base):
    __tablename__ = "instrument_meta"

    ts_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(16), default="stock")  # stock | index
    list_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True)
    # 个股：Tushare stock_basic 的 market（主板/创业板/科创板等）、exchange（SSE/SZSE/BSE）
    market: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Indicator(Base):
    """指标库：MA / KDJ / BOLL / MACD / EXPMA / 个股数据。"""
    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)        # 英文标识，如 MA
    display_name: Mapped[str] = mapped_column(String(64))             # 展示名，如 移动平均线
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    params: Mapped[List["IndicatorParam"]] = relationship(back_populates="indicator", cascade="all, delete-orphan")
    sub_indicators: Mapped[List["IndicatorSubIndicator"]] = relationship(back_populates="indicator", cascade="all, delete-orphan")


class IndicatorParam(Base):
    """指标参数，如 BOLL 的 N 和 sigma。"""
    __tablename__ = "indicator_params"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_value: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    indicator: Mapped["Indicator"] = relationship(back_populates="params")


class IndicatorSubIndicator(Base):
    """指标子线，如 BOLL 的上轨/中轨/下轨。"""
    __tablename__ = "indicator_sub_indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(ForeignKey("indicators.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 是否可作为买入/卖出价格基准（False=不能，如成交量、KDJ K/D/J、MACD柱等）
    can_be_price: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    indicator: Mapped["Indicator"] = relationship(back_populates="sub_indicators")

class IndicatorPreDaily(Base):
    """日线指标预计算；阶段一仅写入 adj_mode=qfq（前复权），与全市场回测口径一致。"""
    __tablename__ = "indicator_pre_daily"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("symbols.id"), index=True)
    trade_date: Mapped[Date] = mapped_column(Date, index=True)
    adj_mode: Mapped[str] = mapped_column(String(8), default="none")
    payload: Mapped[str] = mapped_column(Text, default="{}")  # JSON 字典，键为子指标名

    __table_args__ = (
        UniqueConstraint("symbol_id", "trade_date", "adj_mode", name="uq_indicator_pre_symbol_date_adj"),
    )


class UserIndicator(Base):
    """用户自定义指标：PRD DSL（definition_json）或与旧版兼容的单条 expr。"""

    __tablename__ = "user_indicators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 旧版：单条四则表达式。新版可为空串，完整定义见 definition_json。
    expr: Mapped[str] = mapped_column(Text, default="")
    definition_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


