from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Symbol
from app.schemas import SymbolCreate, SymbolOut, SymbolPatch

router = APIRouter(prefix="/symbols", tags=["symbols"])


@router.get("", response_model=list[SymbolOut])
def list_symbols(enabled: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(Symbol)
    if enabled is not None:
        q = q.filter(Symbol.enabled.is_(enabled))
    return q.order_by(Symbol.ts_code.asc()).all()


@router.post("", response_model=SymbolOut)
def create_symbol(body: SymbolCreate, db: Session = Depends(get_db)):
    exists = db.query(Symbol).filter(Symbol.ts_code == body.ts_code.strip()).one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="ts_code already exists")
    sym = Symbol(ts_code=body.ts_code.strip().upper(), name=body.name, enabled=True)
    db.add(sym)
    db.commit()
    db.refresh(sym)
    return sym


@router.patch("/{symbol_id}", response_model=SymbolOut)
def patch_symbol(symbol_id: int, body: SymbolPatch, db: Session = Depends(get_db)):
    sym = db.query(Symbol).filter(Symbol.id == symbol_id).one_or_none()
    if not sym:
        raise HTTPException(status_code=404, detail="symbol not found")
    if body.enabled is not None:
        sym.enabled = body.enabled
    if body.name is not None:
        sym.name = body.name
    db.commit()
    db.refresh(sym)
    return sym
