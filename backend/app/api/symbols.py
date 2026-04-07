"""股票池接口（/api/symbols）。

管理「本地要跟踪哪些 ts_code」。多页面会拉列表选股票。
数据表：symbols。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Symbol
from app.schemas import SymbolCreate, SymbolOut, SymbolPatch
from app.services.ingestion import ensure_symbols_for_stock_meta

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("", response_model=list[SymbolOut])
def list_symbols(db: Session = Depends(get_db)):
    # 曾只同步到 instrument_meta 未写 symbols，导致下拉全空；读列表时按需补齐（无缺口则一次反查即返回）
    ensure_symbols_for_stock_meta(db)
    return db.query(Symbol).order_by(Symbol.ts_code.asc()).all()


@router.post("", response_model=SymbolOut)
def create_symbol(body: SymbolCreate, db: Session = Depends(get_db)):
    exists = db.query(Symbol).filter(Symbol.ts_code == body.ts_code.strip()).one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="ts_code already exists")
    sym = Symbol(ts_code=body.ts_code.strip().upper(), name=body.name)
    db.add(sym)
    db.commit()
    db.refresh(sym)
    return sym


@router.patch("/{symbol_id}", response_model=SymbolOut)
def patch_symbol(symbol_id: int, body: SymbolPatch, db: Session = Depends(get_db)):
    sym = db.query(Symbol).filter(Symbol.id == symbol_id).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="symbol not found")
    if body.name is not None:
        sym.name = body.name
    db.commit()
    db.refresh(sym)
    return sym
