from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BarDaily, Symbol
from app.schemas import BarPoint, Interval
from app.services.adj import AdjType, apply_adj, build_adj_map, get_latest_factor
from app.services.aggregation import DailyRow, aggregate_bars

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
