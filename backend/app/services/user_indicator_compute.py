"""
用户指标 DSL 求值引擎：按子线拓扑序、按 bar 递推计算各子线的逐日序列。

核心函数调用链：
  compute_definition_series（主入口）
    → _topo_order（子线拓扑排序，确定计算顺序）
    → build_builtin_series（提前计算所有内置指标序列）
    → _eval_formula（递归对每根 bar 上的公式节点求值）
      → apply_fetch（根据取数方式从序列中取值：current/prev_n/range）

设计要点：
  - 拓扑顺序：子线 B 的公式引用了子线 A → A 先算。用 DFS 拓扑排序实现。
  - 初始值：用户可设置 initial_value，在第一根 bar 计算失败时作为兜底值。
  - 诊断：试算时传入 diag 列表，_eval_formula 会将失败原因（如除数为0）记录进去。
  - 复权对齐：load_adjusted_bar_sequence 提供与 K 线接口一致的复权 bar 序列，
    确保副图指标与 K 线颜色/位置一致。
"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

from sqlalchemy.orm import Session

from app.models import BarDaily, Symbol, UserIndicator
from app.services.adj import AdjType, apply_adj, build_adj_map, get_latest_factor
from app.services.indicator_compute import compute_indicators
from app.services.user_indicator_dsl import UserIndicatorDefinitionParsed, parse_and_validate_definition, parse_param_defaults


def _param_int(param_vals: dict[str, str], name: str) -> int:
    """从参数字典中取整数值，最小值为 1（参数不存在或非法时默认 1）。"""
    raw = param_vals.get(name, "0")
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        n = 0
    return max(1, n) if n >= 1 else 1


def _float_initial(s: Optional[str]) -> Optional[float]:
    """将 initial_value 字符串转为 float；空字符串或 None 返回 None。"""
    if s is None or str(s).strip() == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _td_str(bars: Sequence[Any], i: int) -> Optional[str]:
    """获取第 i 根 bar 的交易日字符串（用于诊断消息）。"""
    if 0 <= i < len(bars):
        d = getattr(bars[i], "trade_date", None)
        if d is not None:
            return d.isoformat() if hasattr(d, "isoformat") else str(d)
    return None


def _append_diag(
    diag: Optional[list[dict[str, Any]]],
    code: str,
    *,
    bar_index: Optional[int] = None,
    trade_date: Optional[str] = None,
    sub_key: str = "",
    detail: str = "",
) -> None:
    """向诊断列表追加一条失败记录（最多 14 条，防止内存爆炸）。

    诊断条目包含：失败代码、bar 序号、交易日、子线 key 和详细说明。
    前端在试算失败时展示这些诊断信息，帮助用户定位公式错误。
    """
    if diag is None or len(diag) >= 14:
        return
    diag.append(
        {
            "code": code,
            "bar_index": bar_index,
            "trade_date": trade_date,
            "sub_key": sub_key,
            "detail": detail,
        }
    )


def _topo_order(sub_keys: set[str], subs: list[dict[str, Any]]) -> list[str]:
    """对子线做拓扑排序，返回安全的计算顺序（被依赖的先算）。

    算法：DFS 后序遍历（每个节点的所有依赖处理完后再加入结果）。
    如 B 引用 A，则排序结果中 A 在 B 之前。
    Raises:
        ValueError: 如果依赖中有环路。
    """
    # adj[sk] = {dep1, dep2, ...}：sk 需要先计算哪些子线
    adj: dict[str, set[str]] = {k: set() for k in sub_keys}

    def collect_deps(node: Any, out: set[str]) -> None:
        """递归收集公式节点中所有 ref_sibling 引用的子线 key。"""
        if not isinstance(node, dict):
            return
        op = node.get("op")
        if op == "ref_sibling":
            sk = node.get("sub_key")
            if isinstance(sk, str):
                out.add(sk)
            f = node.get("fetch")
            if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
                collect_deps(f["std_baseline"], out)
        elif op in ("sqrt", "neg"):
            collect_deps(node.get("x"), out)
        elif op in ("add", "sub", "mul", "div"):
            collect_deps(node.get("left"), out)
            collect_deps(node.get("right"), out)
        elif op == "ref_builtin":
            f = node.get("fetch")
            if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
                collect_deps(f["std_baseline"], out)

    for s in subs:
        sk = str(s.get("key"))
        deps: set[str] = set()
        collect_deps(s.get("formula"), deps)
        deps.discard(sk)  # 排除自引用
        for d0 in deps:
            if d0 in sub_keys:
                adj[sk].add(d0)

    visited: set[str] = set()
    stack: set[str] = set()
    out_list: list[str] = []

    def dfs(u: str) -> None:
        if u in stack:
            raise ValueError("子指标引用形成环路")
        if u in visited:
            return
        stack.add(u)
        for v in adj[u]:
            dfs(v)
        stack.remove(u)
        visited.add(u)
        out_list.append(u)  # 后序：所有依赖处理完后才加入

    for k in sub_keys:
        if k not in visited:
            dfs(k)
    return out_list  # 结果中靠前的子线不依赖靠后的


def _intrinsic_bar(b: Any, field: str) -> Optional[float]:
    """从单根 bar 对象中取行情字段值（OHLCV 和换手率）。"""
    if field == "close":
        return float(b.close)
    if field == "open":
        return float(b.open)
    if field == "high":
        return float(b.high)
    if field == "low":
        return float(b.low)
    if field == "volume":
        return float(b.volume)
    if field == "amount":
        return float(b.amount)
    if field == "turnover_rate":
        tr = getattr(b, "turnover_rate", None)
        return float(tr) if tr is not None else 0.0
    return None


def _eval_formula(
    node: Any,
    *,
    i: int,                                      # 当前 bar 的序号（0-based）
    bars: Sequence[Any],                          # 全部 bar 序列（含历史数据）
    builtin_rows: list[dict[str, float]],         # 内置指标序列（与 bars 等长）
    sub_values: dict[str, list[Optional[float]]], # 已计算完成的子线序列（按 topo 顺序）
    param_vals: dict[str, str],                   # 参数名 → 默认值字符串
    depth: int = 0,                               # 递归深度（防止栈溢出）
    diag: Optional[list[dict[str, Any]]] = None, # 诊断列表（试算时传入，None=不收集）
    eval_sub_key: str = "",                       # 当前正在计算的子线 key（用于诊断）
) -> Optional[float]:
    """递归对单个公式节点在第 i 根 bar 上求值。

    公式节点的各类型求值逻辑：
    - num：返回数字常量
    - param：从 param_vals 取对应参数的数值
    - intrinsic：从 bars[i] 取行情字段
    - rolling：对 bars[i-N+1..i] 的某字段做滚动统计（avg/min/max）
    - neg：对子节点取负
    - sqrt：对子节点开平方根（负数返回 None）
    - add/sub/mul/div：对左右子节点做四则运算（除数为0返回 None）
    - ref_builtin：从 builtin_rows[j] 取内置指标某子线的值，j 由 fetch 决定
    - ref_sibling：从 sub_values[sk][j] 取已算好的兄弟子线的值，j 由 fetch 决定

    Returns:
        计算结果；None 表示本 bar 无法计算（数据不足、除数为0等），
        None 会沿公式树向上传播（任何操作数为 None → 结果为 None）。
    """
    if depth > 64:
        _append_diag(diag, "NEST_TOO_DEEP", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail="公式嵌套超过上限")
        return None
    if not isinstance(node, dict):
        _append_diag(diag, "INVALID_NODE", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key)
        return None
    op = node.get("op")

    if op == "num":
        # 数字常量
        try:
            v = float(node.get("value"))
            if math.isnan(v) or math.isinf(v):
                return None
            return v
        except (TypeError, ValueError):
            return None

    if op == "param":
        # 参数引用：取参数的默认值并转为 float
        name = node.get("name")
        if not isinstance(name, str):
            return None
        raw = param_vals.get(name, "0")
        try:
            return float(raw)
        except ValueError:
            try:
                return float(int(raw))
            except ValueError:
                _append_diag(diag, "BAD_PARAM", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"参数[{name}]无法转为数值")
                return None

    if op == "intrinsic":
        # 行情字段：直接从 bars[i] 读取
        f = node.get("field")
        if not isinstance(f, str):
            return None
        if i < 0 or i >= len(bars):
            _append_diag(diag, "BAD_BAR_INDEX", bar_index=i, sub_key=eval_sub_key)
            return None
        return _intrinsic_bar(bars[i], f)

    if op == "rolling":
        # 参数化 N 日滚动统计：对 bars[i-N+1..i] 的某字段做 avg/min/max
        field = node.get("field")
        npar = node.get("n_param")
        stat = node.get("stat", "avg")
        if not isinstance(field, str) or not isinstance(npar, str):
            return None
        if stat not in ("avg", "min", "max"):
            return None
        n_win = _param_int(param_vals, npar)
        start_idx = i - n_win + 1
        if start_idx < 0:
            _append_diag(
                diag, "WINDOW_SHORT",
                bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key,
                detail=f"rolling 需要至少 {n_win} 根 K 线，当前序号 {i + 1} 不足",
            )
            return None
        xs: list[float] = []
        for j in range(start_idx, i + 1):
            v = _intrinsic_bar(bars[j], field)
            if v is None:
                return None
            xs.append(v)
        if stat == "avg":
            return sum(xs) / len(xs)
        if stat == "min":
            return min(xs)
        return max(xs)

    if op == "neg":
        x = _eval_formula(node.get("x"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        return None if x is None else -x

    if op == "sqrt":
        x = _eval_formula(node.get("x"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        if x is None:
            return None
        if x < 0:
            _append_diag(diag, "NEG_SQRT", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key)
            return None
        return math.sqrt(x)

    if op == "add":
        a = _eval_formula(node.get("left"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        b = _eval_formula(node.get("right"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        if a is None or b is None:
            _append_diag(diag, "OPERAND_MISSING", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail="加法分支缺少数值")
            return None
        return a + b

    if op == "sub":
        a = _eval_formula(node.get("left"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        b = _eval_formula(node.get("right"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        if a is None or b is None:
            return None
        return a - b

    if op == "mul":
        a = _eval_formula(node.get("left"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        b = _eval_formula(node.get("right"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        if a is None or b is None:
            return None
        return a * b

    if op == "div":
        a = _eval_formula(node.get("left"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        b = _eval_formula(node.get("right"), i=i, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)
        if a is None or b is None:
            return None
        if b == 0:
            _append_diag(diag, "DIV_ZERO", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail="除数为 0")
            return None
        return a / b

    # ---- 辅助闭包：方便 ref_builtin / ref_sibling 复用 ----

    def eval_here(formula: Any, idx: int) -> Optional[float]:
        """在当前上下文中对公式在第 idx 根 bar 上求值（供 fetch std_baseline 使用）。"""
        return _eval_formula(formula, i=idx, bars=bars, builtin_rows=builtin_rows, sub_values=sub_values, param_vals=param_vals, depth=depth + 1, diag=diag, eval_sub_key=eval_sub_key)

    def apply_fetch(fetch: Any, getter: Callable[[int], Optional[float]]) -> Optional[float]:
        """根据取数方式（fetch）调用 getter(j) 获取目标位置的值。

        - mode=current：getter(i)，取当前 bar
        - mode=prev_n：getter(i-N)，取前 N 个 bar
        - mode=range：对 [i-N+1..i] 窗口内调用 getter，再做 avg/min/max/std 聚合
        """
        if not isinstance(fetch, dict):
            return None
        mode = fetch.get("mode")
        if mode == "current":
            return getter(i)
        npar = fetch.get("n_param")
        if not isinstance(npar, str):
            return None
        n_win = _param_int(param_vals, npar)
        if mode == "prev_n":
            j = i - n_win
            if j < 0:
                _append_diag(diag, "WINDOW_SHORT", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"前 {n_win} 周期超出有效范围")
                return None
            return getter(j)
        if mode != "range":
            return None
        agg = fetch.get("range_agg")
        start = i - n_win + 1
        if start < 0:
            _append_diag(diag, "WINDOW_SHORT", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"区间需要 {n_win} 根含当日的 K 线，序号不足")
            return None
        window = range(start, i + 1)
        if agg in ("avg", "min", "max"):
            xs: list[float] = []
            for j in window:
                v = getter(j)
                if v is None:
                    _append_diag(diag, "MISSING_IN_WINDOW", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"窗口内 {_td_str(bars, j)} 取值为空")
                    return None
                xs.append(v)
            if agg == "avg":
                return sum(xs) / len(xs)
            if agg == "min":
                return min(xs)
            return max(xs)
        if agg == "std":
            # 标准差 = sqrt(mean((volatility_j - baseline_j)^2))，逐 bar 计算差值的方差
            bl = fetch.get("std_baseline")
            vol_f = fetch.get("std_volatility")
            if not isinstance(bl, dict) or not isinstance(vol_f, str):
                return None
            acc: list[float] = []
            for j in window:
                bj = eval_here(bl, j)      # 均值基准（自定义公式）
                vj = _intrinsic_bar(bars[j], vol_f) if 0 <= j < len(bars) else None  # 波动字段
                if bj is None or vj is None:
                    return None
                acc.append((vj - bj) ** 2)
            return math.sqrt(sum(acc) / len(acc))
        return None

    if op == "ref_builtin":
        # 引用内置指标子线：从 builtin_rows[j] 取 sub_name 字段
        sub_name = node.get("sub_name")
        fetch = node.get("fetch")
        if not isinstance(sub_name, str) or not isinstance(fetch, dict):
            return None

        def getter(idx: int) -> Optional[float]:
            if idx < 0 or idx >= len(builtin_rows):
                return None
            v = builtin_rows[idx].get(sub_name)
            if v is None:
                return None
            fv = float(v)
            if math.isnan(fv) or math.isinf(fv):
                return None
            return fv

        out = apply_fetch(fetch, getter)
        if out is None and diag is not None:
            _append_diag(diag, "REF_BUILTIN_NONE", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"内置[{sub_name}]取数结果为空（含窗口不足或无该子线值）")
        return out

    if op == "ref_sibling":
        # 引用兄弟子线：从 sub_values[sk][j] 取已算好的同指标其他子线的值
        sk = node.get("sub_key")
        fetch = node.get("fetch")
        if not isinstance(sk, str) or not isinstance(fetch, dict):
            return None
        series = sub_values.get(sk)
        if series is None:
            _append_diag(diag, "MISSING_SIBLING_SERIES", sub_key=eval_sub_key, detail=f"未找到子线[{sk}]序列")
            return None

        def getter(idx: int) -> Optional[float]:
            if idx < 0 or idx >= len(series):
                return None
            return series[idx]

        out = apply_fetch(fetch, getter)
        if out is None:
            _append_diag(diag, "MISSING_SIBLING_VALUE", bar_index=i, trade_date=_td_str(bars, i), sub_key=eval_sub_key, detail=f"子线[{sk}]引用链在当日或窗口内无数值")
        return out

    return None  # 未知 op 类型（已在 DSL 校验阶段拦截）


def explain_sub_failure(
    parsed: UserIndicatorDefinitionParsed,
    bars: Sequence[Any],
    sub_values: dict[str, list[Optional[float]]],
    idx: int,
    sub_key: str,
) -> list[dict[str, Any]]:
    """对某一子线在指定 bar 重算一次并收集详细诊断信息（仅用于试算失败时的错误提示）。

    Returns:
        诊断条目列表，每条包含 code/detail/trade_date 等字段（最多 14 条）。
    """
    subs = parsed.sub_indicators
    sub = next((x for x in subs if str(x.get("key")) == sub_key), None)
    if not sub:
        return []
    param_vals = parse_param_defaults([dict(x) for x in parsed.params])
    builtin_rows = build_builtin_series(list(bars))
    diag: list[dict[str, Any]] = []
    _eval_formula(
        sub.get("formula"),
        i=idx,
        bars=bars,
        builtin_rows=builtin_rows,
        sub_values=sub_values,
        param_vals=param_vals,
        diag=diag,
        eval_sub_key=sub_key,
    )
    return diag


def build_builtin_series(bars: Sequence[Any]) -> list[dict[str, float]]:
    """将内置指标（MA/KDJ/BOLL/MACD）的按日字典，对齐成与 bars 同长的列表。

    返回 [{bar_i 的所有内置指标值 + OHLCV}, ...]，下标与 bars 下标一一对应。
    支持 BarDaily ORM 对象或复权后的 SimpleNamespace 对象。
    """
    if not bars:
        return []
    bar_list = list(bars)
    ind_by_date = compute_indicators(bar_list, start_date=getattr(bar_list[0], "trade_date", None))
    rows: list[dict[str, float]] = []
    for b in bar_list:
        row: dict[str, float] = {}
        td = getattr(b, "trade_date")
        pack = ind_by_date.get(td) or {}
        for k, v in pack.items():
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                row[k] = float(v)
        # 补充 OHLCV（确保 intrinsic 字段始终可用）
        row["open"] = float(b.open)
        row["high"] = float(b.high)
        row["low"] = float(b.low)
        row["close"] = float(b.close)
        row["volume"] = float(b.volume)
        row["amount"] = float(b.amount)
        tr = getattr(b, "turnover_rate", None)
        row["turnover_rate"] = float(tr) if tr is not None else 0.0
        rows.append(row)
    return rows


def compute_definition_series(
    parsed: UserIndicatorDefinitionParsed,
    bars: Sequence[Any],
) -> dict[str, list[Optional[float]]]:
    """计算所有子线的完整逐日序列（主要外部接口）。

    按拓扑顺序逐条子线、逐根 bar 调用 _eval_formula 求值。
    第一根 bar 计算失败时若设置了 initial_value，用它作为兜底。

    Args:
        parsed: parse_and_validate_definition 返回的已解析定义。
        bars: 按 trade_date 升序的 K 线序列。

    Returns:
        {子线key: [v0, v1, ..., vn-1]}，与 bars 等长，无值处为 None。
    """
    bar_list = list(bars)
    subs = parsed.sub_indicators
    sub_keys = {str(s.get("key")) for s in subs}
    topo = _topo_order(sub_keys, subs)         # 计算安全顺序
    param_vals = parse_param_defaults([dict(x) for x in parsed.params])
    builtin_rows = build_builtin_series(bar_list)
    n = len(bar_list)
    sub_values: dict[str, list[Optional[float]]] = {str(s.get("key")): [None] * n for s in subs}
    init_map = {str(s.get("key")): _float_initial(s.get("initial_value")) for s in subs}

    for sk in topo:
        sub = next(x for x in subs if str(x.get("key")) == sk)
        formula = sub.get("formula")
        init_v = init_map.get(sk)
        for i in range(n):
            v = _eval_formula(
                formula,
                i=i,
                bars=bar_list,
                builtin_rows=builtin_rows,
                sub_values=sub_values,
                param_vals=param_vals,
                eval_sub_key=sk,
            )
            if v is not None and not (math.isnan(v) or math.isinf(v)):
                sub_values[sk][i] = v
            elif init_v is not None and i == 0:
                # 第一根 bar 计算失败时使用初始值（如 RSI 的第一天初始值 = 50）
                sub_values[sk][i] = init_v
            else:
                sub_values[sk][i] = v
    return sub_values


def load_adjusted_bar_sequence(
    db: Session,
    symbol_id: int,
    start: Optional[date],
    end: Optional[date],
    adj: AdjType,
) -> list[Any]:
    """从数据库加载日线并做复权处理，返回 SimpleNamespace 列表（与 K 线接口口径一致）。

    副图自定义指标需要与 K 线使用相同的复权方式，否则指标值与 K 线位置不对齐。
    返回的对象与 BarDaily 接口兼容（有 trade_date/open/high/low/close/volume/amount/turnover_rate）。
    """
    q = db.query(BarDaily).filter(BarDaily.symbol_id == symbol_id).order_by(BarDaily.trade_date.asc())
    if start:
        q = q.filter(BarDaily.trade_date >= start)
    if end:
        q = q.filter(BarDaily.trade_date <= end)
    rows_db = q.all()
    adj_map: dict[date, float] = {}
    latest_factor = 1.0
    if adj != "none":
        adj_map = build_adj_map(db, symbol_id)
        latest_factor = get_latest_factor(adj_map)
    out: list[Any] = []
    for b in rows_db:
        out.append(
            SimpleNamespace(
                trade_date=b.trade_date,
                open=apply_adj(float(b.open), b.trade_date, adj, adj_map, latest_factor),
                high=apply_adj(float(b.high), b.trade_date, adj, adj_map, latest_factor),
                low=apply_adj(float(b.low), b.trade_date, adj, adj_map, latest_factor),
                close=apply_adj(float(b.close), b.trade_date, adj, adj_map, latest_factor),
                volume=float(b.volume),
                amount=float(b.amount),
                turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
            )
        )
    return out


def custom_indicator_daily_points(
    db: Session,
    *,
    ts_code: str,
    user_indicator_id: int,
    sub_key: str,
    adj: AdjType,
    start: Optional[date],
    end: Optional[date],
) -> dict[str, Any]:
    """计算并返回 K 线副图所需的自定义指标子线序列。

    仅支持 DSL（definition_json）类型的指标，且子线必须设置了 use_in_chart=True。
    时间对齐：与 K 线使用相同的复权方式（adj 参数），确保指标值在图上与 K 线对应。

    Returns:
        {"ok": True, "points": [{"time": "YYYY-MM-DD", "value": ...}, ...], ...}
        或 {"ok": False, "message": "错误原因"}
    """
    code = ts_code.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        return {"ok": False, "message": f"未找到标的 {code}", "points": []}
    ui = db.query(UserIndicator).filter(UserIndicator.id == user_indicator_id).one_or_none()
    if not ui or not (ui.definition_json and str(ui.definition_json).strip()):
        return {"ok": False, "message": "仅支持已保存为 DSL 的自定义指标", "points": []}
    try:
        parsed = parse_and_validate_definition(db, json.loads(ui.definition_json))
    except ValueError as e:
        return {"ok": False, "message": str(e), "points": []}
    # 校验 sub_key 是否设置了参与图形展示
    allowed = {str(s.get("key")) for s in parsed.sub_indicators if bool(s.get("use_in_chart")) and not bool(s.get("auxiliary_only"))}
    if sub_key not in allowed:
        return {"ok": False, "message": f"子线 {sub_key} 未参与图形展示", "points": []}
    bars = load_adjusted_bar_sequence(db, sym.id, start, end, adj)
    if len(bars) < 2:
        return {"ok": False, "message": "有效日线不足", "points": []}
    try:
        series = compute_definition_series(parsed, bars)
    except ValueError as e:
        return {"ok": False, "message": str(e), "points": []}
    seq = series.get(sub_key) or []
    points = []
    for i, b in enumerate(bars):
        td = getattr(b, "trade_date")
        v = seq[i] if i < len(seq) else None
        points.append(
            {
                "time": td.isoformat() if hasattr(td, "isoformat") else str(td),
                "value": None if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))) else float(v),
            }
        )
    return {"ok": True, "message": "ok", "points": points, "display_name": ui.display_name, "sub_key": sub_key}


def try_eval_definition_on_symbol(
    db: Session,
    parsed: UserIndicatorDefinitionParsed,
    ts_code: str,
    *,
    trade_date: Optional[date] = None,
    warmup_days: int = 260,
    sample_tail: int = 5,
) -> dict[str, Any]:
    """对指定股票做指标试算，返回最近几日的样本数据（供保存前校验）。

    Args:
        parsed: 已解析的指标定义。
        ts_code: 用于试算的股票代码（需本地已同步日线）。
        trade_date: 若指定，只返回该日的结果；否则返回最近 sample_tail 天。
        warmup_days: 向前加载多少天历史数据（保证 MA60 等有足够预热）。
        sample_tail: 无 trade_date 时返回最后几天的样本。

    Returns:
        {
          "ok": bool,                   # 所有样本日期是否全部计算成功
          "message": str,               # 成功/失败摘要
          "sample_rows": [...],         # 每天的值和诊断信息
          "error_detail": str | None,   # 第一个失败项的简短说明
          "report_keys": [str] | None,  # DSL 参与展示的子线 key 列表
        }
    """
    code = ts_code.strip().upper()
    sym = db.query(Symbol).filter(Symbol.ts_code == code).one_or_none()
    if not sym:
        return {"ok": False, "message": f"未找到标的 {code}", "sample_rows": [], "error_detail": None, "report_keys": None}

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
            "message": f"{code} 近期日线不足 30 根，无法试算",
            "sample_rows": [],
            "error_detail": None,
            "report_keys": None,
        }

    try:
        series = compute_definition_series(parsed, bars)
    except ValueError as e:
        return {"ok": False, "message": str(e), "sample_rows": [], "error_detail": str(e), "report_keys": None}

    all_dates = [b.trade_date for b in bars]
    if trade_date is not None:
        if trade_date not in all_dates:
            return {"ok": False, "message": f"指定日 {trade_date} 无数据", "sample_rows": [], "error_detail": None, "report_keys": None}
        try_dates = [trade_date]
    else:
        try_dates = all_dates[-sample_tail:]  # 最近 N 天

    # 确定参与报告的子线：非辅助且参与选股或图形的
    report_keys: list[str] = []
    for s in parsed.sub_indicators:
        if bool(s.get("auxiliary_only")):
            continue
        if bool(s.get("use_in_screening")) or bool(s.get("use_in_chart")):
            report_keys.append(str(s.get("key")))
    if not report_keys:
        report_keys = [str(parsed.sub_indicators[0].get("key"))]

    sample_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for td in try_dates:
        idx = all_dates.index(td)
        row: dict[str, Any] = {"trade_date": td.isoformat(), "values": {}, "error": None, "diagnostics": None}
        bad = False
        first_bad_key: Optional[str] = None
        for rk in report_keys:
            val = series.get(rk, [None] * len(bars))[idx]
            if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
                row["values"][rk] = None
                bad = True
                errors.append(f"{td}:{rk}")
                if first_bad_key is None:
                    first_bad_key = rk
            else:
                row["values"][rk] = float(val)
        if bad:
            msgs = [f"{td.isoformat()} 子线[{first_bad_key}]无数值"]
            if first_bad_key:
                # 重算失败子线并收集诊断信息（失败原因提示）
                diags = explain_sub_failure(parsed, bars, series, idx, first_bad_key)
                row["diagnostics"] = diags
                if diags:
                    msgs.append(diags[0].get("detail") or diags[0].get("code") or "")
            row["error"] = "；".join(m for m in msgs if m)
        sample_rows.append(row)

    ok = len(errors) == 0
    return {
        "ok": ok,
        "message": "试算通过" if ok else f"部分试算失败（{len(errors)} 项），见各行 diagnostics",
        "sample_rows": sample_rows,
        "error_detail": errors[0] if errors else None,
        "report_keys": report_keys,
    }
