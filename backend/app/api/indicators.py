from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Indicator
from app.services.indicator_seed import seed_indicators

router = APIRouter(prefix="/indicators", tags=["indicators"])


class ParamOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    default_value: Optional[str]
    model_config = {"from_attributes": True}


class SubIndicatorOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    can_be_price: bool = False
    model_config = {"from_attributes": True}


class IndicatorListItem(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    params_count: int
    sub_count: int


class IndicatorDetail(BaseModel):
    id: int
    name: str
    display_name: str
    description: Optional[str]
    params: List[ParamOut]
    sub_indicators: List[SubIndicatorOut]


@router.get("", response_model=list[IndicatorListItem])
def list_indicators(db: Session = Depends(get_db)):
    rows = db.query(Indicator).order_by(Indicator.id.asc()).all()
    return [
        IndicatorListItem(
            id=r.id,
            name=r.name,
            display_name=r.display_name,
            description=r.description,
            params_count=len(r.params),
            sub_count=len(r.sub_indicators),
        )
        for r in rows
    ]


@router.post("/seed")
def seed(force: bool = False, db: Session = Depends(get_db)):
    """手动初始化指标库种子数据。force=true 时强制覆盖已有数据（用于修复子指标）。"""
    seed_indicators(db, force=force)
    count = db.query(Indicator).count()
    return {"message": f"指标库初始化完成，当前共 {count} 条指标"}


@router.get("/{indicator_id}", response_model=IndicatorDetail)
def get_indicator(indicator_id: int, db: Session = Depends(get_db)):
    row = db.query(Indicator).filter(Indicator.id == indicator_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="indicator not found")
    return IndicatorDetail(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        description=row.description,
        params=[ParamOut.model_validate(p) for p in row.params],
        sub_indicators=[SubIndicatorOut.model_validate(s) for s in row.sub_indicators],
    )
