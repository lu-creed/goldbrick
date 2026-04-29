"""策略 API（路径前缀 /api/strategies/）。

策略 = 多个条件 + 组（组内 AND）+ combiner 树（组间 AND/OR/NOT/括号）+ 主排序条件。

接口列表：
  GET    /api/strategies                 列出当前用户可见的所有策略（自己的 + 系统预置）
  GET    /api/strategies/{id}            获取单个策略详情
  POST   /api/strategies                 创建新策略
  PATCH  /api/strategies/{id}            修改策略（kind 不可改）
  DELETE /api/strategies/{id}            删除策略（系统预置不可删）
  POST   /api/strategies/{id}/dry-run    指定股票 + 截面日试算，返回每条件/组/命中详情

权限：
  - user_id IS NULL 的策略为系统预置，所有用户可读但不可改删
  - 自己的策略完整 CRUD
  - 看不到别人的策略

Combiner 求值逻辑在 app/services/combiner.py；
单 bar / 序列求值在 app/services/strategy_engine.py。
策略实际运行（选股/回测）将在 PR#4 / PR#5 接入 run_screen / run_backtest。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import BarDaily, Strategy, Symbol, UserIndicator
from app.schemas import (
    GalleryPreview,
    StrategyCreate,
    StrategyDryRunIn,
    StrategyDryRunLogicResult,
    StrategyDryRunOut,
    StrategyGalleryCard,
    StrategyListItem,
    StrategyLogic,
    StrategyOut,
    StrategyPatch,
)
from app.services.combiner import validate_combiner
from app.services.strategy_engine import compile_strategy, dry_run_on_bars
from app.services.strategy_seed import list_presets

log = logging.getLogger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])


# 策略 code 命名规则：字母开头，字母/数字/下划线，长度 1~64
_CODE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def _assert_code_ok(code: str) -> str:
    """校验策略 code 格式合法。返回 strip 过的 code。"""
    code = (code or "").strip()
    if not code or not _CODE_PATTERN.match(code):
        raise ValueError("code 必须以字母开头，仅含字母/数字/下划线，长度 1~64")
    return code


def _validate_logic(db: Session, logic: StrategyLogic) -> None:
    """校验 logic 结构：引用的 user_indicator_id 全部存在 + combiner 树合法。

    Pydantic 层已做的校验：
      - conditions / groups id 唯一
      - groups.condition_ids 都在 conditions 中
      - primary_condition_id 在 conditions 中
      - combiner 节点 ref/op 互斥

    本函数追加：
      - 每个条件的 user_indicator_id 都存在
      - combiner 树中所有 ref 都指向已定义的组 + 深度合法
    """
    ind_ids = {c.user_indicator_id for c in logic.conditions}
    existing = {
        row.id for row in db.query(UserIndicator.id).filter(UserIndicator.id.in_(ind_ids)).all()
    }
    missing = ind_ids - existing
    if missing:
        raise ValueError(f"引用的自定义指标不存在: {sorted(missing)}")
    group_ids = [g.id for g in logic.groups]
    validate_combiner(logic.combiner.model_dump(exclude_none=True), group_ids)


def _row_to_out(row: Strategy) -> StrategyOut:
    """ORM Strategy → StrategyOut，反序列化三个 logic_json 字段。"""
    def parse(s: Optional[str]) -> Optional[StrategyLogic]:
        if not s or not s.strip():
            return None
        try:
            return StrategyLogic(**json.loads(s))
        except (json.JSONDecodeError, ValueError):
            log.exception("策略 %s 的 logic_json 反序列化失败", row.id)
            return None

    return StrategyOut(
        id=row.id,
        code=row.code,
        display_name=row.display_name,
        description=row.description,
        notes=row.notes if row.user_id is not None else None,  # 系统预置策略不返回 notes
        kind=row.kind,  # type: ignore[arg-type]
        logic=parse(row.logic_json),
        buy_logic=parse(row.buy_logic_json),
        sell_logic=parse(row.sell_logic_json),
        is_system=row.user_id is None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_list_item(row: Strategy) -> StrategyListItem:
    return StrategyListItem(
        id=row.id,
        code=row.code,
        display_name=row.display_name,
        description=row.description,
        kind=row.kind,  # type: ignore[arg-type]
        is_system=row.user_id is None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _dump_logic(logic: Optional[StrategyLogic]) -> Optional[str]:
    if logic is None:
        return None
    # 用 pydantic 的 model_dump 得到纯 dict，剔除 None 的 combiner 字段
    return json.dumps(logic.model_dump(exclude_none=False, mode="json"), ensure_ascii=False)


# ─────────────────────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────────────────────

@router.get("/gallery", response_model=List[StrategyGalleryCard])
def get_strategy_gallery(_user=Depends(get_current_user), db: Session = Depends(get_db)):
    """策略广场:返回 12 个预置策略的卡片数据(含人话描述 + 预跑回测快照)。

    元数据(category/one_liner/good_for/preview)来自 strategy_seed.py 的声明,不落库;
    但 strategy_id 来自 strategies 表(需 ensure_default_strategies 启动钩子已跑过)。
    """
    # 查所有系统预置策略(user_id IS NULL),按 code 映射
    system_rows = (
        db.query(Strategy.code, Strategy.id)
        .filter(Strategy.user_id.is_(None))
        .all()
    )
    code_to_id = {row.code: row.id for row in system_rows}

    out: List[StrategyGalleryCard] = []
    for p in list_presets():
        out.append(StrategyGalleryCard(
            strategy_id=code_to_id.get(p.code),  # 可能为 None(预置指标缺失时 seed 跳过)
            code=p.code,
            display_name=p.display_name,
            description=p.description,
            category=p.category,
            one_liner=p.one_liner,
            long_description=p.long_description,
            good_for=p.good_for,
            bad_for=p.bad_for,
            preview=GalleryPreview(
                window=p.preview_window,
                total_return_pct=p.preview_total_return_pct,
                max_drawdown_pct=p.preview_max_drawdown_pct,
                total_trades=p.preview_total_trades,
                win_rate=p.preview_win_rate,
            ),
        ))
    return out


@router.get("", response_model=List[StrategyListItem])
def list_strategies(
    kind: Optional[str] = Query(None, description="screen | backtest，不传返回全部"),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前用户可见的所有策略（自己的 + 系统预置），按 ID 升序。"""
    q = db.query(Strategy).filter(
        or_(Strategy.user_id.is_(None), Strategy.user_id == current_user.id)
    )
    if kind:
        if kind not in ("screen", "backtest"):
            raise HTTPException(status_code=400, detail="kind 须为 screen 或 backtest")
        q = q.filter(Strategy.kind == kind)
    rows = q.order_by(Strategy.id.asc()).all()
    return [_row_to_list_item(r) for r in rows]


@router.get("/{strategy_id}", response_model=StrategyOut)
def get_strategy(
    strategy_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取单个策略详情（含完整 logic）。"""
    row = db.query(Strategy).filter(Strategy.id == strategy_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    if row.user_id is not None and row.user_id != current_user.id:
        # 不是系统预置、也不是自己的 → 装作不存在
        raise HTTPException(status_code=404, detail="策略不存在")
    return _row_to_out(row)


@router.post("", response_model=StrategyOut)
def create_strategy(
    body: StrategyCreate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新策略。code 在自己的策略中必须唯一。"""
    try:
        code = _assert_code_ok(body.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    dup = db.query(Strategy).filter(
        Strategy.user_id == current_user.id,
        Strategy.code == code,
    ).one_or_none()
    if dup:
        raise HTTPException(status_code=400, detail="code 已存在")

    try:
        if body.kind == "screen":
            _validate_logic(db, body.logic)  # type: ignore[arg-type]
        else:
            _validate_logic(db, body.buy_logic)   # type: ignore[arg-type]
            _validate_logic(db, body.sell_logic)  # type: ignore[arg-type]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    row = Strategy(
        user_id=current_user.id,
        code=code,
        display_name=body.display_name.strip(),
        description=(body.description or "").strip() or None,
        notes=(body.notes or "").strip() or None,
        kind=body.kind,
        logic_json=_dump_logic(body.logic),
        buy_logic_json=_dump_logic(body.buy_logic),
        sell_logic_json=_dump_logic(body.sell_logic),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _row_to_out(row)


@router.patch("/{strategy_id}", response_model=StrategyOut)
def update_strategy(
    strategy_id: int,
    body: StrategyPatch,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """修改策略：只更新传入的字段，其余保留。系统预置不可改。"""
    row = db.query(Strategy).filter(Strategy.id == strategy_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    if row.user_id is None:
        raise HTTPException(status_code=403, detail="系统预置策略不可修改")
    if row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="策略不存在")

    # logic 校验（按 kind 决定该校验哪几个）
    try:
        if body.logic is not None:
            if row.kind != "screen":
                raise HTTPException(status_code=400, detail="非选股策略不能设置 logic")
            _validate_logic(db, body.logic)
        if body.buy_logic is not None:
            if row.kind != "backtest":
                raise HTTPException(status_code=400, detail="非回测策略不能设置 buy_logic")
            _validate_logic(db, body.buy_logic)
        if body.sell_logic is not None:
            if row.kind != "backtest":
                raise HTTPException(status_code=400, detail="非回测策略不能设置 sell_logic")
            _validate_logic(db, body.sell_logic)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if body.display_name is not None:
        row.display_name = body.display_name.strip()
    if body.description is not None:
        row.description = (body.description or "").strip() or None
    if body.notes is not None:
        # 支持传空串清空 notes:strip 后为空则置 None;否则保留原 Markdown 文本(含换行)
        trimmed = body.notes.strip()
        row.notes = trimmed if trimmed else None
    if body.logic is not None:
        row.logic_json = _dump_logic(body.logic)
    if body.buy_logic is not None:
        row.buy_logic_json = _dump_logic(body.buy_logic)
    if body.sell_logic is not None:
        row.sell_logic_json = _dump_logic(body.sell_logic)

    db.commit()
    db.refresh(row)
    return _row_to_out(row)


@router.delete("/{strategy_id}")
def delete_strategy(
    strategy_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除策略（不可恢复）。系统预置不可删除。"""
    row = db.query(Strategy).filter(Strategy.id == strategy_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    if row.user_id is None:
        raise HTTPException(status_code=403, detail="系统预置策略不可删除")
    if row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="策略不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


# ─────────────────────────────────────────────────────────────
# dry-run：给一只股票 + 截面日，返回每条件/组/命中详情（调试用）
# ─────────────────────────────────────────────────────────────

def _load_bars_for_dryrun(
    db: Session, ts_code: str, trade_date: Optional[date], warmup_days: int,
) -> tuple[list[BarDaily], Optional[date], Optional[str]]:
    """加载指定股票的日线窗口 [trade_date - warmup_days, trade_date]。

    Returns:
        (bars, effective_date, error_msg)
        - trade_date 未指定时取 bars 末尾日期
        - error_msg 非空表示数据问题（股票不存在、无日线等），调用方应直接短路
    """
    code = ts_code.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        return [], None, f"未找到股票 {code}"

    if trade_date is None:
        # 取最后一根 K 线
        last = (
            db.query(BarDaily)
            .filter(BarDaily.symbol_id == sym.id)
            .order_by(BarDaily.trade_date.desc())
            .first()
        )
        if not last:
            return [], None, f"股票 {code} 无日线数据，请先同步"
        trade_date = last.trade_date

    start = trade_date - timedelta(days=warmup_days)
    bars = (
        db.query(BarDaily)
        .filter(
            BarDaily.symbol_id == sym.id,
            BarDaily.trade_date >= start,
            BarDaily.trade_date <= trade_date,
        )
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if not bars:
        return [], trade_date, f"股票 {code} 在 {trade_date} 及之前 {warmup_days} 天内无日线"
    if bars[-1].trade_date != trade_date:
        return bars, trade_date, f"{trade_date} 非交易日，最后可用日期为 {bars[-1].trade_date}"
    return bars, trade_date, None


def _dry_run_one_logic(
    db: Session, logic: StrategyLogic, bars: list[BarDaily], note: Optional[str],
) -> StrategyDryRunLogicResult:
    """编译 + 在 bars 上跑一次 logic，转成响应模型。"""
    compiled = compile_strategy(db, logic.model_dump(exclude_none=False, mode="json"))
    raw = dry_run_on_bars(compiled, bars)
    # raw 的结构已与 StrategyDryRunLogicResult 对齐（只差 note）
    result = StrategyDryRunLogicResult(**raw)
    if note and not result.note:
        result.note = note
    return result


@router.post("/{strategy_id}/dry-run", response_model=StrategyDryRunOut)
def strategy_dry_run(
    strategy_id: int,
    body: StrategyDryRunIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对指定策略做试算：给一只股票 + 截面日，返回每条件/组/命中详情。

    用途：前端策略编辑器的「试一下」按钮 —— 让用户立即看到当前配置在某只股票上是否命中、
    每个条件的指标值与布尔结果，便于调试阈值和逻辑。

    返回结构：
      - screen 策略：main 字段包含完整试算结果，buy/sell 为 None
      - backtest 策略：buy 和 sell 字段各包含一份结果（同一截面日），main 为 None
    """
    row = db.query(Strategy).filter(Strategy.id == strategy_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="策略不存在")
    if row.user_id is not None and row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="策略不存在")

    bars, _eff_date, err = _load_bars_for_dryrun(db, body.ts_code, body.trade_date, body.warmup_days)
    if err and not bars:
        raise HTTPException(status_code=400, detail=err)

    try:
        if row.kind == "screen":
            logic_dict = json.loads(row.logic_json) if row.logic_json else None
            if not logic_dict:
                raise HTTPException(status_code=500, detail="策略缺少 logic 配置")
            logic = StrategyLogic(**logic_dict)
            main = _dry_run_one_logic(db, logic, bars, note=err)
            return StrategyDryRunOut(
                strategy_id=row.id, kind="screen", ts_code=body.ts_code.upper(), main=main,
            )
        # backtest
        buy_dict = json.loads(row.buy_logic_json) if row.buy_logic_json else None
        sell_dict = json.loads(row.sell_logic_json) if row.sell_logic_json else None
        if not buy_dict or not sell_dict:
            raise HTTPException(status_code=500, detail="回测策略缺少 buy_logic 或 sell_logic")
        buy = _dry_run_one_logic(db, StrategyLogic(**buy_dict), bars, note=err)
        sell = _dry_run_one_logic(db, StrategyLogic(**sell_dict), bars, note=err)
        return StrategyDryRunOut(
            strategy_id=row.id, kind="backtest", ts_code=body.ts_code.upper(),
            buy=buy, sell=sell,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
