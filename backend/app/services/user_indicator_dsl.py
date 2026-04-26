"""
用户自定义指标 DSL（与 PRD 指标库对齐）：JSON 公式树校验与定义解析。

公式树是一个嵌套 JSON，每个节点是一个运算操作（op）：
  num        数字常量，如 {"op": "num", "value": 20}
  param      引用本指标的参数，如 {"op": "param", "name": "N"}
  intrinsic  引用行情字段，如 {"op": "intrinsic", "field": "close"}
  rolling    参数化 N 日滚动统计，如 {"op": "rolling", "field": "close", "n_param": "N", "stat": "avg"}
  sqrt       平方根，如 {"op": "sqrt", "x": <子公式>}
  neg        取负，如 {"op": "neg", "x": <子公式>}
  add/sub/mul/div  四则运算，各有 left 和 right 子节点
  ref_builtin  引用内置指标子线（如 MA20），附带取数方式（fetch）
  ref_sibling  引用本指标的另一条子线（前面算好的），附带取数方式

「取数方式」fetch：
  current        取当前周期的值
  prev_n         取前 N 个周期的值（N 来自参数）
  range+avg/min/max/std  对 [i-N+1..i] 区间的值做聚合

校验：
  - 公式树合法性（op 类型、字段名、参数名存在）
  - ref_builtin 引用的子线名必须在指标库白名单中
  - ref_sibling 必须引用已声明的子线 key
  - 子线之间的依赖不能形成环路（拓扑排序检测）
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import IndicatorSubIndicator

# ---- 常量 ----

# 支持在公式中直接引用的行情字段名
INTRINSIC_FIELDS = frozenset(
    {"close", "high", "low", "volume", "amount", "open", "turnover_rate"}
)
# 支持的周期字面量
PERIOD_LITERALS = frozenset({"1d", "1w", "1M", "1Q", "1y"})
# 区间聚合方式
RANGE_AGGS = frozenset({"avg", "min", "max", "std"})
# 子线 key 格式：小写字母开头，只含小写字母/数字/下划线，最长 64 字符
SUB_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# 参数名格式：字母/下划线开头，含字母/数字/下划线
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def builtin_sub_names(db: Session) -> frozenset[str]:
    """从 indicator_sub_indicators 表获取所有内置子线名（白名单，用于 ref_builtin 校验）。"""
    rows = db.query(IndicatorSubIndicator.name).all()
    return frozenset(r[0] for r in rows if r[0])


def parse_param_defaults(params: list[dict[str, Any]]) -> dict[str, str]:
    """将参数定义列表转换为 {参数名: 默认值字符串} 字典，供求值引擎使用。

    默认值为空字符串时视为未设置（求值时会转成 0 或 1）。
    """
    out: dict[str, str] = {}
    for p in params:
        name = str(p.get("name") or "").strip()
        dv = p.get("default_value")
        out[name] = "" if dv is None else str(dv).strip()
    return out


def _as_dict(node: Any) -> dict[str, Any]:
    """确保公式节点是 dict，否则抛 ValueError。"""
    if not isinstance(node, dict):
        raise ValueError("公式节点必须是 JSON 对象")
    return node


@dataclass
class UserIndicatorDefinitionParsed:
    """校验通过后的指标定义对象（保留原始 raw dict，便于重新序列化存库）。"""

    version: int
    raw: dict[str, Any]

    @property
    def params(self) -> list[dict[str, Any]]:
        """返回参数定义列表。"""
        return list(self.raw.get("params") or [])

    @property
    def periods(self) -> list[str]:
        """返回支持的周期列表，如 ["1d"]。"""
        return list(self.raw.get("periods") or [])

    @property
    def sub_indicators(self) -> list[dict[str, Any]]:
        """返回子线定义列表。"""
        return list(self.raw.get("sub_indicators") or [])


def validate_fetch(fetch: Any, param_names: set[str]) -> None:
    """校验取数方式节点（fetch）的合法性。

    fetch.mode 可以是：
    - current：直接取当前 bar 的值，无需参数
    - prev_n：取前 N 个 bar 的值，N 由参数 n_param 指定
    - range：取 [i-N+1..i] 区间内的聚合值，N 由参数 n_param 指定，聚合方式由 range_agg 指定

    range_agg=std 时，需要额外的 std_baseline（均值基准公式）和 std_volatility（波动字段名）。
    """
    fd = _as_dict(fetch)
    mode = fd.get("mode")
    if mode not in ("current", "prev_n", "range"):
        raise ValueError("fetch.mode 须为 current / prev_n / range")
    if mode == "current":
        return
    npar = fd.get("n_param")
    if not isinstance(npar, str) or npar not in param_names:
        raise ValueError("prev_n / range 须指定 n_param，且为本指标已声明的参数名")
    if mode == "prev_n":
        return
    agg = fd.get("range_agg")
    if agg not in RANGE_AGGS:
        raise ValueError("range 须指定 range_agg: avg|min|max|std")
    if agg == "std":
        if fd.get("std_baseline") is None:
            raise ValueError("range_agg=std 时必须提供 std_baseline 公式")
        vol = fd.get("std_volatility")
        if vol not in INTRINSIC_FIELDS:
            raise ValueError("std_volatility 须为固有字段名")


def _validate_formula_tree(
    node: Any,
    param_names: set[str],
    sub_keys: Optional[set[str]],
    *,
    depth: int = 0,
) -> None:
    """递归校验公式树的每个节点是否合法。

    Args:
        node: 当前要校验的公式节点（JSON dict）。
        param_names: 本指标已声明的参数名集合（param 节点引用的名称必须在这里）。
        sub_keys: 本指标已声明的子线 key 集合（ref_sibling 引用的 key 必须在这里）。
        depth: 当前递归深度，超过 48 视为嵌套过深，防止构造恶意深递归耗尽栈。
    """
    if depth > 48:
        raise ValueError("公式嵌套过深")
    d = _as_dict(node)
    op = d.get("op")
    if op == "num":
        # 数字常量：必须是有限浮点数
        v = d.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("num.value 须为数字")
        fv = float(v)
        if math.isnan(fv) or math.isinf(fv):
            raise ValueError("不允许 NaN/Inf")
        return
    if op == "param":
        # 参数引用：名称必须是本指标已声明的参数
        name = d.get("name")
        if not isinstance(name, str) or name not in param_names:
            raise ValueError("param.name 须为本指标参数")
        return
    if op == "intrinsic":
        # 行情字段引用：只能是 INTRINSIC_FIELDS 中的字段名
        field = d.get("field")
        if field not in INTRINSIC_FIELDS:
            raise ValueError(f"intrinsic.field 非法: {field}")
        return
    if op == "rolling":
        # 参数化 N 周期滚动统计（等价于动态 MA，避免只能通过 ref_builtin 固定 MA5/10…）
        fld = d.get("field")
        if fld not in INTRINSIC_FIELDS:
            raise ValueError("rolling.field 须为固有行情字段")
        npar = d.get("n_param")
        if not isinstance(npar, str) or npar not in param_names:
            raise ValueError("rolling.n_param 须为本指标已声明的参数名")
        st = d.get("stat", "avg")
        if st not in ("avg", "min", "max"):
            raise ValueError("rolling.stat 须为 avg|min|max")
        return
    if op == "sqrt":
        _validate_formula_tree(d.get("x"), param_names, sub_keys, depth=depth + 1)
        return
    if op == "neg":
        _validate_formula_tree(d.get("x"), param_names, sub_keys, depth=depth + 1)
        return
    if op in ("add", "sub", "mul", "div"):
        _validate_formula_tree(d.get("left"), param_names, sub_keys, depth=depth + 1)
        _validate_formula_tree(d.get("right"), param_names, sub_keys, depth=depth + 1)
        return
    if op == "ref_builtin":
        # 引用内置指标子线：子线名非空，取数方式合法
        sub_name = d.get("sub_name")
        if not isinstance(sub_name, str) or not sub_name.strip():
            raise ValueError("ref_builtin.sub_name 不能为空")
        validate_fetch(d.get("fetch"), param_names)
        return
    if op == "ref_sibling":
        # 引用兄弟子线：key 必须是本指标已声明的其他子线
        sk = d.get("sub_key")
        if sub_keys is not None:
            if not isinstance(sk, str) or sk not in sub_keys:
                raise ValueError("ref_sibling.sub_key 须为已声明的子指标 key")
        validate_fetch(d.get("fetch"), param_names)
        return
    raise ValueError(f"未知公式 op: {op}")


def _collect_sibling_deps(node: Any, out: set[str]) -> None:
    """递归收集公式树中所有 ref_sibling 引用的子线 key（用于环路检测）。

    将所有「该子线需要哪些兄弟子线先计算好」的依赖收集到 out 集合中。
    """
    d = _as_dict(node)
    op = d.get("op")
    if op == "ref_sibling":
        sk = d.get("sub_key")
        if isinstance(sk, str):
            out.add(sk)
        # std_baseline 子公式里也可能有 ref_sibling
        f = d.get("fetch")
        if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
            _collect_sibling_deps(f["std_baseline"], out)
        return
    if op in ("sqrt", "neg"):
        _collect_sibling_deps(d.get("x"), out)
        return
    if op in ("add", "sub", "mul", "div"):
        _collect_sibling_deps(d.get("left"), out)
        _collect_sibling_deps(d.get("right"), out)
        return
    if op == "ref_builtin":
        f = d.get("fetch")
        if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
            _collect_sibling_deps(f["std_baseline"], out)
        return


def parse_and_validate_definition(db: Session, raw: Any) -> UserIndicatorDefinitionParsed:
    """解析并校验完整的指标定义 JSON。

    校验步骤：
    1. JSON 格式合法
    2. version=1
    3. params 格式和命名合法（无重复）
    4. periods 格式合法
    5. sub_indicators 非空，每条子线格式合法（key 唯一、name 非空、formula 存在）
    6. 公式树每个节点合法（递归校验）
    7. ref_builtin 引用的子线名在指标库白名单中
    8. 子线依赖无环（DFS 拓扑排序）

    Returns:
        UserIndicatorDefinitionParsed 对象，成功后可用于求值。

    Raises:
        ValueError: 任何校验失败时抛出，错误信息直接给前端展示。
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"definition 不是合法 JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError("definition 须为 JSON 对象")
    ver = raw.get("version", 1)
    if ver != 1:
        raise ValueError("仅支持 definition.version=1")

    # 校验参数列表
    params = raw.get("params") or []
    if not isinstance(params, list):
        raise ValueError("params 须为数组")
    param_names: set[str] = set()
    for i, p in enumerate(params):
        if not isinstance(p, dict):
            raise ValueError(f"params[{i}] 须为对象")
        name = str(p.get("name") or "").strip()
        if not PARAM_NAME_RE.match(name):
            raise ValueError(f"参数名非法: {name}")
        if name in param_names:
            raise ValueError(f"参数名重复: {name}")
        param_names.add(name)

    # 校验周期
    periods = raw.get("periods") or ["1d"]
    if not isinstance(periods, list) or not periods:
        raise ValueError("periods 须为非空数组")
    for p in periods:
        if p not in PERIOD_LITERALS:
            raise ValueError(f"不支持的周期: {p}")

    # 校验子线列表
    subs = raw.get("sub_indicators") or []
    if not isinstance(subs, list) or not subs:
        raise ValueError("须至少配置一条子指标")
    sub_keys: set[str] = set()
    builtin_allow = builtin_sub_names(db)

    for i, s in enumerate(subs):
        if not isinstance(s, dict):
            raise ValueError(f"sub_indicators[{i}] 须为对象")
        key = str(s.get("key") or "").strip()
        if not SUB_KEY_RE.match(key):
            raise ValueError(f"子指标 key 须小写字母开头，仅含小写字母数字下划线: {key}")
        if key in sub_keys:
            raise ValueError(f"子指标 key 重复: {key}")
        sub_keys.add(key)
        name = str(s.get("name") or "").strip()
        if not name:
            raise ValueError(f"子指标 {key} 须填写名称")
        aux = bool(s.get("auxiliary_only"))
        u_scr = bool(s.get("use_in_screening"))
        u_chart = bool(s.get("use_in_chart"))
        # auxiliary_only=True 时不应参与选股/图形展示（避免配置矛盾）
        if aux and (u_scr or u_chart):
            raise ValueError(f"子指标 {key}: 勾选仅辅助计算时，不应参与选股/回测或图形")
        if u_chart:
            ck = s.get("chart_kind")
            if ck not in ("line", "bar"):
                raise ValueError(f"子指标 {key}: 参与图形展示时必须指定 chart_kind=line|bar")
        form = s.get("formula")
        if form is None:
            raise ValueError(f"子指标 {key} 缺少 formula")

    # 校验每条子线的公式树 + ref_builtin 白名单
    for s in subs:
        key = str(s.get("key"))
        form = s.get("formula")
        _validate_formula_tree(form, param_names, sub_keys)

        def _check_builtin_refs(n: Any) -> None:
            """递归检查 ref_builtin 引用的子线名是否在白名单中。"""
            d = _as_dict(n)
            op = d.get("op")
            if op == "ref_builtin":
                sn = d.get("sub_name")
                if sn not in builtin_allow:
                    raise ValueError(f"子指标 {key}: 引用的内置子线「{sn}」不存在于指标库")
            if op in ("sqrt", "neg"):
                _check_builtin_refs(d.get("x"))
            if op in ("add", "sub", "mul", "div"):
                _check_builtin_refs(d.get("left"))
                _check_builtin_refs(d.get("right"))
            if op == "ref_builtin":
                f = d.get("fetch")
                if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
                    _check_builtin_refs(f["std_baseline"])
            if op == "ref_sibling":
                f = d.get("fetch")
                if isinstance(f, dict) and f.get("range_agg") == "std" and f.get("std_baseline"):
                    _check_builtin_refs(f["std_baseline"])

        _check_builtin_refs(form)

    # 检查子线依赖环：构建有向图，用 DFS 检测是否有环
    # adj[A] = {B, C} 表示「A 的公式引用了 B 和 C（B、C 需先计算）」
    adj: dict[str, set[str]] = {k: set() for k in sub_keys}
    for s in subs:
        sk = str(s.get("key"))
        deps: set[str] = set()
        _collect_sibling_deps(s.get("formula"), deps)
        deps.discard(sk)  # 自引用不算（虽然通常不应该自引用）
        for d0 in deps:
            if d0 not in sub_keys:
                raise ValueError(f"子指标 {sk} 引用了不存在的子线 key: {d0}")
            adj[sk].add(d0)

    # DFS 环路检测：如果在 DFS 递归栈中遇到已在栈上的节点，说明有环
    visited: set[str] = set()
    stack: set[str] = set()

    def dfs(u: str) -> None:
        if u in stack:
            raise ValueError("子指标引用形成环路，请调整公式")
        if u in visited:
            return
        stack.add(u)
        for v in adj[u]:
            dfs(v)
        stack.remove(u)
        visited.add(u)

    for k in sub_keys:
        if k not in visited:
            dfs(k)

    return UserIndicatorDefinitionParsed(version=1, raw=dict(raw))


def definition_to_storable(parsed: UserIndicatorDefinitionParsed) -> str:
    """将已校验的定义序列化为 JSON 字符串存入数据库（去掉运行期临时字段）。"""
    d = dict(parsed.raw)
    d.pop("_compute_order", None)
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def definition_dict_for_api(parsed: UserIndicatorDefinitionParsed) -> dict[str, Any]:
    """将已校验的定义转换为可序列化的 dict（供 API 返回时用）。"""
    d = dict(parsed.raw)
    d.pop("_compute_order", None)
    return d
