"""统一策略求值引擎：把多条件 + 组 + combiner 的策略 logic 编译为可直接在 bars 上运行的对象。

两阶段：

1. **编译（compile_strategy）**：
   - 对每个 user_indicator_id 读 UserIndicator、解析一次 DSL 或 legacy expr，缓存在 indicators dict
   - 与 bars 无关，可在策略保存后立即完成
   - 命中缓存，同一指标不同条件共用一次解析结果

2. **求值**：
   - `eval_strategy_on_bars(compiled, bars)` → 单日（最后一根 K 线）命中判定，供选股
   - `eval_strategy_on_series(compiled, bars, start, end)` → 整段序列每日命中判定，供回测
   - 每只股票每个指标只调一次 `compute_definition_series`，多条件共用序列
   - 组 = 组内条件 AND；combiner 通过 `services/combiner.eval_combiner` 递归求值

返回约定：
  - `(hit, primary_val, cond_values)` 三元组
  - primary_val 取主条件对应指标值，供排序
  - 缺数据 / 计算失败 → 条件布尔 = False（即"不满足"），符合既有单条件逻辑
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import UserIndicator
from app.services.combiner import eval_combiner, validate_combiner
from app.services.custom_indicator_eval import eval_expression, parse_and_validate_expr
from app.services.custom_indicator_service import allowed_variable_names
from app.services.indicator_compute import compute_indicators
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition

# 与 screening_runner._COMPARE_OPS 对齐；此处做本地拷贝避免循环依赖。
_COMPARE_OPS = frozenset({"gt", "lt", "eq", "gte", "le", "ne"})

# legacy expr 指标不存在 sub_key 的概念，用此占位符标记
LEGACY_SUB_KEY = "__expr__"


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class CompiledIndicator:
    """编译后的单个指标：DSL 形式已 parsed_def，legacy 形式已 parsed_tree。"""
    user_indicator_id: int
    is_dsl: bool
    display_name: str
    code: str
    parsed_def: Optional[Any] = None     # DSL 解析结果
    parsed_tree: Optional[Any] = None    # legacy expr 的 AST
    valid_sub_keys: frozenset = field(default_factory=frozenset)  # DSL 可用子线集合（含辅助）


@dataclass
class CompiledCondition:
    cond_id: int
    user_indicator_id: int
    sub_key: str                 # DSL 用；legacy 用 LEGACY_SUB_KEY 占位
    compare_op: str
    threshold: float


@dataclass
class CompiledStrategy:
    conditions: List[CompiledCondition]
    groups: List[Tuple[str, List[int]]]        # [(group_id, [cond_id, ...])]
    combiner_tree: Dict[str, Any]
    primary_cond_id: int
    indicators: Dict[int, CompiledIndicator]   # user_indicator_id → 编译结果


# ─────────────────────────────────────────────────────────────
# 编译
# ─────────────────────────────────────────────────────────────

def compile_strategy(db: Session, logic: Dict[str, Any]) -> CompiledStrategy:
    """把一份 logic dict（来自 StrategyLogic.model_dump()）编译为可求值对象。

    Args:
        db: SQLAlchemy session，用于加载 UserIndicator。
        logic: 结构见 StrategyLogic：conditions / groups / combiner / primary_condition_id。

    Raises:
        ValueError: 结构不合法、指标不存在、子线无效等。
    """
    conds_raw = logic.get("conditions") or []
    groups_raw = logic.get("groups") or []
    combiner = logic.get("combiner") or {}
    primary_cond_id = logic.get("primary_condition_id")

    if not conds_raw:
        raise ValueError("策略必须至少包含 1 个条件")
    if not groups_raw:
        raise ValueError("策略必须至少包含 1 个组")

    # ---- 解析各指标（按 user_indicator_id 去重缓存）----
    uid_set = {int(c["user_indicator_id"]) for c in conds_raw}
    indicators: Dict[int, CompiledIndicator] = {}
    for uid in uid_set:
        row = db.query(UserIndicator).filter(UserIndicator.id == uid).one_or_none()
        if not row:
            raise ValueError(f"自定义指标 id={uid} 不存在")
        is_dsl = bool(row.definition_json and str(row.definition_json).strip())
        ci = CompiledIndicator(
            user_indicator_id=uid,
            is_dsl=is_dsl,
            display_name=row.display_name,
            code=row.code,
        )
        if is_dsl:
            try:
                ci.parsed_def = parse_and_validate_definition(db, json.loads(row.definition_json))
            except (ValueError, json.JSONDecodeError) as e:
                raise ValueError(f"指标 {row.code} 定义无效: {e}") from e
            ci.valid_sub_keys = frozenset(str(s.get("key")) for s in ci.parsed_def.sub_indicators)
        else:
            expr_s = (row.expr or "").strip()
            if not expr_s:
                raise ValueError(f"指标 {row.code} 既无 DSL 也无 expr，无法使用")
            try:
                ci.parsed_tree = parse_and_validate_expr(expr_s, allowed_variable_names(db))
            except ValueError as e:
                raise ValueError(f"指标 {row.code} 表达式无效: {e}") from e
        indicators[uid] = ci

    # ---- 编译条件 ----
    conditions: List[CompiledCondition] = []
    cond_id_set: set = set()
    for raw in conds_raw:
        cid = int(raw["id"])
        if cid in cond_id_set:
            raise ValueError(f"条件 id={cid} 重复")
        cond_id_set.add(cid)
        op = str(raw.get("compare_op", "gt"))
        if op not in _COMPARE_OPS:
            raise ValueError(f"compare_op 非法: {op!r}")
        uid = int(raw["user_indicator_id"])
        ind = indicators[uid]
        sub_key = (raw.get("sub_key") or "").strip()
        if ind.is_dsl:
            if not sub_key:
                # 未指定时默认取第一条参与选股的子线
                usable = sorted(
                    str(s.get("key")) for s in ind.parsed_def.sub_indicators
                    if s.get("use_in_screening") and not s.get("auxiliary_only")
                )
                if not usable:
                    raise ValueError(f"指标 {ind.code} 无可用于选股的子线（需勾选「选股/回测」且非仅辅助）")
                sub_key = usable[0]
            elif sub_key not in ind.valid_sub_keys:
                raise ValueError(f"指标 {ind.code} 不存在子线 {sub_key!r}")
        else:
            sub_key = LEGACY_SUB_KEY  # legacy 指标无子线
        conditions.append(CompiledCondition(
            cond_id=cid,
            user_indicator_id=uid,
            sub_key=sub_key,
            compare_op=op,
            threshold=float(raw.get("threshold", 0.0)),
        ))

    # ---- 编译组 ----
    groups: List[Tuple[str, List[int]]] = []
    group_id_set: set = set()
    for g in groups_raw:
        gid = str(g["id"])
        if gid in group_id_set:
            raise ValueError(f"组 id={gid!r} 重复")
        group_id_set.add(gid)
        cids = [int(x) for x in (g.get("condition_ids") or [])]
        if not cids:
            raise ValueError(f"组 {gid} 至少含 1 个条件")
        for cid in cids:
            if cid not in cond_id_set:
                raise ValueError(f"组 {gid} 引用了未定义的条件 id={cid}")
        groups.append((gid, cids))

    # ---- 校验 combiner 树 ----
    validate_combiner(combiner, [gid for gid, _ in groups])

    # ---- 主条件 ----
    if primary_cond_id is None or int(primary_cond_id) not in cond_id_set:
        raise ValueError(f"primary_condition_id={primary_cond_id} 不在 conditions 中")

    return CompiledStrategy(
        conditions=conditions,
        groups=groups,
        combiner_tree=combiner,
        primary_cond_id=int(primary_cond_id),
        indicators=indicators,
    )


# ─────────────────────────────────────────────────────────────
# 求值（单 bar，选股用）
# ─────────────────────────────────────────────────────────────

def _cmp(x: float, op: str, thr: float) -> bool:
    if op == "gt": return x > thr
    if op == "gte": return x >= thr
    if op == "lt": return x < thr
    if op == "le": return x <= thr
    if op == "eq": return math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)
    if op == "ne": return not math.isclose(x, thr, rel_tol=0, abs_tol=1e-9)
    return False


def _is_bad(v: Any) -> bool:
    return v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _build_legacy_env_at(bars: List[Any], i: int, ind_by_date: Dict[date, Dict[str, float]]) -> Dict[str, float]:
    """组装 legacy expr 的求值环境：内置指标值 + OHLCV + turnover_rate。"""
    bar = bars[i]
    row = ind_by_date.get(bar.trade_date) or {}
    env: Dict[str, float] = dict(row)
    env["open"] = float(bar.open)
    env["high"] = float(bar.high)
    env["low"] = float(bar.low)
    env["close"] = float(bar.close)
    env["volume"] = float(bar.volume)
    env["amount"] = float(bar.amount)
    tr = getattr(bar, "turnover_rate", None)
    env["turnover_rate"] = float(tr) if tr is not None else 0.0
    return env


def eval_strategy_on_bars(
    compiled: CompiledStrategy,
    bars: List[Any],
) -> Tuple[bool, Optional[float], Dict[int, Optional[float]]]:
    """在 bars 最后一根 K 线上求策略命中。

    Args:
        compiled: compile_strategy 的返回。
        bars: 按日期升序的 K 线列表（含预热期）；以最后一根作为截面日。

    Returns:
        (hit, primary_val, cond_values)
        - hit: 策略是否命中
        - primary_val: 主条件的指标值（None 表示缺数据，通常意味着不应参与排序）
        - cond_values: {cond_id: 指标值 or None}，便于调试
    """
    if not bars:
        return False, None, {c.cond_id: None for c in compiled.conditions}

    # 预计算 DSL 序列（按指标聚合，每个指标只算一次）
    dsl_series: Dict[int, Dict[str, List[Optional[float]]]] = {}
    has_legacy = False
    for uid, ind in compiled.indicators.items():
        if ind.is_dsl:
            try:
                dsl_series[uid] = compute_definition_series(ind.parsed_def, bars)
            except (ValueError, Exception):  # noqa: BLE001
                dsl_series[uid] = {}
        else:
            has_legacy = True

    # legacy 环境（最后一根 bar）只需算一次
    legacy_env: Optional[Dict[str, float]] = None
    if has_legacy:
        ind_by_date = compute_indicators(bars, start_date=bars[0].trade_date)
        legacy_env = _build_legacy_env_at(bars, len(bars) - 1, ind_by_date)

    cond_values: Dict[int, Optional[float]] = {}
    cond_bools: Dict[int, bool] = {}
    for c in compiled.conditions:
        ind = compiled.indicators[c.user_indicator_id]
        if ind.is_dsl:
            seq = dsl_series.get(c.user_indicator_id, {}).get(c.sub_key, [])
            v = seq[-1] if seq else None
        else:
            try:
                v = eval_expression(ind.parsed_tree, legacy_env or {})
            except (ValueError, Exception):  # noqa: BLE001
                v = None
        if _is_bad(v):
            cond_values[c.cond_id] = None
            cond_bools[c.cond_id] = False
        else:
            fv = float(v)
            cond_values[c.cond_id] = fv
            cond_bools[c.cond_id] = _cmp(fv, c.compare_op, c.threshold)

    group_bools: Dict[str, bool] = {}
    for gid, cids in compiled.groups:
        group_bools[gid] = all(cond_bools.get(cid, False) for cid in cids)

    hit = eval_combiner(compiled.combiner_tree, group_bools)
    primary_val = cond_values.get(compiled.primary_cond_id)
    return hit, primary_val, cond_values


# ─────────────────────────────────────────────────────────────
# 求值（序列，回测用）
# ─────────────────────────────────────────────────────────────

def eval_strategy_on_series(
    compiled: CompiledStrategy,
    bars: List[Any],
    start: date,
    end: date,
) -> Dict[date, Tuple[bool, Optional[float]]]:
    """在 bars 整段上求每个交易日的策略命中 + 主条件值。

    Args:
        compiled: compile_strategy 的返回。
        bars: 按日期升序的 K 线列表（含预热期 + 回测窗口）。
        start / end: 返回的 trade_date 必须落在 [start, end] 内（闭区间）。

    Returns:
        {trade_date: (hit, primary_val)}；缺数据或主值 NaN 的交易日不出现。
    """
    if not bars:
        return {}

    n = len(bars)

    # 每个 DSL 指标只算一次整段序列
    dsl_series: Dict[int, Dict[str, List[Optional[float]]]] = {}
    has_legacy = False
    for uid, ind in compiled.indicators.items():
        if ind.is_dsl:
            try:
                dsl_series[uid] = compute_definition_series(ind.parsed_def, bars)
            except (ValueError, Exception):  # noqa: BLE001
                dsl_series[uid] = {}
        else:
            has_legacy = True

    # legacy：对每天都要算一次环境；compute_indicators 提前算好全段
    legacy_ind_by_date: Optional[Dict[date, Dict[str, float]]] = None
    if has_legacy:
        legacy_ind_by_date = compute_indicators(bars, start_date=bars[0].trade_date)

    # 对每个条件预先算出长度 n 的 (value, bool) 两列
    cond_value_arr: Dict[int, List[Optional[float]]] = {}
    cond_bool_arr: Dict[int, List[bool]] = {}
    for c in compiled.conditions:
        ind = compiled.indicators[c.user_indicator_id]
        vals: List[Optional[float]] = [None] * n
        bools: List[bool] = [False] * n
        if ind.is_dsl:
            seq = dsl_series.get(c.user_indicator_id, {}).get(c.sub_key, [])
            for i in range(min(n, len(seq))):
                v = seq[i]
                if _is_bad(v):
                    continue
                fv = float(v)
                vals[i] = fv
                bools[i] = _cmp(fv, c.compare_op, c.threshold)
        else:
            for i in range(n):
                env = _build_legacy_env_at(bars, i, legacy_ind_by_date or {})
                try:
                    v = eval_expression(ind.parsed_tree, env)
                except (ValueError, Exception):  # noqa: BLE001
                    v = None
                if _is_bad(v):
                    continue
                fv = float(v)
                vals[i] = fv
                bools[i] = _cmp(fv, c.compare_op, c.threshold)
        cond_value_arr[c.cond_id] = vals
        cond_bool_arr[c.cond_id] = bools

    # 每日聚合组 + 求 combiner
    out: Dict[date, Tuple[bool, Optional[float]]] = {}
    primary_vals = cond_value_arr.get(compiled.primary_cond_id, [None] * n)
    for i in range(n):
        td = bars[i].trade_date
        if td < start or td > end:
            continue
        pv = primary_vals[i]
        if pv is None:
            continue  # 主条件值缺失的日期不参与（无法排序）
        group_bools = {
            gid: all(cond_bool_arr[cid][i] for cid in cids)
            for gid, cids in compiled.groups
        }
        hit = eval_combiner(compiled.combiner_tree, group_bools)
        out[td] = (bool(hit), float(pv))
    return out


# ─────────────────────────────────────────────────────────────
# 试算工具：给 dry-run 端点用
# ─────────────────────────────────────────────────────────────

def dry_run_on_bars(
    compiled: CompiledStrategy,
    bars: List[Any],
) -> Dict[str, Any]:
    """在 bars 最后一根 K 线上完整跑一次策略，返回详细的布尔/值分解。

    返回结构便于前端调试：
        {
            "trade_date": "2024-01-10",
            "hit": true,
            "primary_value": 12.3,
            "conditions": [
                {"cond_id": 1, "user_indicator_id": 7, "code": "my_ma", "display_name": "...",
                 "sub_key": "MA5", "compare_op": "gt", "threshold": 0,
                 "indicator_value": 1.23, "satisfied": true},
                ...
            ],
            "groups": [{"group_id": "G1", "satisfied": true}, ...],
        }
    """
    if not bars:
        return {
            "trade_date": None,
            "hit": False,
            "primary_value": None,
            "conditions": [],
            "groups": [],
            "note": "无 K 线数据，无法试算",
        }

    hit, primary_val, cond_values = eval_strategy_on_bars(compiled, bars)

    # 复算组布尔（eval_strategy_on_bars 已经做过但没返回，这里从 cond_values 再推一次，便于返回详情）
    cond_bool_map: Dict[int, bool] = {}
    for c in compiled.conditions:
        v = cond_values.get(c.cond_id)
        cond_bool_map[c.cond_id] = False if v is None else _cmp(v, c.compare_op, c.threshold)

    conds_out: List[Dict[str, Any]] = []
    for c in compiled.conditions:
        ind = compiled.indicators[c.user_indicator_id]
        conds_out.append({
            "cond_id": c.cond_id,
            "user_indicator_id": c.user_indicator_id,
            "code": ind.code,
            "display_name": ind.display_name,
            "sub_key": c.sub_key if ind.is_dsl else None,
            "compare_op": c.compare_op,
            "threshold": c.threshold,
            "indicator_value": cond_values.get(c.cond_id),
            "satisfied": cond_bool_map[c.cond_id],
        })

    groups_out: List[Dict[str, Any]] = []
    for gid, cids in compiled.groups:
        groups_out.append({
            "group_id": gid,
            "condition_ids": list(cids),
            "satisfied": all(cond_bool_map.get(cid, False) for cid in cids),
        })

    return {
        "trade_date": bars[-1].trade_date.isoformat() if hasattr(bars[-1].trade_date, "isoformat") else str(bars[-1].trade_date),
        "hit": bool(hit),
        "primary_value": primary_val,
        "conditions": conds_out,
        "groups": groups_out,
    }


# ─────────────────────────────────────────────────────────────
# 老单条件 → 新 logic dict 适配（向后兼容）
# ─────────────────────────────────────────────────────────────

def legacy_to_logic(
    user_indicator_id: int,
    sub_key: Optional[str],
    compare_op: str,
    threshold: float,
) -> Dict[str, Any]:
    """把老单条件（user_indicator_id + sub_key + compare_op + threshold）转为新 logic dict。

    输出的 logic 只含 1 个条件、1 个组、combiner 指向该组，主排序条件就是它自己。
    调用方把旧参数包进来后，内部所有代码统一走 strategy_engine.compile_strategy 求值。
    """
    return {
        "conditions": [{
            "id": 1,
            "user_indicator_id": int(user_indicator_id),
            "sub_key": (sub_key or None),
            "compare_op": compare_op,
            "threshold": float(threshold),
        }],
        "groups": [{"id": "G1", "condition_ids": [1]}],
        "combiner": {"ref": "G1"},
        "primary_condition_id": 1,
    }
