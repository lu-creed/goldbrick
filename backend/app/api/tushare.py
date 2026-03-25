from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import TushareSymbolOut
from app.services.ingestion import fetch_all_a_stock_list

router = APIRouter(prefix="/tushare", tags=["tushare"])


@router.get("/symbols", response_model=list[TushareSymbolOut])
def get_all_a_symbols(db: Session = Depends(get_db)):
    try:
        return fetch_all_a_stock_list(db)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex

