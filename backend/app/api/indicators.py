"""指标库 API（路径前缀 /api/indicators/）。

提供内置指标的查询和种子数据初始化接口。

内置指标是系统预置的标准技术指标（MA、MACD、KDJ、BOLL 等），
参数固定（如 MA5 永远是 5 日均线），不可由用户修改。
用户可以通过「自定义指标」引用内置指标的子线（见 /api/indicators/custom）。

接口说明：
- GET  /api/indicators       : 列表（每条指标的 ID、名称、参数数、子线数）
- GET  /api/indicators/{id}  : 详情（包含完整的参数列表和子线列表）
- POST /api/indicators/seed  : 初始化种子数据（首次部署时调用，force=true 强制重建）

数据来源：app/services/indicator_seed.py 的 seed_indicators 函数。
对应前端：指标库页（IndicatorLibPage）。
"""

from __future__ import annotations
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Indicator
from app.services.indicator_seed import seed_indicators

router = APIRouter(prefix="/indicators", tags=["indicators"])


class ParamOut(BaseModel):
    """指标参数的输出格式（用于指标详情页展示）。

    id: 参数在数据库中的 ID
    name: 参数名，如 'N'（均线周期）
    description: 参数说明，如 '均线计算周期（天数）'
    default_value: 默认值字符串，如 '20'
    """
    id: int
    name: str
    description: Optional[str]
    default_value: Optional[str]
    model_config = {"from_attributes": True}


class SubIndicatorOut(BaseModel):
    """子线输出格式（用于指标详情页展示）。

    id: 子线在数据库中的 ID
    name: 子线名，如 'DIF'/'DEA'/'MACD柱'（MACD 的三条线）
    description: 子线说明
    can_be_price: 此子线的值是否与价格同量级（True=图表中叠加在主图价格轴，False=放副图）
    """
    id: int
    name: str
    description: Optional[str]
    can_be_price: bool = False
    model_config = {"from_attributes": True}


class IndicatorListItem(BaseModel):
    """指标列表项的简要信息（用于指标库页的列表展示）。

    params_count: 该指标有多少个可配置参数
    sub_count: 该指标有多少条子线（如 MACD 有 3 条：DIF/DEA/MACD柱）
    """
    id: int
    name: str
    display_name: str
    description: Optional[str]
    params_count: int
    sub_count: int


class IndicatorDetail(BaseModel):
    """指标详情（用于指标详情弹窗，包含完整参数和子线信息）。"""
    id: int
    name: str
    display_name: str
    description: Optional[str]
    params: List[ParamOut]
    sub_indicators: List[SubIndicatorOut]


@router.get("", response_model=List[IndicatorListItem])
def list_indicators(db: Session = Depends(get_db)):
    """获取所有内置指标的列表（简要信息，不含参数和子线详情）。"""
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
    """初始化指标库种子数据（首次部署时调用）。

    force=False（默认）：若已有数据则跳过（幂等）。
    force=True：强制重建所有指标数据（用于修复损坏的子指标数据）。

    Returns:
        {'message': '指标库初始化完成，当前共 N 条指标'}
    """
    seed_indicators(db, force=force)
    count = db.query(Indicator).count()
    return {"message": f"指标库初始化完成，当前共 {count} 条指标"}


@router.get("/{indicator_id}", response_model=IndicatorDetail)
def get_indicator(indicator_id: int, db: Session = Depends(get_db)):
    """获取单个内置指标的完整详情（含参数列表和子线列表）。

    用于指标库页点击某个指标后弹出详情卡片展示。
    """
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
