"""Tushare 相关只读接口（/api/tushare）。

例如拉全 A 股列表，给同步页勾选；真正请求外部 API 在 app/services/ingestion.py。
"""

from __future__ import annotations
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.database import get_db
from app.schemas import TushareSymbolOut
from app.services.ingestion import fetch_all_a_stock_list

router = APIRouter(prefix="/tushare", tags=["tushare"])


@router.get("/symbols", response_model=List[TushareSymbolOut])
def get_all_a_symbols(_admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    try:
        return fetch_all_a_stock_list(db)
    except HTTPException:
        raise
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(ex)) from ex

