"""K 线接口（路径前缀 /api/bars/）。

提供两个接口：

1. GET /api/bars — 获取 K 线数据
   - 查询单只股票（或指数）的 K 线序列
   - 支持复权（none/qfq/hfq）和周期聚合（日/周/月/季/年）
   - 日线从 bars_daily 读取原始数据，复权在内存中计算，周期聚合调用 aggregation.py
   - 对应前端 K 线页的 fetchBars 调用

2. GET /api/bars/custom-indicator-series — 自定义指标子线序列
   - 在 K 线图的副图中叠加自定义指标
   - 计算结果与当前选中的复权方式对齐（同一复权下的价格序列）
   - 只支持日线（指标库的子线是按日计算的）
"""

from __future__ import annotations
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import get_current_user, get_current_user_optional
from app.models import Symbol
from app.schemas import BarPoint, CustomIndicatorPoint, CustomIndicatorSeriesOut, Interval
from app.services.adj import AdjType, build_adj_map, get_latest_factor
from app.services.aggregation import DailyRow, aggregate_bars
from app.services.user_indicator_compute import custom_indicator_daily_points

router = APIRouter(prefix="/bars", tags=["bars"])


@router.get("", response_model=List[BarPoint])
def get_bars(
    ts_code: str = Query(...),
    interval: Interval = Query("1d"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    adj: AdjType = Query("none"),
    _user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """获取 K 线数据。

    Args:
        ts_code: 股票代码，如 '600000.SH'（上交所）或 '000001.SZ'（深交所）。
        interval: 周期：'1d'=日 / '1w'=周 / '1M'=月 / '1Q'=季 / '1y'=年。
                  非日线时，日线会被聚合（OHLC取边值，成交量/额求和，时间取周期最后一天）。
        start: 返回数据的起始日期（含）；None 则从最早有数据的日期开始。
        end: 返回数据的结束日期（含）；None 则到最新日期。
        adj: 复权类型：
             'none'=不复权（原始价格）
             'qfq'=前复权（以今天价格为基准，适合形态分析）
             'hfq'=后复权（以最早价格为基准，适合收益率比较）

    Returns:
        BarPoint 列表，按日期升序，每个元素含 time/open/high/low/close/volume 等字段。
        time 字段：日线为当日日期，周/月/季/年线为该周期最后一个交易日的日期。
    """
    sym = db.query(Symbol).filter(Symbol.ts_code == ts_code.strip().upper()).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="unknown ts_code")

    # 只有需要复权时才加载复权因子（无复权时跳过，节省数据库 I/O）
    adj_map: dict[date, float] = {}
    latest_factor = 1.0
    if adj != "none":
        adj_map = build_adj_map(db, sym.id)
        latest_factor = get_latest_factor(adj_map)

    # 用 raw SQL 替代 ORM，避免为每行实例化完整 Python 对象
    conditions = ["symbol_id = :sid"]
    params: dict = {"sid": sym.id}
    if start:
        conditions.append("trade_date >= :start")
        params["start"] = start
    if end:
        conditions.append("trade_date <= :end")
        params["end"] = end
    where_clause = " AND ".join(conditions)
    rows_db = db.execute(
        text(f"""
            SELECT trade_date,
                   CAST(open  AS REAL) AS open,
                   CAST(high  AS REAL) AS high,
                   CAST(low   AS REAL) AS low,
                   CAST(close AS REAL) AS close,
                   CAST(volume AS REAL) AS volume,
                   CAST(amount AS REAL) AS amount,
                   turnover_rate,
                   consecutive_limit_up_days,
                   consecutive_limit_down_days,
                   consecutive_up_days,
                   consecutive_down_days
            FROM bars_daily
            WHERE {where_clause}
            ORDER BY trade_date ASC
        """),
        params,
    ).fetchall()

    # 复权系数每行算一次（而非对 OHLC 分别调 apply_adj），减少 dict 查找次数
    rows = []
    for b in rows_db:
        # raw SQL 下 SQLite 日期列返回字符串，需转为 date 对象才能命中 adj_map
        td_raw = b.trade_date
        td: date = td_raw if isinstance(td_raw, date) else date.fromisoformat(str(td_raw))
        if adj == "none" or not adj_map:
            factor = 1.0
        else:
            af = adj_map.get(td, 1.0)
            factor = af if adj == "hfq" else af / latest_factor
        rows.append(
            DailyRow(
                trade_date=td,
                open=round(float(b.open) * factor, 4),
                high=round(float(b.high) * factor, 4),
                low=round(float(b.low) * factor, 4),
                close=round(float(b.close) * factor, 4),
                volume=float(b.volume),
                amount=float(b.amount),
                turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
                consecutive_limit_up_days=int(b.consecutive_limit_up_days),
                consecutive_limit_down_days=int(b.consecutive_limit_down_days),
                consecutive_up_days=int(b.consecutive_up_days),
                consecutive_down_days=int(b.consecutive_down_days),
            )
        )
    # aggregate_bars 处理周期聚合：日线 interval='1d' 时直接返回，否则按自然日历分组压缩
    return aggregate_bars(rows, interval)


@router.get("/custom-indicator-series", response_model=CustomIndicatorSeriesOut)
def get_custom_indicator_series(
    ts_code: str = Query(...),
    user_indicator_id: int = Query(..., ge=1),
    sub_key: str = Query(..., min_length=1, max_length=64),
    adj: AdjType = Query("none"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    _user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取自定义指标的某条子线的日线序列，用于 K 线图副图叠加展示。

    Args:
        ts_code: 股票代码。
        user_indicator_id: 要计算的自定义指标 ID（来自 /api/indicators/custom）。
        sub_key: 子线的 key（如 'line1'、'signal'），必须是该指标已定义的子线。
        adj: 复权类型（与当前 K 线主图的复权方式保持一致，否则价格基准不同会错位）。
        start/end: 返回数据的日期范围（None 则返回全量）。

    Returns:
        CustomIndicatorSeriesOut，含 ts_code/display_name/points（{time, value}列表）。
    """
    raw = custom_indicator_daily_points(
        db,
        ts_code=ts_code,
        user_indicator_id=user_indicator_id,
        sub_key=sub_key.strip(),
        adj=adj,
        start=start,
        end=end,
    )
    if not raw.get("ok"):
        raise HTTPException(status_code=400, detail=raw.get("message") or "计算失败")
    return CustomIndicatorSeriesOut(
        ts_code=ts_code.strip().upper(),
        user_indicator_id=user_indicator_id,
        sub_key=sub_key.strip(),
        display_name=str(raw.get("display_name") or ""),
        points=[CustomIndicatorPoint(**p) for p in raw["points"]],
    )
