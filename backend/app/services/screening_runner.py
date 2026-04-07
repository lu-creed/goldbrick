"""条件选股：基于用户自定义指标（DSL 或旧版 expr）在指定交易日筛选标的。

口径：与指标库试算一致，使用**未复权** `bars_daily`；全市场扫描时分批查询以控制 SQL 次数。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import BarDaily, UserIndicator
from app.services.custom_indicator_eval import eval_expression, parse_and_validate_expr
from app.services.custom_indicator_service import allowed_variable_names
from app.services.indicator_compute import compute_indicators
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition

_COMPARE_OPS = frozenset({"gt", "lt", "eq", "gte", "le", "ne"})
_CHUNK = 450
_MAX_SCAN = 6000
_WARMUP_DAYS = 400


def _cmp(x: float, op: str, thr: float) -> bool:
    if op == "gt":
        return x > thr
    if op == "gte":
        return x >= thr
    if op == "lt":
        return x < thr
    if op == "le":
        return x <= thr
    if op == "eq":
        return math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)
    if op == "ne":
        return not math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)
    return False


def _load_bars_grouped(
    db: Session, symbol_ids: list[int], start: date, end: date
) -> dict[int, list[BarDaily]]:
    if not symbol_ids:
        return {}
    rows = (
        db.query(BarDaily)
        .filter(
            BarDaily.symbol_id.in_(symbol_ids),
            BarDaily.trade_date >= start,
            BarDaily.trade_date <= end,
        )
        .order_by(BarDaily.symbol_id, BarDaily.trade_date.asc())
        .all()
    )
    out: dict[int, list[BarDaily]] = defaultdict(list)
    for b in rows:
        out[b.symbol_id].append(b)
    return dict(out)


def _stocks_with_bar_on(db: Session, d: date) -> list[dict[str, Any]]:
    sql = text("""
        SELECT s.id AS symbol_id, s.ts_code, m.name AS name,
               CAST(b.close AS REAL) AS close,
               (
                   SELECT CAST(b2.close AS REAL)
                   FROM bars_daily b2
                   WHERE b2.symbol_id = b.symbol_id AND b2.trade_date < :d
                   ORDER BY b2.trade_date DESC
                   LIMIT 1
               ) AS prev_close
        FROM bars_daily b
        JOIN symbols s ON s.id = b.symbol_id
        JOIN instrument_meta m ON m.ts_code = s.ts_code AND m.asset_type = 'stock'
        WHERE b.trade_date = :d
    """)
    return [dict(r._mapping) for r in db.execute(sql, {"d": d}).fetchall()]


def _value_dsl_at_last_bar(
    db: Session,
    parsed: Any,
    sub_key: str,
    bars: list[BarDaily],
) -> Optional[float]:
    if len(bars) < 5:
        return None
    try:
        series = compute_definition_series(parsed, bars)
    except ValueError:
        return None
    seq = series.get(sub_key)
    if not seq:
        return None
    v = seq[-1]
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return float(v)


def _value_legacy_at_last_bar(db: Session, expr: str, bars: list[BarDaily]) -> Optional[float]:
    allowed = allowed_variable_names(db)
    try:
        tree = parse_and_validate_expr(expr, allowed)
    except ValueError:
        return None
    if not bars:
        return None
    ind_by_date = compute_indicators(bars, start_date=bars[0].trade_date)
    td = bars[-1].trade_date
    bar = bars[-1]
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
        v = eval_expression(tree, env)
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    except ValueError:
        return None


def run_screen(
    db: Session,
    *,
    trade_date: date,
    user_indicator_id: int,
    sub_key: str,
    compare_op: str,
    threshold: float,
    max_scan: int = _MAX_SCAN,
) -> dict[str, Any]:
    if compare_op not in _COMPARE_OPS:
        raise ValueError(f"compare_op 须为 {_COMPARE_OPS}")
    ui = db.query(UserIndicator).filter(UserIndicator.id == user_indicator_id).one_or_none()
    if not ui:
        raise ValueError("自定义指标不存在")

    stocks = _stocks_with_bar_on(db, trade_date)
    if not stocks:
        return {
            "trade_date": trade_date.isoformat(),
            "scanned": 0,
            "matched": 0,
            "note": "该日无任何个股日线，请先同步",
            "items": [],
        }

    stocks = stocks[: max(1, min(max_scan, _MAX_SCAN))]
    start = trade_date - timedelta(days=_WARMUP_DAYS)

    is_dsl = bool(ui.definition_json and str(ui.definition_json).strip())
    parsed = None
    expr_s = (ui.expr or "").strip()
    if is_dsl:
        try:
            parsed = parse_and_validate_definition(db, json.loads(ui.definition_json))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"指标定义无效: {e}") from e
        sk_valid = {str(s.get("key")) for s in parsed.sub_indicators if s.get("use_in_screening") and not s.get("auxiliary_only")}
        sk_in = (sub_key or "").strip()
        if not sk_valid:
            raise ValueError("该指标没有可用于选股的子线（请勾选子线「选股/回测」且非仅辅助）")
        if not sk_in:
            sub_key = sorted(sk_valid)[0]
        elif sk_in not in sk_valid:
            raise ValueError(f"子线 {sk_in} 未参与选股或仅为辅助线")
    else:
        if not expr_s:
            raise ValueError("该指标无可用表达式")
        sub_key = "(expr)"

    items_out: list[dict[str, Any]] = []
    scanned = 0

    for i in range(0, len(stocks), _CHUNK):
        chunk = stocks[i : i + _CHUNK]
        ids = [int(r["symbol_id"]) for r in chunk]
        grouped = _load_bars_grouped(db, ids, start, trade_date)
        for r in chunk:
            sid = int(r["symbol_id"])
            scanned += 1
            bars = grouped.get(sid, [])
            if not bars or bars[-1].trade_date != trade_date:
                continue
            if is_dsl:
                val = _value_dsl_at_last_bar(db, parsed, sub_key, bars)
            else:
                val = _value_legacy_at_last_bar(db, expr_s, bars)
            if val is None:
                continue
            if not _cmp(val, compare_op, threshold):
                continue
            prev = r.get("prev_close")
            close = float(r["close"])
            pct: Optional[float] = None
            if prev is not None and float(prev) > 0:
                pct = round((close - float(prev)) / float(prev) * 100.0, 3)
            items_out.append(
                {
                    "ts_code": r["ts_code"],
                    "name": r.get("name"),
                    "close": close,
                    "pct_change": pct,
                    "indicator_value": val,
                }
            )

    items_out.sort(key=lambda x: x["indicator_value"], reverse=True)
    return {
        "trade_date": trade_date.isoformat(),
        "user_indicator_id": user_indicator_id,
        "sub_key": sub_key,
        "compare_op": compare_op,
        "threshold": threshold,
        "scanned": scanned,
        "matched": len(items_out),
        "items": items_out,
    }
