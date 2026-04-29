"""参数敏感性扫描:对同一策略在单个参数的 N 个值上分别跑回测,评估策略鲁棒性。

核心思路:用户跑完一次回测觉得效果不错,但「这个结果是策略真的好,还是刚好调到这个阈值?」
把 buy_threshold 从 25 扫到 35(步长 2),看收益曲线平滑还是波动 — 平滑 = 鲁棒,大起大落 = 过拟合。

实现上:循环调用 backtest_runner.run_backtest(),每次替换 param_path 指向的字段,
收集关键指标(total_return_pct / max_drawdown_pct / total_trades / sharpe_ratio)返回。

支持两种参数路径:
  - 顶层字段:"buy_threshold" / "max_positions" / ...
  - 多条件 logic 嵌套:"buy_logic.conditions[0].threshold"
"""
from __future__ import annotations

import copy
import logging
import re
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.services.backtest_runner import run_backtest

log = logging.getLogger(__name__)

# 白名单:哪些顶层字段允许被扫描。避免用户误传 start_date 这种非数值字段。
_TOP_LEVEL_SCANNABLE = {
    "buy_threshold",
    "sell_threshold",
    "initial_capital",
    "max_positions",
    "max_scan",
    "commission_rate",
    "slippage_bps",
    "lot_size",
}


def _set_param(params: dict, param_path: str, value: float) -> None:
    """按路径在 params 字典里定位目标字段,原地替换为 value。

    支持路径:
      - "buy_threshold"
      - "buy_logic.conditions[0].threshold"  (conditions 用索引,其它用 key)
    """
    if "." not in param_path and "[" not in param_path:
        # 顶层字段
        if param_path not in _TOP_LEVEL_SCANNABLE:
            raise ValueError(f"顶层字段 {param_path} 不在可扫描白名单中")
        params[param_path] = value
        return

    # 嵌套路径:按 . 拆分,每段可能是 "key" 或 "key[idx]"
    parts = param_path.split(".")
    cur: Any = params
    for i, seg in enumerate(parts):
        is_last = i == len(parts) - 1
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(\[(\d+)\])?$", seg)
        if not m:
            raise ValueError(f"非法路径段 '{seg}'")
        key = m.group(1)
        idx = int(m.group(3)) if m.group(3) is not None else None

        if cur is None:
            raise ValueError(f"路径 {param_path} 中 {seg} 的父级为 None")
        if key not in cur:
            raise ValueError(f"路径 {param_path} 中找不到键 '{key}'")

        if idx is None:
            if is_last:
                cur[key] = value
            else:
                cur = cur[key]
        else:
            arr = cur[key]
            if not isinstance(arr, list) or idx >= len(arr):
                raise ValueError(f"路径 {param_path} 中 {key}[{idx}] 越界或非数组")
            if is_last:
                arr[idx] = value
            else:
                cur = arr[idx]


def _extract_metrics(result: dict) -> dict:
    """从 run_backtest 完整结果中抽取敏感性扫描关心的关键绩效指标。"""
    return {
        "total_return_pct": float(result.get("total_return_pct") or 0.0),
        "max_drawdown_pct": float(result.get("max_drawdown_pct") or 0.0),
        "total_trades": int(result.get("total_trades") or 0),
        "win_rate": result.get("win_rate"),  # 可能为 None
        "sharpe_ratio": result.get("sharpe_ratio"),
        "annualized_return": result.get("annualized_return"),
        "alpha_pct": result.get("alpha_pct"),
        "scanned_stocks": int(result.get("scanned_stocks") or 0),
    }


def run_sensitivity_scan(
    db: Session,
    *,
    base_params: dict,
    param_path: str,
    values: list[float],
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> list[dict]:
    """对 base_params 中 param_path 指向的字段,分别取 values 中每个值跑一次回测。

    Args:
        db: SQLAlchemy Session(每次 run_backtest 内部自己管理事务,外层只需保证 Session 有效)。
        base_params: 基础回测参数字典(同 run_backtest 的 kwargs)。每次扫描会 deepcopy 后修改。
        param_path: 要扫描的参数路径,见 _set_param 文档。
        values: 扫描点列表(2-15 个值,否则拒绝:太少无意义,太多太慢)。
        progress_callback: 可选回调,签名 (done_count, total_count),每跑完一个点就调用一次。

    Returns:
        [{"value": v, "metrics": {...}, "error": None | str}, ...]
        单点回测失败不整批失败,记到对应项的 error 字段里,继续跑其它点。
    """
    if not values:
        raise ValueError("values 不能为空")
    if len(values) < 2 or len(values) > 15:
        raise ValueError(f"values 数量应在 2-15 之间,当前 {len(values)}")

    total = len(values)
    out: list[dict] = []
    for i, v in enumerate(values):
        params = copy.deepcopy(base_params)
        try:
            _set_param(params, param_path, v)
            result = run_backtest(db, **params)
            out.append({"value": v, "metrics": _extract_metrics(result), "error": None})
        except Exception as ex:  # noqa: BLE001
            log.warning("敏感性扫描第 %d 点(%s=%s)失败: %s", i + 1, param_path, v, ex)
            out.append({"value": v, "metrics": None, "error": str(ex)})
        if progress_callback is not None:
            try:
                progress_callback(i + 1, total)
            except Exception:  # noqa: BLE001
                # 回调挂了不影响主流程
                pass
    return out
