"""策略组合器（combiner）：把若干「组」通过 AND/OR/NOT/括号连成一棵布尔树。

存储一律用嵌套 JSON 树（零 eval 风险）：
  - 叶子：{"ref": "G1"}，指向某个组的布尔结果
  - 内部：{"op": "AND"|"OR", "args": [node, ...]}（至少 2 个子节点）
  - 一元：{"op": "NOT", "args": [node]}（恰好 1 个子节点）

前端可以让用户输入字符串如 "G1 AND (G2 OR G3)"，由 combiner_from_str 转树后存库；
回显时再用 combiner_to_str 还原为可读字符串。
"""
from __future__ import annotations

from typing import Any, Dict, List

_MAX_DEPTH = 16
_ALLOWED_OPS = frozenset({"AND", "OR", "NOT"})


# ─────────────────────────────────────────────────────────────
# 校验
# ─────────────────────────────────────────────────────────────

def validate_combiner(tree: Any, group_ids: List[str]) -> None:
    """递归校验 combiner 树。失败抛 ValueError。

    Args:
        tree: 待校验的 combiner 节点（dict）。
        group_ids: 所有合法的组 id 列表。ref 叶子必须指向其中之一。

    约束：
        - 叶子 = {"ref": str}，ref 必须在 group_ids 中
        - 内部 = {"op": "AND"|"OR", "args": [...]}，args 至少 2 个子节点
        - 一元 = {"op": "NOT", "args": [node]}
        - 深度 ≤ _MAX_DEPTH
        - args 不可为空、不能混用 ref 和 op 键
    """
    if not group_ids:
        raise ValueError("groups 不可为空")
    gid_set = set(group_ids)
    _validate_node(tree, gid_set, depth=0)


def _validate_node(node: Any, gid_set: set, depth: int) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError(f"combiner 嵌套深度超过 {_MAX_DEPTH}")
    if not isinstance(node, dict):
        raise ValueError(f"combiner 节点必须为 dict，实际: {type(node).__name__}")

    has_ref = "ref" in node
    has_op = "op" in node
    if has_ref and has_op:
        raise ValueError("combiner 节点不能同时含 ref 和 op")
    if not has_ref and not has_op:
        raise ValueError("combiner 节点必须含 ref 或 op")

    if has_ref:
        ref = node["ref"]
        if not isinstance(ref, str) or not ref:
            raise ValueError(f"ref 必须为非空字符串，实际: {ref!r}")
        if ref not in gid_set:
            raise ValueError(f"ref {ref!r} 不在组列表中")
        return

    # 内部节点
    op = node["op"]
    if op not in _ALLOWED_OPS:
        raise ValueError(f"op 必须是 {sorted(_ALLOWED_OPS)}，实际: {op!r}")
    args = node.get("args")
    if not isinstance(args, list) or not args:
        raise ValueError(f"op={op} 的 args 必须是非空列表")
    if op == "NOT":
        if len(args) != 1:
            raise ValueError(f"NOT 必须恰好 1 个子节点，实际 {len(args)} 个")
    else:
        if len(args) < 2:
            raise ValueError(f"{op} 至少需要 2 个子节点，实际 {len(args)} 个")
    for child in args:
        _validate_node(child, gid_set, depth + 1)


# ─────────────────────────────────────────────────────────────
# 求值
# ─────────────────────────────────────────────────────────────

def eval_combiner(tree: Dict[str, Any], group_results: Dict[str, bool]) -> bool:
    """对给定的组结果字典递归求 combiner 真值。

    Args:
        tree: 已通过 validate_combiner 的 combiner 树。
        group_results: {组 id: bool}，覆盖树中所有 ref。
    """
    if "ref" in tree:
        ref = tree["ref"]
        if ref not in group_results:
            raise ValueError(f"组 {ref!r} 缺少求值结果")
        return bool(group_results[ref])
    op = tree["op"]
    args = tree["args"]
    if op == "AND":
        for child in args:
            if not eval_combiner(child, group_results):
                return False
        return True
    if op == "OR":
        for child in args:
            if eval_combiner(child, group_results):
                return True
        return False
    if op == "NOT":
        return not eval_combiner(args[0], group_results)
    raise ValueError(f"不支持的 op: {op!r}")


# ─────────────────────────────────────────────────────────────
# 字符串 ↔ 树（UI 辅助）
# ─────────────────────────────────────────────────────────────

def combiner_to_str(tree: Dict[str, Any]) -> str:
    """把树渲染成人类可读字符串，如 "(G1 AND G2) OR G3"。"""
    return _node_to_str(tree, parent_op=None)


# 运算符优先级：NOT > AND > OR（值越大越紧）
_PRECEDENCE = {"OR": 1, "AND": 2, "NOT": 3}


def _node_to_str(node: Dict[str, Any], parent_op: str) -> str:
    if "ref" in node:
        return str(node["ref"])
    op = node["op"]
    args = node["args"]
    if op == "NOT":
        inner = _node_to_str(args[0], op)
        s = f"NOT {inner}"
    else:
        parts = [_node_to_str(a, op) for a in args]
        s = f" {op} ".join(parts)
    # 需要加括号：外层优先级高于自己（例如 OR 在 AND 内）
    if parent_op is not None and _PRECEDENCE[op] < _PRECEDENCE.get(parent_op, 0):
        return f"({s})"
    return s


def combiner_from_str(text: str, group_ids: List[str]) -> Dict[str, Any]:
    """把 "G1 AND (G2 OR G3)" 解析为 combiner 树。

    支持：
        - 标识符：字母开头，字母/数字/下划线（如 G1、GroupA）
        - 关键字：AND / OR / NOT（大小写不敏感）
        - 括号：( )
    """
    gid_set = set(group_ids)
    tokens = _tokenize(text)
    parser = _Parser(tokens, gid_set)
    tree = parser.parse_expression()
    parser.expect_eof()
    return tree


# ----- Tokenizer / Parser（递归下降，无 eval）-----

def _tokenize(text: str) -> List[tuple]:
    tokens: List[tuple] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append(("PAREN", c))
            i += 1
            continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            upper = word.upper()
            if upper in {"AND", "OR", "NOT"}:
                tokens.append(("OP", upper))
            else:
                tokens.append(("IDENT", word))
            i = j
            continue
        raise ValueError(f"combiner 字符串含非法字符 {c!r} at pos {i}")
    return tokens


class _Parser:
    def __init__(self, tokens: List[tuple], gid_set: set) -> None:
        self.tokens = tokens
        self.pos = 0
        self.gid_set = gid_set

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _consume(self):
        tok = self._peek()
        if tok is None:
            raise ValueError("combiner 表达式意外结束")
        self.pos += 1
        return tok

    def expect_eof(self) -> None:
        if self._peek() is not None:
            raise ValueError(f"combiner 表达式有多余 token: {self._peek()}")

    def parse_expression(self) -> Dict[str, Any]:
        """OR 优先级最低。"""
        left = self.parse_and()
        args = [left]
        while True:
            tok = self._peek()
            if tok and tok[0] == "OP" and tok[1] == "OR":
                self._consume()
                args.append(self.parse_and())
            else:
                break
        return args[0] if len(args) == 1 else {"op": "OR", "args": args}

    def parse_and(self) -> Dict[str, Any]:
        left = self.parse_not()
        args = [left]
        while True:
            tok = self._peek()
            if tok and tok[0] == "OP" and tok[1] == "AND":
                self._consume()
                args.append(self.parse_not())
            else:
                break
        return args[0] if len(args) == 1 else {"op": "AND", "args": args}

    def parse_not(self) -> Dict[str, Any]:
        tok = self._peek()
        if tok and tok[0] == "OP" and tok[1] == "NOT":
            self._consume()
            inner = self.parse_not()  # NOT NOT X 允许
            return {"op": "NOT", "args": [inner]}
        return self.parse_atom()

    def parse_atom(self) -> Dict[str, Any]:
        tok = self._consume()
        if tok[0] == "PAREN" and tok[1] == "(":
            expr = self.parse_expression()
            close = self._consume()
            if close != ("PAREN", ")"):
                raise ValueError(f"期望 ')'，实际 {close}")
            return expr
        if tok[0] == "IDENT":
            ident = tok[1]
            if ident not in self.gid_set:
                raise ValueError(f"标识符 {ident!r} 不在组列表中")
            return {"ref": ident}
        raise ValueError(f"意外 token: {tok}")
