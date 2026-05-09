"""个股财务快照接口（路径前缀 /api/fundamentals/）。

提供单只股票的财务快照数据，用于 K 线页面下方的财务信息面板：
- 公司基本信息（名称/市场/上市日期）
- 最新 PE/PB/总市值/流通市值（来自 fundamental_daily）
- PE/PB 近 60 个交易日历史序列（用于趋势折线图）
- 最新价格与换手率（来自 bars_daily）
- 若用户已登录且该股票在大V看板中，附加派息率/EPS/预期股息率/分类
- 年度财务指标（ROE/毛利率/资产负债率/营收/净利润），按需从 AKShare 实时拉取
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import get_current_user_optional
from app.database import get_db

router = APIRouter(prefix="/fundamentals", tags=["fundamentals"])

# 年度财务指标内存缓存（TTL 1 小时）：key = ts_code, value = (timestamp, data)
_FI_CACHE: Dict[str, Tuple[float, list]] = {}
_FI_CACHE_TTL = 3600.0


class PEPBPoint(BaseModel):
    date: str
    value: Optional[float]


class FundamentalSnapshot(BaseModel):
    ts_code: str
    name: Optional[str]
    market: Optional[str]
    exchange: Optional[str]
    list_date: Optional[str]
    asset_type: Optional[str]
    # 最新估值
    pe_ttm: Optional[float]
    pb: Optional[float]
    total_mv: Optional[float]
    circ_mv: Optional[float]
    fundamental_date: Optional[str]
    # 最新行情
    latest_close: Optional[float]
    latest_turnover_rate: Optional[float]
    # 大V看板数据（仅登录且在看板中的股票）
    dav_payout_ratio: Optional[float]
    dav_eps: Optional[float]
    dav_class: Optional[str]
    expected_yield: Optional[float]
    # 历史序列（升序，用于趋势图）
    pe_history: List[PEPBPoint]
    pb_history: List[PEPBPoint]


@router.get("/snapshot", response_model=FundamentalSnapshot)
def get_fundamental_snapshot(
    ts_code: str = Query(...),
    user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """获取个股财务快照，包含最新 PE/PB/市值、PE/PB 历史趋势，以及大V看板数据（若登录）。"""
    ts_code = ts_code.strip().upper()

    # 公司基本信息
    meta_row = db.execute(
        text("SELECT name, market, exchange, list_date, asset_type FROM instrument_meta WHERE ts_code = :tc"),
        {"tc": ts_code},
    ).fetchone()

    # PE/PB 近 60 条历史（DESC 取最新，后续升序排列供图表使用）
    fd_rows = db.execute(
        text("""
            SELECT trade_date,
                   CAST(pe_ttm  AS REAL) AS pe_ttm,
                   CAST(pb      AS REAL) AS pb,
                   CAST(total_mv AS REAL) AS total_mv,
                   CAST(circ_mv  AS REAL) AS circ_mv
            FROM fundamental_daily
            WHERE ts_code = :tc
            ORDER BY trade_date DESC
            LIMIT 60
        """),
        {"tc": ts_code},
    ).fetchall()

    # 最新日线行情
    bar_row = db.execute(
        text("""
            SELECT CAST(b.close AS REAL) AS close,
                   CAST(b.turnover_rate AS REAL) AS turnover_rate
            FROM bars_daily b
            JOIN symbols s ON s.id = b.symbol_id
            WHERE s.ts_code = :tc
            ORDER BY b.trade_date DESC
            LIMIT 1
        """),
        {"tc": ts_code},
    ).fetchone()

    # 大V看板数据（仅登录用户）
    dav_row = None
    if user is not None:
        dav_row = db.execute(
            text("""
                SELECT manual_payout_ratio, auto_payout_ratio,
                       manual_eps, auto_eps, dav_class
                FROM dav_stock_watch
                WHERE user_id = :uid AND ts_code = :tc
            """),
            {"uid": user.id, "tc": ts_code},
        ).fetchone()

    # 历史序列：升序排列
    fd_sorted = sorted(fd_rows, key=lambda r: str(r.trade_date))
    pe_history = [
        PEPBPoint(date=str(r.trade_date), value=float(r.pe_ttm) if r.pe_ttm is not None else None)
        for r in fd_sorted
    ]
    pb_history = [
        PEPBPoint(date=str(r.trade_date), value=float(r.pb) if r.pb is not None else None)
        for r in fd_sorted
    ]

    # 最新快照取第一条（DESC 排序）
    latest_fd = fd_rows[0] if fd_rows else None

    # 大V数据计算
    dav_payout = dav_eps = dav_class = expected_yield = None
    if dav_row:
        raw_payout = dav_row.manual_payout_ratio or dav_row.auto_payout_ratio
        raw_eps = dav_row.manual_eps or dav_row.auto_eps
        dav_payout = float(raw_payout) if raw_payout else None
        dav_eps = float(raw_eps) if raw_eps else None
        dav_class = dav_row.dav_class
        if dav_payout and dav_eps and bar_row and bar_row.close and float(bar_row.close) > 0:
            expected_yield = round(dav_payout / 100.0 * dav_eps / float(bar_row.close) * 100.0, 2)

    return FundamentalSnapshot(
        ts_code=ts_code,
        name=meta_row.name if meta_row else None,
        market=meta_row.market if meta_row else None,
        exchange=meta_row.exchange if meta_row else None,
        list_date=str(meta_row.list_date) if meta_row and meta_row.list_date else None,
        asset_type=meta_row.asset_type if meta_row else None,
        pe_ttm=float(latest_fd.pe_ttm) if latest_fd and latest_fd.pe_ttm is not None else None,
        pb=float(latest_fd.pb) if latest_fd and latest_fd.pb is not None else None,
        total_mv=float(latest_fd.total_mv) if latest_fd and latest_fd.total_mv is not None else None,
        circ_mv=float(latest_fd.circ_mv) if latest_fd and latest_fd.circ_mv is not None else None,
        fundamental_date=str(latest_fd.trade_date) if latest_fd else None,
        latest_close=float(bar_row.close) if bar_row and bar_row.close is not None else None,
        latest_turnover_rate=float(bar_row.turnover_rate) if bar_row and bar_row.turnover_rate is not None else None,
        dav_payout_ratio=dav_payout,
        dav_eps=dav_eps,
        dav_class=dav_class,
        expected_yield=expected_yield,
        pe_history=pe_history,
        pb_history=pb_history,
    )


class FinancialIndicatorRow(BaseModel):
    period: str               # 年份，如 "2023"
    roe: Optional[float]      # 净资产收益率（%）
    gross_margin: Optional[float]  # 销售毛利率（%）
    debt_ratio: Optional[float]    # 资产负债率（%）
    revenue: Optional[float]       # 营业收入（元）
    net_profit: Optional[float]    # 净利润（元）


@router.get("/financial-indicators", response_model=List[FinancialIndicatorRow])
def get_financial_indicators(
    ts_code: str = Query(...),
    _user=Depends(get_current_user_optional),
):
    """从 AKShare 实时拉取个股近 5 年年度财务指标（ROE/毛利率/资产负债率/营收/净利润）。

    结果缓存 1 小时，避免频繁调用外部接口。
    AKShare 拉取失败时返回空列表（前端静默处理）。
    """
    ts_code = ts_code.strip().upper()

    now = time.time()
    cached = _FI_CACHE.get(ts_code)
    if cached and now - cached[0] < _FI_CACHE_TTL:
        return cached[1]

    from app.services.akshare_fundamentals import fetch_financial_analysis_indicator
    rows = fetch_financial_analysis_indicator(ts_code)

    result = [FinancialIndicatorRow(**r) for r in rows]
    _FI_CACHE[ts_code] = (now, result)
    return result
