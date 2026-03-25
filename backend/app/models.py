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
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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
