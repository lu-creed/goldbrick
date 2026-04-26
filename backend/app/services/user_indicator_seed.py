"""预置自定义指标种子：应用启动时写入，已存在则跳过。

这10条指标与前端「策略模板」一一对应，用户无需手动创建即可直接使用回测。
每条指标的 code 均以 tpl_ 开头，便于与用户自建指标区分。

注意：本模块在 seed_indicators 之后调用，确保内置子线（RSI12/MA5 等）已入库，
     parse_and_validate_definition 的 ref_builtin 校验才能通过。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import UserIndicator
from app.services.user_indicator_dsl import (
    definition_to_storable,
    parse_and_validate_definition,
)

log = logging.getLogger(__name__)

# 取数方式简写
_CURRENT = {"mode": "current"}

def _ref(sub_name: str) -> dict[str, Any]:
    return {"op": "ref_builtin", "sub_name": sub_name, "fetch": _CURRENT}

def _sub(left: Any, right: Any) -> dict[str, Any]:
    return {"op": "sub", "left": left, "right": right}

def _div(left: Any, right: Any) -> dict[str, Any]:
    return {"op": "div", "left": left, "right": right}

def _intrinsic(field: str) -> dict[str, Any]:
    return {"op": "intrinsic", "field": field}

def _def(subs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": 1, "params": [], "periods": ["1d"], "sub_indicators": subs}

def _sub_ind(
    key: str,
    name: str,
    formula: dict[str, Any],
    chart_kind: str = "line",
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "use_in_screening": True,
        "use_in_chart": True,
        "chart_kind": chart_kind,
        "formula": formula,
    }


# 10 条预置指标定义
_SEED: list[tuple[str, str, str, dict[str, Any]]] = [
    # (code, display_name, description, definition_dict)
    (
        "tpl_rsi",
        "RSI信号【预置】",
        "引用内置 RSI12，用于 RSI 超买超卖策略。子线 rsi12 取值 0~100，<30 超卖区，>70 超买区。",
        _def([_sub_ind("rsi12", "RSI12", _ref("RSI12"))]),
    ),
    (
        "tpl_ma_cross",
        "MA均线差值【预置】",
        "MA5 - MA20 的差值，>0 表示短线强于中线（金叉），<0 表示死叉。用于均线金叉/死叉策略。",
        _def([_sub_ind("diff", "MA5-MA20差值", _sub(_ref("MA5"), _ref("MA20")), "bar")]),
    ),
    (
        "tpl_macd_bar",
        "MACD柱【预置】",
        "直接暴露内置 MACD 柱状线（2×(DIF-DEA)）。>0 表示多头动能，<0 表示空头动能。",
        _def([_sub_ind("bar", "MACD柱", _ref("MACD柱"), "bar")]),
    ),
    (
        "tpl_boll_pos",
        "布林位置【预置】",
        "价格在布林带中的相对位置：(close-下轨)/(上轨-下轨)。0 = 在下轨，0.5 = 在中轨，1 = 在上轨。<0.1 接近下轨超卖，>0.9 接近上轨超买。",
        _def([_sub_ind(
            "pos", "布林位置(0~1)",
            _div(
                _sub(_intrinsic("close"), _ref("LOWER")),
                _sub(_ref("UPPER"), _ref("LOWER")),
            ),
        )]),
    ),
    (
        "tpl_kdj_j",
        "KDJ的J值【预置】",
        "引用内置 KDJ 的 J 线。J 值波动最剧烈，<20 为超卖区（买入信号），>80 为超买区（卖出信号）。",
        _def([_sub_ind("j", "J值", _ref("J"))]),
    ),
    (
        "tpl_cci",
        "CCI商品通道指数【预置】",
        "引用内置 CCI14。< -100 为超卖（潜在买入区），> +100 为超买（潜在卖出区）。",
        _def([_sub_ind("cci14", "CCI14", _ref("CCI14"))]),
    ),
    (
        "tpl_bias",
        "BIAS乖离率【预置】",
        "引用内置 BIAS12（收盘价偏离12日均线的百分比）。< -8% 表示价格明显低于均线，有均值回归机会。",
        _def([_sub_ind("bias12", "BIAS12(%)", _ref("BIAS12"))]),
    ),
    (
        "tpl_roc",
        "ROC变化率【预置】",
        "引用内置 ROC12（当日收盘价相比12日前的涨跌幅%）。>0 表示上升动量，<0 表示下降动量。",
        _def([_sub_ind("roc12", "ROC12(%)", _ref("ROC12"))]),
    ),
    (
        "tpl_vol_ratio",
        "量比（成交量/VMA20）【预置】",
        "当日成交量与20日量能均线的比值。>2 表示显著放量（资金关注），<0.5 表示明显缩量。",
        _def([_sub_ind(
            "vol_ratio", "量比(vol/VMA20)",
            _div(_intrinsic("volume"), _ref("VMA20")),
        )]),
    ),
    (
        "tpl_trix",
        "TRIX三重指数平滑【预置】",
        "引用内置 TRIX12（三重指数平滑变化率%）。>0 表示趋势向上，上穿0轴为买入信号；<0 且下穿0轴为卖出信号。",
        _def([_sub_ind("trix12", "TRIX12(%)", _ref("TRIX12"))]),
    ),
]


def ensure_default_user_indicators(db: Session) -> None:
    """写入预置自定义指标，已存在（按 code 查重）则跳过。"""
    for code, display_name, description, def_dict in _SEED:
        exists = db.query(UserIndicator).filter(UserIndicator.code == code).one_or_none()
        if exists:
            continue
        try:
            parsed = parse_and_validate_definition(db, def_dict)
            dj = definition_to_storable(parsed)
        except ValueError as e:
            log.warning("预置指标 %s 校验失败，跳过：%s", code, e)
            continue
        row = UserIndicator(
            code=code,
            display_name=display_name,
            description=description,
            expr="",
            definition_json=dj,
        )
        db.add(row)
        log.info("写入预置指标: %s", code)
    db.commit()
