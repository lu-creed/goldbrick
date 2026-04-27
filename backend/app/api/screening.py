"""条件选股 API（路径前缀 /api/screening/）。

接口列表：
  POST /api/screening/run           执行一次选股，结果自动保存到历史
  GET  /api/screening/history       获取历史记录列表（分页）
  GET  /api/screening/history/{id}  获取单条历史记录详情（含命中股票列表）
  DELETE /api/screening/history/{id} 删除单条历史记录

核心扫描逻辑在 app/services/screening_runner.py 中实现（分批查询 + 指标计算）。
"""

import json
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ScreeningHistory, UserIndicator
from app.auth import get_current_user
from app.schemas import (
    ScreeningHistoryDetail,
    ScreeningHistoryItem,
    ScreeningRunIn,
    ScreeningRunOut,
    ScreeningStockRow,
)
from app.services.screening_runner import run_screen

log = logging.getLogger(__name__)
router = APIRouter(prefix="/screening", tags=["screening"])


@router.post("/run", response_model=ScreeningRunOut)
def screening_run(body: ScreeningRunIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """在指定交易日对全市场个股执行条件选股扫描，并自动保存本次结果到历史记录。

    扫描流程（约 6000 只个股，分批进行）：
    1. 查出该交易日有日线的全部个股列表
    2. 每批 450 只，一次性加载「预热期~交易日」的历史 K 线
    3. 对每只股票用指标引擎计算最后一根 K 线（即交易日当天）的指标值
    4. 与阈值比较（compare_op），满足条件加入结果
    5. 结果按指标值降序排列
    6. 将本次结果写入 screening_history 表（自动保存，无需额外操作）
    """
    try:
        raw = run_screen(
            db,
            trade_date=body.trade_date,
            user_indicator_id=body.user_indicator_id,
            sub_key=body.sub_key or "",
            compare_op=body.compare_op,
            threshold=body.threshold,
            max_scan=body.max_scan,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    items = [ScreeningStockRow(**x) for x in raw["items"]]

    # ── 自动保存到历史记录 ────────────────────────────────────────
    # 查指标名称（冗余存储，即使指标后续被删除也能展示历史名称）
    indicator_name = ""
    indicator_code = ""
    ind = db.query(UserIndicator).filter(UserIndicator.id == body.user_indicator_id).first()
    if ind:
        indicator_name = ind.display_name
        indicator_code = ind.code

    history_record = ScreeningHistory(
        user_id=current_user.id,
        created_at=datetime.utcnow(),
        trade_date=str(body.trade_date),
        user_indicator_id=body.user_indicator_id,
        indicator_name=indicator_name,
        indicator_code=indicator_code,
        sub_key=body.sub_key,
        compare_op=body.compare_op,
        threshold=body.threshold,
        scanned=raw["scanned"],
        matched=raw["matched"],
        # 只保存核心字段，节省空间（不保存 pct_change 等）
        result_json=json.dumps(
            [{"ts_code": r.ts_code, "name": r.name, "close": r.close,
              "pct_change": r.pct_change, "indicator_value": r.indicator_value}
             for r in items],
            ensure_ascii=False,
        ),
    )
    try:
        db.add(history_record)
        db.commit()
        db.refresh(history_record)
        history_id = history_record.id
    except Exception:
        log.exception("保存选股历史失败，不影响本次结果返回")
        db.rollback()
        history_id = None
    # ────────────────────────────────────────────────────────────

    return ScreeningRunOut(
        trade_date=raw["trade_date"],
        user_indicator_id=raw.get("user_indicator_id"),
        sub_key=raw.get("sub_key"),
        compare_op=raw.get("compare_op"),
        threshold=raw.get("threshold"),
        scanned=raw["scanned"],
        matched=raw["matched"],
        note=raw.get("note"),
        items=items,
        history_id=history_id,
    )


@router.get("/history", response_model=List[ScreeningHistoryItem])
def screening_history_list(
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(20, ge=5, le=100, description="每页条数"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取条件选股历史记录列表（按执行时间倒序，最新的排在最前面）。

    返回摘要信息，不包含命中股票列表（详情通过 /history/{id} 获取）。
    支持分页，page=1 page_size=20 获取最近 20 条。
    """
    offset = (page - 1) * page_size
    rows = (
        db.query(ScreeningHistory)
        .filter(ScreeningHistory.user_id == current_user.id)
        .order_by(ScreeningHistory.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return rows


@router.get("/history/{record_id}", response_model=ScreeningHistoryDetail)
def screening_history_detail(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单条选股历史的详情，包含命中的股票列表。"""
    row = db.query(ScreeningHistory).filter(
        ScreeningHistory.id == record_id,
        ScreeningHistory.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史记录不存在")

    # 将 result_json 字符串反序列化为 ScreeningStockRow 列表
    try:
        raw_items = json.loads(row.result_json or "[]")
        items = [ScreeningStockRow(**x) for x in raw_items]
    except Exception:
        log.exception("解析选股历史 result_json 失败，id=%s", record_id)
        items = []

    return ScreeningHistoryDetail(
        id=row.id,
        created_at=row.created_at,
        trade_date=row.trade_date,
        indicator_name=row.indicator_name,
        indicator_code=row.indicator_code,
        user_indicator_id=row.user_indicator_id,
        sub_key=row.sub_key,
        compare_op=row.compare_op,
        threshold=float(row.threshold),
        scanned=row.scanned,
        matched=row.matched,
        items=items,
    )


@router.delete("/history/{record_id}")
def screening_history_delete(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """删除指定的选股历史记录（不可恢复）。"""
    row = db.query(ScreeningHistory).filter(
        ScreeningHistory.id == record_id,
        ScreeningHistory.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史记录不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}
