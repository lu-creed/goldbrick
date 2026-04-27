"""自选股池 API（路径前缀 /api/watchlist/）。

轻量收藏功能：把关注的股票加入自选股池，方便从复盘/选股页面一键导入，
再从自选股页面集中查看并跳转 K 线。

与「大V看板」区别：
  - 大V看板：ABCD 框架分类 + 派息率 + EPS，精细化管理少数核心仓股
  - 自选股池：轻量收藏，快速标记关注但不一定深研的股票

接口列表：
  GET    /api/watchlist/         查询所有自选股（按加入时间倒序）
  POST   /api/watchlist/         添加一只股票到自选股池
  DELETE /api/watchlist/{ts_code}  从自选股池移除
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import WatchlistStock
from app.auth import get_current_user

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


# ── Schemas ────────────────────────────────────────────────────────────────

class WatchlistIn(BaseModel):
    """添加自选股时的请求体。"""
    ts_code: str          # 股票代码，如 "000001.SZ"
    name: Optional[str] = None   # 股票名称（前端展示用，可选填）
    note: Optional[str] = None   # 用户自由备注


class WatchlistOut(BaseModel):
    """返回给前端的自选股记录。"""
    ts_code: str
    name: Optional[str]
    note: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}  # 支持从 SQLAlchemy ORM 对象直接转换


# ── 接口实现 ────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[WatchlistOut])
def list_watchlist(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """返回当前用户的全部自选股，按加入时间倒序。"""
    rows = (
        db.query(WatchlistStock)
        .filter(WatchlistStock.user_id == current_user.id)
        .order_by(WatchlistStock.created_at.desc())
        .all()
    )
    return rows


@router.post("/", response_model=WatchlistOut)
def add_to_watchlist(body: WatchlistIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """添加一只股票到自选股池。若已存在则更新 name / note 后返回。"""
    ts_code = body.ts_code.strip().upper()
    existing = db.query(WatchlistStock).filter(
        WatchlistStock.user_id == current_user.id,
        WatchlistStock.ts_code == ts_code,
    ).one_or_none()
    if existing:
        if body.name is not None:
            existing.name = body.name
        if body.note is not None:
            existing.note = body.note
        db.commit()
        db.refresh(existing)
        return existing
    item = WatchlistStock(user_id=current_user.id, ts_code=ts_code, name=body.name, note=body.note)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{ts_code}")
def remove_from_watchlist(ts_code: str, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """从自选股池移除一只股票。ts_code 大小写不敏感。"""
    item = (
        db.query(WatchlistStock)
        .filter(
            WatchlistStock.user_id == current_user.id,
            WatchlistStock.ts_code == ts_code.upper(),
        )
        .one_or_none()
    )
    if not item:
        raise HTTPException(status_code=404, detail="not in watchlist")
    db.delete(item)
    db.commit()
    return {"ok": True}
