"""
条件选股：基于用户自定义指标（DSL 或旧版 expr）在指定交易日筛选标的。

口径：与 K 线副图一致，默认使用**前复权（qfq）**日线；全市场扫描时分批查询以控制 SQL 次数。
同一指标在「条件选股」「K 线副图」「回测」三处数值可直接对齐。

扫描逻辑：
  1. 查出指定日期有日线的全部个股（最多 max_scan 只）
  2. 每批 _CHUNK=450 只股票，一次性拉取「交易日-预热天数 ~ 交易日」的日线，按 qfq 批量复权
  3. 对每只股票用指标引擎算最后一根 K 线（即交易日当天）的指标值
  4. 与阈值比较，满足条件的加入结果列表
  5. 结果按指标值降序排列
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AdjFactorDaily, BarDaily, UserIndicator
from app.services.adj import AdjType, apply_adj, get_latest_factor
from app.services.custom_indicator_eval import eval_expression, parse_and_validate_expr
from app.services.custom_indicator_service import allowed_variable_names
from app.services.indicator_compute import compute_indicators
from app.services.strategy_engine import (
    compile_strategy,
    eval_strategy_on_bars,
    legacy_to_logic,
)
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition

# 支持的比较运算符集合
_COMPARE_OPS = frozenset({"gt", "lt", "eq", "gte", "le", "ne"})
# 每批查询的股票数（避免 SQL IN 子句过长导致性能问题）
_CHUNK = 450
# 全市场最多扫描只数上限（防止超时）
_MAX_SCAN = 6000
# 向前预热天数：保证有足够历史数据计算 MA60、BOLL 等需要 60 根以上 K 线的指标
_WARMUP_DAYS = 400


def _cmp(x: float, op: str, thr: float) -> bool:
    """对单个数值执行比较操作：x op thr，如 x > thr（op="gt"）。"""
    if op == "gt":
        return x > thr
    if op == "gte":
        return x >= thr
    if op == "lt":
        return x < thr
    if op == "le":
        return x <= thr
    if op == "eq":
        return math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)  # 浮点等值用近似比较
    if op == "ne":
        return not math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)
    return False


def _load_adj_maps_batch(
    db: Session, symbol_ids: list[int]
) -> dict[int, dict[date, float]]:
    """一次性加载多只股票的全部复权因子，返回 {symbol_id: {trade_date: factor}}。

    相比逐只调 build_adj_map，这里用 IN 批查，6000 只股票的扫描能把 SELECT 次数从
    6000 压到 ~14（按 _CHUNK=450 分批）。
    """
    if not symbol_ids:
        return {}
    rows = (
        db.query(AdjFactorDaily)
        .filter(AdjFactorDaily.symbol_id.in_(symbol_ids))
        .all()
    )
    out: dict[int, dict[date, float]] = defaultdict(dict)
    for r in rows:
        out[r.symbol_id][r.trade_date] = float(r.adj_factor)
    return dict(out)


def _to_adj_bar(b: BarDaily, adj: AdjType, adj_map: dict[date, float], latest_factor: float) -> Any:
    """把 BarDaily 复权后转成 SimpleNamespace（不改写原 ORM 对象，避免误触发 UPDATE）。

    对齐 load_adjusted_bar_sequence 的口径：OHLC 按 adj 换算，volume/amount/turnover_rate 保持原值。
    """
    return SimpleNamespace(
        trade_date=b.trade_date,
        open=apply_adj(float(b.open), b.trade_date, adj, adj_map, latest_factor),
        high=apply_adj(float(b.high), b.trade_date, adj, adj_map, latest_factor),
        low=apply_adj(float(b.low), b.trade_date, adj, adj_map, latest_factor),
        close=apply_adj(float(b.close), b.trade_date, adj, adj_map, latest_factor),
        volume=float(b.volume),
        amount=float(b.amount),
        turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
    )


def _load_bars_grouped(
    db: Session,
    symbol_ids: list[int],
    start: date,
    end: date,
    adj_mode: Literal["none", "qfq"] = "qfq",
) -> dict[int, list[Any]]:
    """批量加载多只股票在 [start, end] 日期范围内的日线，按 symbol_id 分组返回。

    adj_mode:
      - "qfq"（默认）：与 K 线副图、自定义指标副图同口径的前复权，返回 SimpleNamespace 列表。
      - "none"：原始未复权 BarDaily，用于个别需要名义价/成交家数口径的场景。

    用于分批查询：一次 SQL 拿一批股票的全部日线，避免 N+1 查询。
    返回 {symbol_id: [bar, ...]}，按 trade_date 升序。
    """
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
    grouped_raw: dict[int, list[BarDaily]] = defaultdict(list)
    for b in rows:
        grouped_raw[b.symbol_id].append(b)

    if adj_mode == "none":
        return dict(grouped_raw)

    # qfq：批量取该 chunk 的全部复权因子，按股票换算
    adj_maps = _load_adj_maps_batch(db, symbol_ids)
    out: dict[int, list[Any]] = {}
    for sid, raw_bars in grouped_raw.items():
        adj_map = adj_maps.get(sid, {})
        latest_factor = get_latest_factor(adj_map)
        if not adj_map:
            # 没有复权因子（如指数、停牌已退市标的）→ 直接返回原始 bars，避免空结果
            out[sid] = list(raw_bars)
            continue
        out[sid] = [_to_adj_bar(b, "qfq", adj_map, latest_factor) for b in raw_bars]
    return out


def _stocks_with_bar_on(db: Session, d: date) -> list[dict[str, Any]]:
    """查询在交易日 d 有日线数据的全部个股（仅 asset_type='stock'）。

    同时用子查询取昨日收盘价（prev_close），用于计算当日涨跌幅。
    返回字段：symbol_id, ts_code, name（来自 instrument_meta），close, prev_close。
    """
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
    """对 DSL 指标计算最后一根 K 线（当日）的子线值。

    Args:
        parsed: parse_and_validate_definition 返回的已解析指标定义。
        sub_key: 要取值的子线 key。
        bars: 该股历史日线（含预热数据）。

    Returns:
        当日指标值，或 None（数据不足/计算失败/nan/inf）。
    """
    if len(bars) < 5:  # 太少的 K 线无法计算有意义的指标
        return None
    try:
        series = compute_definition_series(parsed, bars)
    except ValueError:
        return None
    seq = series.get(sub_key)
    if not seq:
        return None
    v = seq[-1]  # 最后一根 = 交易日当天
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    return float(v)


def _value_legacy_at_last_bar(db: Session, expr: str, bars: list[BarDaily]) -> Optional[float]:
    """对旧版单行表达式计算最后一根 K 线（当日）的值。

    Args:
        expr: 旧版表达式字符串，如 "(close - MA20) / MA20 * 100"。
        bars: 该股历史日线（含预热数据）。

    Returns:
        当日表达式值，或 None。
    """
    allowed = allowed_variable_names(db)
    try:
        tree = parse_and_validate_expr(expr, allowed)
    except ValueError:
        return None
    if not bars:
        return None
    # 计算内置指标，然后将最后一日的值和 OHLCV 组装为环境变量
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
    # 新路径：多条件 logic（结构见 app/schemas.StrategyLogic）
    logic: Optional[dict] = None,
    # 老路径：单条件入参；仅当 logic 为空时生效
    user_indicator_id: Optional[int] = None,
    sub_key: Optional[str] = None,
    compare_op: str = "gt",
    threshold: float = 0.0,
    max_scan: int = _MAX_SCAN,
) -> dict[str, Any]:
    """在指定交易日对全市场（最多 max_scan 只）做条件选股扫描。

    支持两种入参方式（由 logic 是否为空决定）：
      - 多条件：传 logic（条件 + 组 + combiner），由 strategy_engine 统一求值
      - 单条件（老）：传 user_indicator_id + sub_key + compare_op + threshold
        内部转为 1 条件 logic 走同一条路径

    Args:
        trade_date: 要扫描的截面交易日。
        logic: 多条件策略 dict；优先使用，None 时走老路径。
        user_indicator_id / sub_key / compare_op / threshold: 老单条件参数。
        max_scan: 最多扫描只数（受 _MAX_SCAN=6000 上限约束）。

    Returns:
        {trade_date, scanned, matched, items, logic, is_multi, adj_mode, ...}
        items 每条含 indicator_value（主条件值）+ indicator_values（所有条件值字典）

    Raises:
        ValueError: logic 结构无效、指标不存在、子线无效等。
    """
    # ---- 统一路径：把所有入参折合为 logic ----
    is_multi = logic is not None
    if logic is None:
        if user_indicator_id is None:
            raise ValueError("必须提供 logic 或 user_indicator_id")
        if compare_op not in _COMPARE_OPS:
            raise ValueError(f"compare_op 须为 {_COMPARE_OPS}")
        logic = legacy_to_logic(user_indicator_id, sub_key, compare_op, threshold)

    compiled = compile_strategy(db, logic)

    # ---- 查有日线的个股 ----
    stocks = _stocks_with_bar_on(db, trade_date)
    if not stocks:
        out = {
            "trade_date": trade_date.isoformat(),
            "scanned": 0,
            "matched": 0,
            "note": "该日无任何个股日线，请先同步",
            "items": [],
            "adj_mode": "qfq",
            "logic": logic,
            "is_multi": is_multi,
        }
        if not is_multi:
            out.update({
                "user_indicator_id": user_indicator_id,
                "sub_key": logic["conditions"][0].get("sub_key") or "",
                "compare_op": compare_op,
                "threshold": threshold,
            })
        return out

    # 截断到 max_scan（防超时）
    stocks = stocks[: max(1, min(max_scan, _MAX_SCAN))]
    # 预热起始日期：向前 _WARMUP_DAYS 天，保证 MA60/BOLL 有足够历史数据
    start = trade_date - timedelta(days=_WARMUP_DAYS)

    items_out: list[dict[str, Any]] = []
    scanned = 0

    # 分批处理：每次取 _CHUNK 只股票，批量加载日线后逐一计算
    for i in range(0, len(stocks), _CHUNK):
        chunk = stocks[i : i + _CHUNK]
        ids = [int(r["symbol_id"]) for r in chunk]
        grouped = _load_bars_grouped(db, ids, start, trade_date)
        for r in chunk:
            sid = int(r["symbol_id"])
            scanned += 1
            bars = grouped.get(sid, [])
            # 跳过：该股在指定日无数据（停牌等情况）
            if not bars or bars[-1].trade_date != trade_date:
                continue

            hit, primary_val, cond_values = eval_strategy_on_bars(compiled, bars)
            if not hit or primary_val is None:
                continue

            # 计算涨跌幅（用原始 close 口径，stocks 里的是未复权 close）
            prev = r.get("prev_close")
            close = float(r["close"])
            pct: Optional[float] = None
            if prev is not None and float(prev) > 0:
                pct = round((close - float(prev)) / float(prev) * 100.0, 3)
            items_out.append({
                "ts_code": r["ts_code"],
                "name": r.get("name"),
                "close": close,
                "pct_change": pct,
                "indicator_value": primary_val,
                "indicator_values": {str(k): v for k, v in cond_values.items() if v is not None},
                "adj_mode": "qfq",
            })

    # 按主条件值从大到小排序
    items_out.sort(key=lambda x: x["indicator_value"], reverse=True)

    out: dict[str, Any] = {
        "trade_date": trade_date.isoformat(),
        "scanned": scanned,
        "matched": len(items_out),
        "items": items_out,
        "adj_mode": "qfq",
        "logic": logic,
        "is_multi": is_multi,
    }
    # 老路径：保留旧字段回显，兼容现有 API / 前端
    if not is_multi:
        out.update({
            "user_indicator_id": user_indicator_id,
            "sub_key": logic["conditions"][0].get("sub_key") or "",
            "compare_op": compare_op,
            "threshold": threshold,
        })
    return out
