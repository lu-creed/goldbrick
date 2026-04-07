"""用户自定义指标 DSL（与 PRD 指标库对齐）：JSON 公式树 + 定义校验。

公式仅允许：数字、本指标参数、固有行情字段、sqrt、四则运算、引用内置子线、引用兄弟子线；
每种引用须带「取数方式」：当前周期 / 前 N 周期（N 来自参数）/ 区间（均/高/低/低标准差，标准差需基准表达式 + 波动固有字段）。
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

INTRINSIC_FIELDS = frozenset(
    {"close", "high", "low", "volume", "amount", "open", "turnover_rate"}
)
PERIOD_LITERALS = frozenset({"1d", "1w", "1M", "1Q", "1y"})
RANGE_AGGS = frozenset({"avg", "min", "max", "std"})
SUB_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")


def builtin_sub_names(db: Session) -> frozenset[str]:
    rows = db.query(IndicatorSubIndicator.name).all()
    return frozenset(r[0] for r in rows if r[0])


def parse_param_defaults(params: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in params:
        name = str(p.get("name") or "").strip()
        dv = p.get("default_value")
        out[name] = "" if dv is None else str(dv).strip()
    return out


def _as_dict(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError("公式节点必须是 JSON 对象")
    return node


@dataclass
class UserIndicatorDefinitionParsed:
    """校验通过后的定义（dict 形式，便于序列化）。"""

    version: int
    raw: dict[str, Any]

    @property
    def params(self) -> list[dict[str, Any]]:
        return list(self.raw.get("params") or [])

    @property
    def periods(self) -> list[str]:
        return list(self.raw.get("periods") or [])

    @property
    def sub_indicators(self) -> list[dict[str, Any]]:
        return list(self.raw.get("sub_indicators") or [])


def validate_fetch(fetch: Any, param_names: set[str]) -> None:
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
    if depth > 48:
        raise ValueError("公式嵌套过深")
    d = _as_dict(node)
    op = d.get("op")
    if op == "num":
        v = d.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("num.value 须为数字")
        fv = float(v)
        if math.isnan(fv) or math.isinf(fv):
            raise ValueError("不允许 NaN/Inf")
        return
    if op == "param":
        name = d.get("name")
        if not isinstance(name, str) or name not in param_names:
            raise ValueError("param.name 须为本指标参数")
        return
    if op == "intrinsic":
        field = d.get("field")
        if field not in INTRINSIC_FIELDS:
            raise ValueError(f"intrinsic.field 非法: {field}")
        return
    if op == "rolling":
        # 参数化 N 周期滚动统计（PRD 里「动态均线」等价于收盘 rolling avg）
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
        sub_name = d.get("sub_name")
        if not isinstance(sub_name, str) or not sub_name.strip():
            raise ValueError("ref_builtin.sub_name 不能为空")
        validate_fetch(d.get("fetch"), param_names)
        return
    if op == "ref_sibling":
        sk = d.get("sub_key")
        if sub_keys is not None:
            if not isinstance(sk, str) or sk not in sub_keys:
                raise ValueError("ref_sibling.sub_key 须为已声明的子指标 key")
        validate_fetch(d.get("fetch"), param_names)
        return
    raise ValueError(f"未知公式 op: {op}")


def _collect_sibling_deps(node: Any, out: set[str]) -> None:
    d = _as_dict(node)
    op = d.get("op")
    if op == "ref_sibling":
        sk = d.get("sub_key")
        if isinstance(sk, str):
            out.add(sk)
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
    """校验完整定义 JSON；失败抛出 ValueError。"""
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

    periods = raw.get("periods") or ["1d"]
    if not isinstance(periods, list) or not periods:
        raise ValueError("periods 须为非空数组")
    for p in periods:
        if p not in PERIOD_LITERALS:
            raise ValueError(f"不支持的周期: {p}")

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
        if aux and (u_scr or u_chart):
            raise ValueError(f"子指标 {key}: 勾选仅辅助计算时，不应参与选股/回测或图形")
        if u_chart:
            ck = s.get("chart_kind")
            if ck not in ("line", "bar"):
                raise ValueError(f"子指标 {key}: 参与图形展示时必须指定 chart_kind=line|bar")
        form = s.get("formula")
        if form is None:
            raise ValueError(f"子指标 {key} 缺少 formula")

    # ref_builtin 白名单、公式树
    for s in subs:
        key = str(s.get("key"))
        form = s.get("formula")
        _validate_formula_tree(form, param_names, sub_keys)

        def _check_builtin_refs(n: Any) -> None:
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

    # 依赖环
    adj: dict[str, set[str]] = {k: set() for k in sub_keys}
    for s in subs:
        sk = str(s.get("key"))
        deps: set[str] = set()
        _collect_sibling_deps(s.get("formula"), deps)
        deps.discard(sk)
        for d0 in deps:
            if d0 not in sub_keys:
                raise ValueError(f"子指标 {sk} 引用了不存在的子线 key: {d0}")
            adj[sk].add(d0)

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
    """写入 DB：去掉运行期附加字段。"""
    d = dict(parsed.raw)
    d.pop("_compute_order", None)
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def definition_dict_for_api(parsed: UserIndicatorDefinitionParsed) -> dict[str, Any]:
    d = dict(parsed.raw)
    d.pop("_compute_order", None)
    return d
