"""自定义指标：白名单变量名、在样本标的上的试算。"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import BarDaily, Indicator, IndicatorSubIndicator, Symbol
from app.services.custom_indicator_eval import BAR_FIELD_NAMES, CODE_PATTERN, eval_expression, parse_and_validate_expr
from app.services.indicator_compute import compute_indicators


def allowed_variable_names(db: Session) -> frozenset[str]:
    rows = db.query(IndicatorSubIndicator.name).distinct().all()
    names = {r[0] for r in rows if r[0]}
    return frozenset(names | set(BAR_FIELD_NAMES))


def reserved_codes(db: Session) -> set[str]:
    rows = db.query(Indicator.name).all()
    built = {r[0].strip().upper() for r in rows if r[0]}
    fields = {x.upper() for x in BAR_FIELD_NAMES}
    return built | fields


def assert_code_ok(db: Session, code: str) -> str:
    c = code.strip()
    if not CODE_PATTERN.match(c):
        raise ValueError("标识 code 须为 ASCII：字母或下划线开头，仅含字母数字下划线，长度 1～64")
    if c.upper() in reserved_codes(db):
        raise ValueError("code 与内置指标或行情字段名冲突，请换一个")
    return c


def try_eval_on_symbol(
    db: Session,
    expr: str,
    ts_code: str,
    *,
    trade_date: Optional[date] = None,
    warmup_days: int = 240,
    sample_tail: int = 5,
) -> dict[str, Any]:
    code = ts_code.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        return {"ok": False, "message": f"未找到标的 {code}", "sample_rows": [], "error_detail": None}

    allowed = allowed_variable_names(db)
    try:
        tree = parse_and_validate_expr(expr, allowed)
    except ValueError as e:
        return {"ok": False, "message": str(e), "sample_rows": [], "error_detail": str(e)}

    end = date.today()
    start = end - timedelta(days=warmup_days)
    bars = (
        db.query(BarDaily)
        .filter(BarDaily.symbol_id == sym.id, BarDaily.trade_date >= start, BarDaily.trade_date <= end)
        .order_by(BarDaily.trade_date.asc())
        .all()
    )
    if len(bars) < 30:
        return {
            "ok": False,
            "message": f"{code} 在近期可用日线不足 30 根，无法可靠试算（请先同步数据）",
            "sample_rows": [],
            "error_detail": None,
        }

    ind_by_date = compute_indicators(bars, start_date=bars[0].trade_date)
    all_dates = [b.trade_date for b in bars]

    if trade_date is not None:
        if trade_date not in all_dates:
            return {
                "ok": False,
                "message": f"指定日 {trade_date} 无 {code} 的日线",
                "sample_rows": [],
                "error_detail": None,
            }
        try_dates = [trade_date]
    else:
        try_dates = all_dates[-sample_tail:]

    sample_rows: list[dict[str, Any]] = []
    for td in try_dates:
        bar = next(b for b in bars if b.trade_date == td)
        row = ind_by_date.get(td) or {}
        env: dict[str, float] = dict(row)
        env["open"] = float(bar.open)
        env["high"] = float(bar.high)
        env["low"] = float(bar.low)
        env["close"] = float(bar.close)
        env["volume"] = float(bar.volume)
        env["amount"] = float(bar.amount)
        env["turnover_rate"] = float(bar.turnover_rate) if bar.turnover_rate is not None else 0.0
        try:
            val = eval_expression(tree, env)
            if math.isnan(val) or math.isinf(val):
                sample_rows.append({"trade_date": td.isoformat(), "value": None, "error": "非有限数值"})
            else:
                sample_rows.append({"trade_date": td.isoformat(), "value": val, "error": None})
        except ValueError as e:
            sample_rows.append({"trade_date": td.isoformat(), "value": None, "error": str(e)})

    errors = [r for r in sample_rows if r.get("error")]
    ok = len(errors) == 0
    return {
        "ok": ok,
        "message": "试算通过" if ok else f"部分日期试算失败（{len(errors)}/{len(sample_rows)}）",
        "sample_rows": sample_rows,
        "error_detail": errors[0]["error"] if errors else None,
    }
