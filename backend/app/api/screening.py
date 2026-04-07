"""条件选股：基于已保存的自定义指标（与指标库 DSL / expr 同一套求值）在指定交易日筛选个股。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ScreeningRunIn, ScreeningRunOut, ScreeningStockRow
from app.services.screening_runner import run_screen

router = APIRouter(prefix="/screening", tags=["screening"])


@router.post("/run", response_model=ScreeningRunOut)
def screening_run(body: ScreeningRunIn, db: Session = Depends(get_db)):
    try:
        raw = run_screen(
            db,
            trade_date=body.trade_date,
            user_indicator_id=body.user_indicator_id,
            sub_key=body.sub_key or "",
            compare_op=body.compare_op,
            threshold=body.threshold,
            max_scan=body.max_scan,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    items = [ScreeningStockRow(**x) for x in raw["items"]]
    return ScreeningRunOut(
        trade_date=raw["trade_date"],
        user_indicator_id=raw.get("user_indicator_id"),
        sub_key=raw.get("sub_key"),
        compare_op=raw.get("compare_op"),
        threshold=raw.get("threshold"),
        scanned=raw["scanned"],
        matched=raw["matched"],
        note=raw.get("note"),
        items=items,
    )
