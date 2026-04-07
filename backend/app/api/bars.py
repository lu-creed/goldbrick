"""K 线接口（路径前缀 /bars，完整为 /api/bars）。

从数据库读出日线，按需复权，再聚合成周/月等周期。计算在 app/services/aggregation.py、adj.py。
对应前端：K 线页调用 fetchBars。
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BarDaily, Symbol
from app.schemas import BarPoint, CustomIndicatorPoint, CustomIndicatorSeriesOut, Interval
from app.services.adj import AdjType, apply_adj, build_adj_map, get_latest_factor
from app.services.aggregation import DailyRow, aggregate_bars
from app.services.user_indicator_compute import custom_indicator_daily_points

router = APIRouter(prefix="/bars", tags=["bars"])


@router.get("", response_model=list[BarPoint])
def get_bars(
    ts_code: str = Query(...),
    interval: Interval = Query("1d"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    adj: AdjType = Query("none"),
    db: Session = Depends(get_db),
):
    sym = db.query(Symbol).filter(Symbol.ts_code == ts_code.strip().upper()).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="unknown ts_code")

    # 加载复权因子（adj != none 时才查，减少无用 IO）
    adj_map: dict[date, float] = {}
    latest_factor = 1.0
    if adj != "none":
        adj_map = build_adj_map(db, sym.id)
        latest_factor = get_latest_factor(adj_map)

    q = db.query(BarDaily).filter(BarDaily.symbol_id == sym.id).order_by(BarDaily.trade_date.asc())
    if start:
        q = q.filter(BarDaily.trade_date >= start)
    if end:
        q = q.filter(BarDaily.trade_date <= end)
    rows_db = q.all()

    rows = [
        DailyRow(
            trade_date=b.trade_date,
            # 仅对价格字段做复权，成交量/金额/换手率不变
            open=apply_adj(float(b.open), b.trade_date, adj, adj_map, latest_factor),
            high=apply_adj(float(b.high), b.trade_date, adj, adj_map, latest_factor),
            low=apply_adj(float(b.low), b.trade_date, adj, adj_map, latest_factor),
            close=apply_adj(float(b.close), b.trade_date, adj, adj_map, latest_factor),
            volume=float(b.volume),
            amount=float(b.amount),
            turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
            consecutive_limit_up_days=int(b.consecutive_limit_up_days),
            consecutive_limit_down_days=int(b.consecutive_limit_down_days),
            consecutive_up_days=int(b.consecutive_up_days),
            consecutive_down_days=int(b.consecutive_down_days),
        )
        for b in rows_db
    ]
    return aggregate_bars(rows, interval)


@router.get("/custom-indicator-series", response_model=CustomIndicatorSeriesOut)
def get_custom_indicator_series(
    ts_code: str = Query(...),
    user_indicator_id: int = Query(..., ge=1),
    sub_key: str = Query(..., min_length=1, max_length=64),
    adj: AdjType = Query("none"),
    start: Optional[date] = Query(None),
    end: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """自定义指标子线日线序列（与当前 K 线选择的复权方式对齐；前端请在日 K 下叠加）。"""
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
