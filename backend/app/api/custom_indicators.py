"""用户自定义指标 API（路径前缀 /api/indicators/custom/）。

用户可以在前端「自定义指标」页面创建两种类型的自定义指标：

1. DSL 类型（新版，推荐）：
   - 通过 definition_json 字段存储 JSON 格式的公式树
   - 支持多参数、多子线、复杂公式（引用内置指标子线、引用其他子线等）
   - 由 app/services/user_indicator_dsl.py 校验，user_indicator_compute.py 求值

2. 旧版 expr 类型：
   - 通过 expr 字段存储单行表达式字符串，如 "(close - MA20) / MA20 * 100"
   - 只能引用内置变量（MA5/MA20 等，见 allowed_variable_names）
   - 由 app/services/custom_indicator_eval.py 解析和求值

创建/修改流程：
  1. 前端用户填写公式后点「试算」→ /validate-definition 或 /validate-expr
  2. 确认无误后点「保存」→ POST /（创建）或 PATCH /{id}（修改）
  3. 保存时后端再次校验并用指定的试算股票（trial_ts_code）验证公式可以正常计算

关于 builtin-catalog：
  - GET /builtin-catalog 返回所有内置指标的子线列表（供前端「引用内置」选择器）
  - 内置指标来自 indicator_seed.py 种子数据，存于 indicators 表
"""

from __future__ import annotations
from typing import List

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Indicator, UserIndicator
from app.schemas import (
    BuiltinCatalogItem,
    CustomIndicatorVariableNamesOut,
    UserIndicatorCreate,
    UserIndicatorOut,
    UserIndicatorPatch,
    UserIndicatorSampleRow,
    UserIndicatorValidateDefinitionRequest,
    UserIndicatorValidateExprRequest,
    UserIndicatorValidateOut,
    UserIndicatorValidateRequest,
)
from app.services.custom_indicator_eval import parse_and_validate_expr
from app.services.custom_indicator_service import (
    allowed_variable_names,
    assert_code_ok,
    try_eval_on_symbol,
)
from app.services.user_indicator_compute import try_eval_definition_on_symbol
from app.services.user_indicator_dsl import definition_to_storable, parse_and_validate_definition


router = APIRouter(prefix="/indicators/custom", tags=["custom_indicators"])


def _user_indicator_to_out(row: UserIndicator) -> UserIndicatorOut:
    """将 UserIndicator ORM 对象转换为 API 响应格式（UserIndicatorOut）。

    判断指标类型：
    - 若 definition_json 非空且是 version=1 的 JSON → kind='dsl'（新版 DSL 指标）
    - 否则 → kind='legacy'（旧版 expr 指标）

    两种类型对应不同的前端编辑器和求值引擎。
    """
    definition = None
    if row.definition_json and str(row.definition_json).strip():
        try:
            definition = json.loads(row.definition_json)
        except json.JSONDecodeError:
            definition = None
    if definition and isinstance(definition, dict) and definition.get("version") == 1:
        return UserIndicatorOut(
            id=row.id,
            code=row.code,
            display_name=row.display_name,
            description=row.description,
            kind="dsl",
            definition=definition,
            expr=None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    # 没有有效的 DSL definition，降级为旧版 expr 类型
    ex = (row.expr or "").strip()
    return UserIndicatorOut(
        id=row.id,
        code=row.code,
        display_name=row.display_name,
        description=row.description,
        kind="legacy",
        definition=None,
        expr=ex if ex else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _ensure_trial_ok_dsl(db: Session, parsed, trial_ts: str) -> None:
    """对 DSL 指标进行试算校验：用指定股票试算最近几天的值，确认公式能正常求值。

    若试算失败（如股票无数据、公式计算错误等），抛出 ValueError（上层转为 400）。
    """
    raw = try_eval_definition_on_symbol(db, parsed, trial_ts.strip())
    if not raw["ok"]:
        raise ValueError(raw.get("message") or "试算未通过")


def _ensure_trial_ok_legacy(db: Session, expr: str, trial_ts: str) -> None:
    """对旧版 expr 指标进行试算校验（与 DSL 试算类似，但使用不同的求值引擎）。"""
    raw = try_eval_on_symbol(db, expr.strip(), trial_ts.strip())
    if not raw["ok"]:
        raise ValueError(raw.get("message") or "试算未通过")


@router.get("/builtin-catalog", response_model=List[BuiltinCatalogItem])
def builtin_catalog_for_editor(db: Session = Depends(get_db)):
    """获取内置指标目录（供 DSL 编辑器中「引用内置指标」选择器使用）。

    返回所有内置指标及其子线列表，格式：
    [{name, display_name, subs: [{name, description}]}]

    例如：{name: "MACD", subs: [{name: "DIF"}, {name: "DEA"}, {name: "MACD柱"}]}
    """
    rows = db.query(Indicator).order_by(Indicator.id.asc()).all()
    return [
        BuiltinCatalogItem(
            name=r.name,
            display_name=r.display_name,
            subs=[{"name": s.name, "description": s.description} for s in r.sub_indicators],
        )
        for r in rows
    ]


@router.get("/variable-names", response_model=CustomIndicatorVariableNamesOut)
def list_variable_names(db: Session = Depends(get_db)):
    """获取旧版 expr 指标可用的变量名白名单（如 MA5、MA20、DIF、close 等）。

    用于前端旧版 expr 编辑器的自动补全提示，以及后端校验 expr 时的白名单判断。
    """
    names = sorted(allowed_variable_names(db))
    return CustomIndicatorVariableNamesOut(names=names)


@router.get("", response_model=List[UserIndicatorOut])
def list_custom_indicators(db: Session = Depends(get_db)):
    """获取用户创建的所有自定义指标（按 ID 升序，创建越早越靠前）。"""
    rows = db.query(UserIndicator).order_by(UserIndicator.id.asc()).all()
    return [_user_indicator_to_out(r) for r in rows]


@router.post("/validate-definition", response_model=UserIndicatorValidateOut)
def validate_definition_draft(body: UserIndicatorValidateDefinitionRequest, db: Session = Depends(get_db)):
    """实时校验 DSL 指标定义（保存前预检）。

    两步校验：
    1. parse_and_validate_definition：校验 JSON 结构合法性（op 类型、字段名、无环依赖等）
    2. try_eval_definition_on_symbol：用指定股票试算，返回最近几天的样本计算结果

    前端可以在用户编辑公式时（防抖后）实时调用此接口，给出即时反馈。
    """
    try:
        parsed = parse_and_validate_definition(db, body.definition)
    except ValueError as e:
        # 结构校验失败：直接返回错误，无需继续试算
        return UserIndicatorValidateOut(
            ok=False,
            message=str(e),
            sample_rows=[],
            error_detail=str(e),
            report_keys=None,
        )
    raw = try_eval_definition_on_symbol(db, parsed, body.ts_code, trade_date=body.trade_date)
    samples = [
        UserIndicatorSampleRow(
            trade_date=s["trade_date"],
            values=s.get("values"),
            value=None,
            error=s.get("error"),
            diagnostics=s.get("diagnostics"),
        )
        for s in raw["sample_rows"]
    ]
    return UserIndicatorValidateOut(
        ok=raw["ok"],
        message=raw["message"],
        sample_rows=samples,
        error_detail=raw.get("error_detail"),
        report_keys=raw.get("report_keys"),
    )


@router.post("/validate-expr", response_model=UserIndicatorValidateOut)
def validate_expr_draft(body: UserIndicatorValidateExprRequest, db: Session = Depends(get_db)):
    """实时校验旧版 expr 单行表达式（保存前预检）。

    两步校验：
    1. parse_and_validate_expr：解析表达式 AST，检查变量名是否在白名单中
    2. try_eval_on_symbol：用指定股票试算，返回样本计算结果
    """
    allowed = allowed_variable_names(db)
    try:
        parse_and_validate_expr(body.expr, allowed)
    except ValueError as e:
        return UserIndicatorValidateOut(
            ok=False,
            message=str(e),
            sample_rows=[],
            error_detail=str(e),
            report_keys=None,
        )
    raw = try_eval_on_symbol(db, body.expr.strip(), body.ts_code, trade_date=body.trade_date)
    samples = [
        UserIndicatorSampleRow(trade_date=s["trade_date"], value=s.get("value"), values=None, error=s.get("error"))
        for s in raw["sample_rows"]
    ]
    return UserIndicatorValidateOut(
        ok=raw["ok"],
        message=raw["message"],
        sample_rows=samples,
        error_detail=raw.get("error_detail"),
        report_keys=None,
    )


@router.post("", response_model=UserIndicatorOut)
def create_custom_indicator(body: UserIndicatorCreate, db: Session = Depends(get_db)):
    """创建新的自定义指标。

    校验流程（DSL 类型）：
    1. assert_code_ok：code 格式合法且不重复
    2. parse_and_validate_definition：公式树结构合法
    3. _ensure_trial_ok_dsl：用 trial_ts_code 试算通过

    校验流程（旧版 expr 类型）：
    1. assert_code_ok：code 合法且不重复
    2. parse_and_validate_expr：表达式语法合法，变量名在白名单中
    3. _ensure_trial_ok_legacy：用 trial_ts_code 试算通过
    """
    try:
        code = assert_code_ok(db, body.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    dup = db.query(UserIndicator).filter(UserIndicator.code == code).one_or_none()
    if dup:
        raise HTTPException(status_code=400, detail="code 已存在")

    trial_ts = body.trial_ts_code.strip()
    expr_stored = ""
    dj: str | None = None

    if body.definition is not None:
        # DSL 类型：校验公式树 + 试算
        try:
            parsed = parse_and_validate_definition(db, body.definition)
            dj = definition_to_storable(parsed)  # 序列化为 JSON 字符串存库
            _ensure_trial_ok_dsl(db, parsed, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
        # 旧版 expr 类型
        ex = (body.expr or "").strip()
        if not ex:
            raise HTTPException(status_code=400, detail="expr 不能为空（未提供 definition 时）")
        allowed = allowed_variable_names(db)
        try:
            parse_and_validate_expr(ex, allowed)
            _ensure_trial_ok_legacy(db, ex, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        expr_stored = ex

    row = UserIndicator(
        code=code,
        display_name=body.display_name.strip(),
        description=body.description,
        expr=expr_stored,
        definition_json=dj,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _user_indicator_to_out(row)


@router.get("/{custom_id}", response_model=UserIndicatorOut)
def get_custom_indicator(custom_id: int, db: Session = Depends(get_db)):
    """按 ID 获取单个自定义指标的完整定义（含 definition JSON 或 expr）。"""
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    return _user_indicator_to_out(row)


@router.patch("/{custom_id}", response_model=UserIndicatorOut)
def patch_custom_indicator(custom_id: int, body: UserIndicatorPatch, db: Session = Depends(get_db)):
    """修改现有自定义指标（支持只修改部分字段）。

    可修改的字段：display_name、description、definition（DSL）或 expr（旧版）。
    修改 definition/expr 时会重新校验和试算（trial_ts_code 默认为 600000.SH 浦发银行）。

    注意：不允许修改 code（唯一标识符，修改会破坏已保存的选股/回测配置）。
    """
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    trial_ts = (body.trial_ts_code or "600000.SH").strip()
    if body.display_name is not None:
        row.display_name = body.display_name.strip()
    if body.description is not None:
        row.description = body.description

    if body.definition is not None:
        # 更新为 DSL 类型（同时清空旧 expr）
        try:
            parsed = parse_and_validate_definition(db, body.definition)
            row.definition_json = definition_to_storable(parsed)
            row.expr = ""  # 切换为 DSL 时清空旧版 expr
            _ensure_trial_ok_dsl(db, parsed, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif body.expr is not None:
        # 更新为旧版 expr 类型（同时清空 definition_json）
        ex = body.expr.strip()
        allowed = allowed_variable_names(db)
        try:
            parse_and_validate_expr(ex, allowed)
            row.expr = ex
            row.definition_json = None  # 切换为 expr 时清空 DSL definition
            _ensure_trial_ok_legacy(db, ex, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    row.updated_at = datetime.utcnow()  # 手动更新修改时间
    db.commit()
    db.refresh(row)
    return _user_indicator_to_out(row)


@router.delete("/{custom_id}")
def delete_custom_indicator(custom_id: int, db: Session = Depends(get_db)):
    """删除自定义指标（物理删除，不可恢复）。

    注意：删除后，依赖此指标的选股配置会失效（因为 user_indicator_id 指向的记录已不存在）。
    """
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/{custom_id}/validate", response_model=UserIndicatorValidateOut)
def validate_custom_indicator(custom_id: int, body: UserIndicatorValidateRequest, db: Session = Depends(get_db)):
    """对已保存的自定义指标进行试算（可更换试算股票和日期，用于验证指标在不同标的上的表现）。

    适用场景：用户已保存指标后，想看它在另一只股票上的值是否合理。
    与 /validate-definition 的区别：此接口从数据库读取已保存的定义，而不是从请求体读取草稿。
    """
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    out = _user_indicator_to_out(row)
    if out.kind == "dsl" and out.definition:
        # DSL 类型：重新解析定义（确保校验是最新的），然后试算
        try:
            parsed = parse_and_validate_definition(db, out.definition)
        except ValueError as e:
            return UserIndicatorValidateOut(
                ok=False, message=str(e), sample_rows=[], error_detail=str(e), report_keys=None
            )
        raw = try_eval_definition_on_symbol(db, parsed, body.ts_code, trade_date=body.trade_date)
        samples = [
            UserIndicatorSampleRow(
                trade_date=s["trade_date"],
                values=s.get("values"),
                value=None,
                error=s.get("error"),
                diagnostics=s.get("diagnostics"),
            )
            for s in raw["sample_rows"]
        ]
        return UserIndicatorValidateOut(
            ok=raw["ok"],
            message=raw["message"],
            sample_rows=samples,
            error_detail=raw.get("error_detail"),
            report_keys=raw.get("report_keys"),
        )
    # 旧版 expr 类型
    ex = (row.expr or "").strip()
    raw = try_eval_on_symbol(db, ex, body.ts_code, trade_date=body.trade_date)
    samples = [
        UserIndicatorSampleRow(trade_date=s["trade_date"], value=s.get("value"), values=None, error=s.get("error"))
        for s in raw["sample_rows"]
    ]
    return UserIndicatorValidateOut(
        ok=raw["ok"],
        message=raw["message"],
        sample_rows=samples,
        error_detail=raw.get("error_detail"),
        report_keys=None,
    )
