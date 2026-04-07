"""自定义指标表达式：仅允许 AST 中的四则运算与「白名单变量名」，避免 eval 注入。

白名单 = 行情字段(open/high/…) + 数据库里所有内置子指标名(如 MA5、MACD柱)。
MVP 不支持函数调用、幂运算、比较运算；后续可扩展 REF/SMA 等。
"""
from __future__ import annotations

import ast
import math
import re
from typing import FrozenSet

CODE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

BAR_FIELD_NAMES: FrozenSet[str] = frozenset(
    {"open", "high", "low", "close", "volume", "amount", "turnover_rate"}
)


def parse_and_validate_expr(source: str, allowed_names: FrozenSet[str]) -> ast.Expression:
    s = (source or "").strip()
    if not s:
        raise ValueError("表达式不能为空")
    if len(s) > 4000:
        raise ValueError("表达式过长（上限 4000 字符）")
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"语法错误: {e.msg}") from e
    if not isinstance(tree, ast.Expression):
        raise ValueError("仅支持单条表达式")
    _validate_node(tree.body, allowed_names)
    return tree


def _validate_node(node: ast.AST, allowed: FrozenSet[str]) -> None:
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            raise ValueError("不允许使用布尔字面量")
        if isinstance(v, (int, float)):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                raise ValueError("不允许 NaN/Inf 字面量")
            return
        raise ValueError("只允许数字字面量")
    if isinstance(node, ast.Name):
        if node.id not in allowed:
            raise ValueError(f"变量未在白名单内: {node.id}")
        return
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, (ast.UAdd, ast.USub)):
            _validate_node(node.operand, allowed)
            return
        raise ValueError("仅支持一元 + / -")
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            _validate_node(node.left, allowed)
            _validate_node(node.right, allowed)
            return
        raise ValueError("仅支持 + - * / 四则运算")
    raise ValueError(f"不支持的语法节点: {type(node).__name__}")


def eval_expression(tree: ast.Expression, env: dict[str, float]) -> float:
    return _eval_node(tree.body, env)


def _eval_node(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ValueError(f"当日缺少变量值: {node.id}")
        return float(env[node.id])
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, env)
        if isinstance(node.op, ast.UAdd):
            return v
        if isinstance(node.op, ast.USub):
            return -v
        raise ValueError("unsupported unary")
    if isinstance(node, ast.BinOp):
        a = _eval_node(node.left, env)
        b = _eval_node(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            if b == 0:
                raise ValueError("除零")
            return a / b
    raise ValueError("unsupported node")
