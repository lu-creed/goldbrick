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

from app.auth import get_current_admin, get_current_user
from app.database import get_db
from app.models import Indicator
from app.services.indicator_pedia import get_indicator_pedia, list_indicator_pedia
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


# ── 指标人话百科(Phase 1:易用性迭代)───────────────────────────
# Pedia 数据来自 services/indicator_pedia.py 的纯 Python 字典,
# 不依赖数据库,改内容只需改字典不需要迁移。

class TypicalSignalOut(BaseModel):
    condition: str
    meaning: str
    caveat: Optional[str] = None


class IndicatorPediaOut(BaseModel):
    """指标人话百科:给前端展示用的「这个指标能帮我看出什么」。

    与 IndicatorDetail 互补:IndicatorDetail 偏机器视角(参数/子线的原始数据),
    IndicatorPediaOut 偏用户视角(怎么用、什么时候用、什么时候别用)。
    """
    code: str
    display_name: str
    one_liner: str                      # 一句话说这个指标干啥
    long_description: str               # 1-2 段详述
    typical_signals: List[TypicalSignalOut]  # 常见信号与含义
    good_for: List[str]                 # 适合的场景
    bad_for: List[str]                  # 不适合的场景(陷阱)
    common_pairs: List[str]             # 常见搭配指标
    sub_notes: dict[str, str]           # 每条子线的人话说明


@router.get("", response_model=List[IndicatorListItem])
def list_indicators(_user=Depends(get_current_user), db: Session = Depends(get_db)):
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
def seed(force: bool = False, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    """初始化指标库种子数据（首次部署时调用）。

    force=False（默认）：若已有数据则跳过（幂等）。
    force=True：强制重建所有指标数据（用于修复损坏的子指标数据）。

    Returns:
        {'message': '指标库初始化完成，当前共 N 条指标'}
    """
    seed_indicators(db, force=force)
    count = db.query(Indicator).count()
    return {"message": f"指标库初始化完成，当前共 {count} 条指标"}


# 人话百科端点必须放在 /{indicator_id} 之前声明,否则 "/pedia/..." 会被误认为 indicator_id=pedia
@router.get("/pedia", response_model=List[IndicatorPediaOut])
def list_all_pedia(_user=Depends(get_current_user)):
    """获取所有内置指标的人话百科(用于指标百科页一次性列表展示)。"""
    return [IndicatorPediaOut(**p) for p in list_indicator_pedia()]


@router.get("/pedia/{code}", response_model=IndicatorPediaOut)
def get_pedia(code: str, _user=Depends(get_current_user)):
    """获取单个指标的人话百科。code 大小写不敏感,未收录返回 404。"""
    p = get_indicator_pedia(code)
    if p is None:
        raise HTTPException(status_code=404, detail=f"未收录指标 '{code}' 的人话百科")
    return IndicatorPediaOut(**p)


@router.get("/{indicator_id}", response_model=IndicatorDetail)
def get_indicator(indicator_id: int, _user=Depends(get_current_user), db: Session = Depends(get_db)):
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
