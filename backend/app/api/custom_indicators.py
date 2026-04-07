"""用户自定义指标 API：/api/indicators/custom/...

PRD：definition_json 存多参数、多子线、公式树（引用内置子线 / 兄弟子线 + 取数方式）；
兼容旧版单条 expr。保存前对 trial_ts_code 试算（与 PRD「选一只股票校验」一致）。
"""

from __future__ import annotations

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
    raw = try_eval_definition_on_symbol(db, parsed, trial_ts.strip())
    if not raw["ok"]:
        raise ValueError(raw.get("message") or "试算未通过")


def _ensure_trial_ok_legacy(db: Session, expr: str, trial_ts: str) -> None:
    raw = try_eval_on_symbol(db, expr.strip(), trial_ts.strip())
    if not raw["ok"]:
        raise ValueError(raw.get("message") or "试算未通过")


@router.get("/builtin-catalog", response_model=list[BuiltinCatalogItem])
def builtin_catalog_for_editor(db: Session = Depends(get_db)):
    """公式「引用内置」：按指标分组的子线名，与指标库种子一致。"""
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
    """旧版 expr 白名单 + 内置子线名参考。"""
    names = sorted(allowed_variable_names(db))
    return CustomIndicatorVariableNamesOut(names=names)


@router.get("", response_model=list[UserIndicatorOut])
def list_custom_indicators(db: Session = Depends(get_db)):
    rows = db.query(UserIndicator).order_by(UserIndicator.id.asc()).all()
    return [_user_indicator_to_out(r) for r in rows]


@router.post("/validate-definition", response_model=UserIndicatorValidateOut)
def validate_definition_draft(body: UserIndicatorValidateDefinitionRequest, db: Session = Depends(get_db)):
    """保存前试算完整 DSL（须先于创建或可与表单联动）。"""
    try:
        parsed = parse_and_validate_definition(db, body.definition)
    except ValueError as e:
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
        try:
            parsed = parse_and_validate_definition(db, body.definition)
            dj = definition_to_storable(parsed)
            _ensure_trial_ok_dsl(db, parsed, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    else:
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
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    return _user_indicator_to_out(row)


@router.patch("/{custom_id}", response_model=UserIndicatorOut)
def patch_custom_indicator(custom_id: int, body: UserIndicatorPatch, db: Session = Depends(get_db)):
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    trial_ts = (body.trial_ts_code or "600000.SH").strip()
    if body.display_name is not None:
        row.display_name = body.display_name.strip()
    if body.description is not None:
        row.description = body.description

    if body.definition is not None:
        try:
            parsed = parse_and_validate_definition(db, body.definition)
            row.definition_json = definition_to_storable(parsed)
            row.expr = ""
            _ensure_trial_ok_dsl(db, parsed, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    elif body.expr is not None:
        ex = body.expr.strip()
        allowed = allowed_variable_names(db)
        try:
            parse_and_validate_expr(ex, allowed)
            row.expr = ex
            row.definition_json = None
            _ensure_trial_ok_legacy(db, ex, trial_ts)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _user_indicator_to_out(row)


@router.delete("/{custom_id}")
def delete_custom_indicator(custom_id: int, db: Session = Depends(get_db)):
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/{custom_id}/validate", response_model=UserIndicatorValidateOut)
def validate_custom_indicator(custom_id: int, body: UserIndicatorValidateRequest, db: Session = Depends(get_db)):
    row = db.query(UserIndicator).filter(UserIndicator.id == custom_id).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="自定义指标不存在")
    out = _user_indicator_to_out(row)
    if out.kind == "dsl" and out.definition:
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
