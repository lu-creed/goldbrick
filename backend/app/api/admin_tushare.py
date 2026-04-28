"""后台管理接口（/api/admin）：当前主要是 Tushare 的 token。

同步任务页用来查看是否已配置、保存 token；具体实现在 app/services/runtime_tokens.py。
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_admin
from app.database import get_db
from app.models import Symbol
from app.services.indicator_precompute import rebuild_indicator_pre_for_symbol
from app.services.runtime_tokens import get_tushare_token_status, set_runtime_tushare_token

router = APIRouter(prefix="/admin", tags=["admin"])


class SetTokenReq(BaseModel):
    token: str


@router.get("/tushare/token-status")
def tushare_token_status(_admin=Depends(get_current_admin)):
    return get_tushare_token_status()


@router.post("/tushare/token")
def set_tushare_token(body: SetTokenReq, _admin=Depends(get_current_admin)):
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token 不能为空")

    try:
        set_runtime_tushare_token(token)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex

    # 按产品要求：保存阶段不调用外部接口校验；在真正同步任务开始前再校验。
    return {"ok": True, "validated": False}


class RebuildIndicatorPreReq(BaseModel):
    """触发指标预计算重建。

    ts_codes 留空 → 重建全市场。
    adj_modes 留空 → 默认 ["qfq", "hfq"]（0.0.4-dev 起 hfq 也走缓存，减少图表首屏延迟）。
    """
    ts_codes: Optional[List[str]] = None
    adj_modes: Optional[List[str]] = None


@router.post("/indicator-pre/rebuild")
def rebuild_indicator_pre(body: RebuildIndicatorPreReq, _admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    codes = [c.strip().upper() for c in (body.ts_codes or []) if c and str(c).strip()]
    adj_modes = [m.strip().lower() for m in (body.adj_modes or []) if m and str(m).strip()] or ["qfq", "hfq"]
    # 校验：只接受受支持的复权口径
    valid = {"qfq", "hfq"}
    invalid = [m for m in adj_modes if m not in valid]
    if invalid:
        raise HTTPException(status_code=400, detail=f"不支持的 adj_mode: {invalid}（可选 qfq/hfq）")
    if codes:
        syms = db.query(Symbol).filter(Symbol.ts_code.in_(codes)).all()
    else:
        syms = db.query(Symbol).order_by(Symbol.ts_code.asc()).all()
    total_rows = 0
    done = 0
    per_adj: dict[str, int] = {}
    for sym in syms:
        for mode in adj_modes:
            n = rebuild_indicator_pre_for_symbol(db, sym.id, mode)
            total_rows += n
            per_adj[mode] = per_adj.get(mode, 0) + n
        done += 1
    return {
        "symbols": done,
        "rows_written": total_rows,
        "adj_modes": adj_modes,
        "per_adj_rows": per_adj,
    }

