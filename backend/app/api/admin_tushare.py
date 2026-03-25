from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.runtime_tokens import get_tushare_token_status, set_runtime_tushare_token

router = APIRouter(prefix="/admin", tags=["admin"])


class SetTokenReq(BaseModel):
    token: str


@router.get("/tushare/token-status")
def tushare_token_status():
    return get_tushare_token_status()


@router.post("/tushare/token")
def set_tushare_token(body: SetTokenReq):
    token = (body.token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="token 不能为空")

    try:
        set_runtime_tushare_token(token)
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex)) from ex

    # 按产品要求：保存阶段不调用外部接口校验；在真正同步任务开始前再校验。
    return {"ok": True, "validated": False}

