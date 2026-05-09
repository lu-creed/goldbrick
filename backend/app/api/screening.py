"""条件选股 API（路径前缀 /api/screening/）。

接口列表：
  POST /api/screening/run           执行一次选股，结果自动保存到历史
  GET  /api/screening/history       获取历史记录列表（分页）
  GET  /api/screening/history/{id}  获取单条历史记录详情（含命中股票列表）
  DELETE /api/screening/history/{id} 删除单条历史记录

核心扫描逻辑在 app/services/screening_runner.py 中实现（分批查询 + 策略引擎求值）。

多条件支持：body 可传 strategy_id（引用已保存策略）、logic（直接传条件）或老的单条件字段。
历史表新增 strategy_snapshot_json 列，多条件时回填完整 logic 快照；老单条件记录仍走原字段。
"""

import json
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ScreeningHistory, Strategy, UserIndicator
from app.auth import get_current_user, get_current_user_optional
from app.schemas import (
    ScreeningHistoryDetail,
    ScreeningHistoryItem,
    ScreeningRunIn,
    ScreeningRunOut,
    ScreeningStockRow,
    StrategyLogic,
)
from app.services.screening_runner import run_screen

log = logging.getLogger(__name__)
router = APIRouter(prefix="/screening", tags=["screening"])


def _resolve_logic_from_body(
    body: ScreeningRunIn, current_user, db: Session,
) -> tuple[Optional[dict], Optional[int]]:
    """按优先级解析选股入参为 logic dict：strategy_id > logic > （老字段 → None 由 runner 转换）。

    Returns: (logic_dict | None, strategy_id | None)
      - logic_dict 非空 → 多条件路径
      - 都为 None → 老单条件路径，runner 内部走 legacy_to_logic
    """
    if body.strategy_id is not None:
        row = db.query(Strategy).filter(Strategy.id == body.strategy_id).one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="策略不存在")
        if row.user_id is not None and row.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="策略不存在")
        if row.kind != "screen":
            raise HTTPException(status_code=400, detail="该策略不是选股策略")
        if not row.logic_json:
            raise HTTPException(status_code=500, detail="策略缺少 logic 配置")
        return json.loads(row.logic_json), row.id
    if body.logic is not None:
        return body.logic.model_dump(exclude_none=False, mode="json"), None
    return None, None


def _primary_condition_of(logic_dict: dict) -> dict:
    """取出 logic 里 primary_condition_id 对应的条件，用于回填老字段冗余。"""
    pid = logic_dict.get("primary_condition_id")
    for c in logic_dict.get("conditions") or []:
        if int(c.get("id", 0)) == int(pid):
            return c
    return {}


@router.post("/run", response_model=ScreeningRunOut)
def screening_run(body: ScreeningRunIn, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """在指定交易日对全市场个股执行条件选股扫描，并自动保存本次结果到历史记录。

    优先级：strategy_id > logic > 老单条件字段。
    """
    logic_dict, used_strategy_id = _resolve_logic_from_body(body, current_user, db)
    try:
        if logic_dict is not None:
            raw = run_screen(db, trade_date=body.trade_date, logic=logic_dict, max_scan=body.max_scan)
        else:
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
    is_multi = bool(raw.get("is_multi"))
    effective_logic = raw.get("logic")  # runner 总会回填（包括老路径的 1 条件 logic）

    # ── 查询指标名用于冗余存储 ────────────────────────────────────
    # 多条件时，取主条件对应的指标名；老单条件路径则直接用 body.user_indicator_id
    primary_cond = _primary_condition_of(effective_logic or {})
    primary_uid = int(primary_cond.get("user_indicator_id") or body.user_indicator_id or 0) or None
    indicator_name = ""
    indicator_code = ""
    if primary_uid:
        ind = db.query(UserIndicator).filter(UserIndicator.id == primary_uid).first()
        if ind:
            indicator_name = ind.display_name
            indicator_code = ind.code
    if is_multi:
        # 多条件时列表页显示为"多条件 (N个)"，详情页再解析快照看每一条
        n_conds = len(effective_logic.get("conditions") or []) if effective_logic else 0
        indicator_name = indicator_name + f"｜多条件 ({n_conds})" if indicator_name else f"多条件 ({n_conds})"

    # ── 自动保存到历史记录 ────────────────────────────────────────
    history_record = ScreeningHistory(
        user_id=current_user.id,
        created_at=datetime.now(),
        trade_date=str(body.trade_date),
        user_indicator_id=primary_uid,
        indicator_name=indicator_name,
        indicator_code=indicator_code,
        sub_key=primary_cond.get("sub_key") if is_multi else body.sub_key,
        compare_op=primary_cond.get("compare_op", body.compare_op),
        threshold=float(primary_cond.get("threshold", body.threshold)),
        scanned=raw["scanned"],
        matched=raw["matched"],
        result_json=json.dumps(
            [{"ts_code": r.ts_code, "name": r.name, "close": r.close,
              "pct_change": r.pct_change, "indicator_value": r.indicator_value,
              "indicator_values": r.indicator_values}
             for r in items],
            ensure_ascii=False,
        ),
        strategy_snapshot_json=(
            json.dumps(effective_logic, ensure_ascii=False) if is_multi else None
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
        is_multi=is_multi,
        logic=StrategyLogic(**effective_logic) if is_multi and effective_logic else None,
        strategy_id=used_strategy_id,
        scanned=raw["scanned"],
        matched=raw["matched"],
        note=raw.get("note"),
        items=items,
        history_id=history_id,
        adj_mode=raw.get("adj_mode", "qfq"),
    )


@router.get("/history", response_model=List[ScreeningHistoryItem])
def screening_history_list(
    page: int = Query(1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(20, ge=5, le=100, description="每页条数"),
    current_user=Depends(get_current_user_optional),
    db: Session = Depends(get_db),
):
    """获取条件选股历史记录列表（按执行时间倒序，最新的排在最前面）。未登录返回空列表。"""
    if current_user is None:
        return []
    offset = (page - 1) * page_size
    rows = (
        db.query(ScreeningHistory)
        .filter(ScreeningHistory.user_id == current_user.id)
        .order_by(ScreeningHistory.created_at.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return [
        ScreeningHistoryItem(
            id=r.id,
            created_at=r.created_at,
            trade_date=r.trade_date,
            indicator_name=r.indicator_name,
            indicator_code=r.indicator_code,
            user_indicator_id=r.user_indicator_id,
            sub_key=r.sub_key,
            compare_op=r.compare_op,
            threshold=float(r.threshold),
            scanned=r.scanned,
            matched=r.matched,
            is_multi=bool(r.strategy_snapshot_json),
        )
        for r in rows
    ]


@router.get("/history/{record_id}", response_model=ScreeningHistoryDetail)
def screening_history_detail(record_id: int, current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单条选股历史的详情，包含命中的股票列表 + 多条件快照（如有）。"""
    row = db.query(ScreeningHistory).filter(
        ScreeningHistory.id == record_id,
        ScreeningHistory.user_id == current_user.id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="历史记录不存在")

    try:
        raw_items = json.loads(row.result_json or "[]")
        items = [ScreeningStockRow(**x) for x in raw_items]
    except Exception:
        log.exception("解析选股历史 result_json 失败，id=%s", record_id)
        items = []

    logic_obj: Optional[StrategyLogic] = None
    if row.strategy_snapshot_json:
        try:
            logic_obj = StrategyLogic(**json.loads(row.strategy_snapshot_json))
        except Exception:
            log.exception("解析选股历史 strategy_snapshot_json 失败，id=%s", record_id)

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
        is_multi=bool(row.strategy_snapshot_json),
        items=items,
        logic=logic_obj,
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
