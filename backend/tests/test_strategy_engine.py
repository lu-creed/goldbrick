"""strategy_engine 模块：编译 + 求值 + dry-run 的单元测试。

用内存 SQLite + 构造的 bars 直接覆盖核心场景：
  - 编译正确 / 缺指标 / 子线无效 / 组合/主条件异常
  - 单 bar 求值：AND / OR / NOT / 嵌套 / 缺数据
  - 序列求值：多日结果
  - 同一指标多条件只调一次 DSL 序列（性能）
"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Indicator, IndicatorSubIndicator, UserIndicator
from app.services import strategy_engine as se


def _make_session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    db = S()
    # 种子内置指标 MA（供 ref_builtin 引用）
    ind = Indicator(name="MA", display_name="MA", description="")
    db.add(ind)
    db.flush()
    db.add(IndicatorSubIndicator(indicator_id=ind.id, name="MA5", description="", can_be_price=False))
    db.commit()
    return db


def _dsl_ref_close():
    """DSL 指标：单子线 x = close（当日收盘价）。"""
    return {
        "version": 1,
        "params": [],
        "periods": ["1d"],
        "sub_indicators": [{
            "key": "x",
            "name": "x",
            "auxiliary_only": False,
            "use_in_screening": True,
            "use_in_chart": True,
            "chart_kind": "line",
            "formula": {"op": "intrinsic", "field": "close"},
        }],
    }


def _dsl_ref_ma5():
    """DSL 指标：单子线 y = MA5（内置 MA 指标的 MA5 子线当日值）。"""
    return {
        "version": 1,
        "params": [],
        "periods": ["1d"],
        "sub_indicators": [{
            "key": "y",
            "name": "y",
            "auxiliary_only": False,
            "use_in_screening": True,
            "use_in_chart": True,
            "chart_kind": "line",
            "formula": {"op": "ref_builtin", "sub_name": "MA5", "fetch": {"mode": "current"}},
        }],
    }


def _seed_user_indicator(db, code: str, definition: dict) -> int:
    """写入一个 DSL 自定义指标，返回其 id。"""
    ui = UserIndicator(
        user_id=None,
        code=code,
        display_name=code,
        description="",
        expr="",
        definition_json=json.dumps(definition, ensure_ascii=False),
    )
    db.add(ui)
    db.commit()
    db.refresh(ui)
    return ui.id


def _bars(n: int, start_close: float = 10.0, step: float = 0.5):
    """生成线性递增收盘价的 n 根 K 线。"""
    out = []
    d0 = date(2024, 1, 1)
    for i in range(n):
        c = start_close + i * step
        out.append(SimpleNamespace(
            trade_date=d0 + timedelta(days=i),
            open=c, high=c + 0.1, low=c - 0.1, close=c,
            volume=100, amount=1000.0, turnover_rate=1.0,
        ))
    return out


# ─────────────────────────────────────────────────────────────
# 编译
# ─────────────────────────────────────────────────────────────

class CompileStrategyTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_session()
        self.uid_close = _seed_user_indicator(self.db, "ind_close", _dsl_ref_close())
        self.uid_ma5 = _seed_user_indicator(self.db, "ind_ma5", _dsl_ref_ma5())

    def _logic(self, **kw):
        """默认：单条件 close>0，单组 G1。"""
        return {
            "conditions": kw.get("conditions", [
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "gt", "threshold": 0.0},
            ]),
            "groups": kw.get("groups", [{"id": "G1", "condition_ids": [1]}]),
            "combiner": kw.get("combiner", {"ref": "G1"}),
            "primary_condition_id": kw.get("primary_condition_id", 1),
        }

    def test_happy_path(self):
        c = se.compile_strategy(self.db, self._logic())
        self.assertEqual(len(c.conditions), 1)
        self.assertEqual(len(c.groups), 1)
        self.assertEqual(c.primary_cond_id, 1)
        self.assertIn(self.uid_close, c.indicators)
        self.assertTrue(c.indicators[self.uid_close].is_dsl)

    def test_missing_indicator(self):
        with self.assertRaises(ValueError) as cm:
            se.compile_strategy(self.db, self._logic(conditions=[
                {"id": 1, "user_indicator_id": 999999, "sub_key": "x",
                 "compare_op": "gt", "threshold": 0.0},
            ]))
        self.assertIn("不存在", str(cm.exception))

    def test_invalid_sub_key(self):
        with self.assertRaises(ValueError) as cm:
            se.compile_strategy(self.db, self._logic(conditions=[
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "NOT_A_KEY",
                 "compare_op": "gt", "threshold": 0.0},
            ]))
        self.assertIn("NOT_A_KEY", str(cm.exception))

    def test_missing_sub_key_defaults_to_first(self):
        c = se.compile_strategy(self.db, self._logic(conditions=[
            {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "",
             "compare_op": "gt", "threshold": 0.0},
        ]))
        self.assertEqual(c.conditions[0].sub_key, "x")

    def test_invalid_compare_op(self):
        with self.assertRaises(ValueError):
            se.compile_strategy(self.db, self._logic(conditions=[
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "banana", "threshold": 0.0},
            ]))

    def test_group_refs_unknown_condition(self):
        with self.assertRaises(ValueError):
            se.compile_strategy(self.db, self._logic(groups=[
                {"id": "G1", "condition_ids": [1, 99]},
            ]))

    def test_combiner_refs_unknown_group(self):
        with self.assertRaises(ValueError):
            se.compile_strategy(self.db, self._logic(combiner={"ref": "Gx"}))

    def test_primary_not_in_conditions(self):
        with self.assertRaises(ValueError):
            se.compile_strategy(self.db, self._logic(primary_condition_id=99))

    def test_duplicate_condition_id(self):
        with self.assertRaises(ValueError):
            se.compile_strategy(self.db, self._logic(conditions=[
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "gt", "threshold": 0.0},
                {"id": 1, "user_indicator_id": self.uid_ma5, "sub_key": "y",
                 "compare_op": "gt", "threshold": 0.0},
            ], groups=[{"id": "G1", "condition_ids": [1]}]))


# ─────────────────────────────────────────────────────────────
# 单 bar 求值
# ─────────────────────────────────────────────────────────────

class EvalOnBarsTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_session()
        self.uid_close = _seed_user_indicator(self.db, "ind_close", _dsl_ref_close())
        self.uid_ma5 = _seed_user_indicator(self.db, "ind_ma5", _dsl_ref_ma5())

    def _two_cond_logic(self, combiner, *, close_thr=15.0, ma5_thr=14.0, primary=1):
        """构造：c1=close>close_thr（G1），c2=MA5>ma5_thr（G2）。"""
        return {
            "conditions": [
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "gt", "threshold": close_thr},
                {"id": 2, "user_indicator_id": self.uid_ma5, "sub_key": "y",
                 "compare_op": "gt", "threshold": ma5_thr},
            ],
            "groups": [
                {"id": "G1", "condition_ids": [1]},
                {"id": "G2", "condition_ids": [2]},
            ],
            "combiner": combiner,
            "primary_condition_id": primary,
        }

    def test_and_both_pass(self):
        bars = _bars(20, start_close=10.0, step=1.0)  # 最后一根 close=29
        compiled = se.compile_strategy(
            self.db,
            self._two_cond_logic({"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]})
        )
        hit, primary, vals = se.eval_strategy_on_bars(compiled, bars)
        self.assertTrue(hit)
        self.assertEqual(primary, 29.0)
        self.assertEqual(vals[1], 29.0)
        # MA5 的最后一根 = (25+26+27+28+29)/5 = 27.0
        self.assertAlmostEqual(vals[2], 27.0, places=6)

    def test_and_one_fails(self):
        bars = _bars(20, start_close=10.0, step=1.0)
        compiled = se.compile_strategy(
            self.db,
            self._two_cond_logic(
                {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                ma5_thr=100.0,  # MA5=27 < 100，G2 失败
            )
        )
        hit, _, _ = se.eval_strategy_on_bars(compiled, bars)
        self.assertFalse(hit)

    def test_or_one_pass(self):
        bars = _bars(20, start_close=10.0, step=1.0)
        compiled = se.compile_strategy(
            self.db,
            self._two_cond_logic(
                {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                close_thr=100.0,  # close=29 < 100，G1 失败
                ma5_thr=10.0,      # MA5=27 > 10，G2 通过
            )
        )
        hit, _, _ = se.eval_strategy_on_bars(compiled, bars)
        self.assertTrue(hit)

    def test_nested_and_or(self):
        # (G1 AND G2) OR G_NEVER  → 等同 G1 AND G2
        # 这里造 3 组以覆盖嵌套；但保持简单用两组 + NOT
        bars = _bars(20, start_close=10.0, step=1.0)
        # NOT G1 → close>30 的 NOT → close=29 不满足 →_True
        logic = {
            "conditions": [
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "gt", "threshold": 30.0},
                {"id": 2, "user_indicator_id": self.uid_ma5, "sub_key": "y",
                 "compare_op": "gt", "threshold": 10.0},
            ],
            "groups": [
                {"id": "G1", "condition_ids": [1]},
                {"id": "G2", "condition_ids": [2]},
            ],
            "combiner": {"op": "AND", "args": [
                {"op": "NOT", "args": [{"ref": "G1"}]},
                {"ref": "G2"},
            ]},
            "primary_condition_id": 2,
        }
        compiled = se.compile_strategy(self.db, logic)
        hit, primary, _ = se.eval_strategy_on_bars(compiled, bars)
        self.assertTrue(hit)
        self.assertAlmostEqual(primary, 27.0, places=6)

    def test_primary_value_from_primary_cond(self):
        bars = _bars(20, start_close=10.0, step=1.0)
        compiled = se.compile_strategy(
            self.db,
            self._two_cond_logic(
                {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                primary=2,  # 主条件 = MA5
            )
        )
        _, primary, _ = se.eval_strategy_on_bars(compiled, bars)
        self.assertAlmostEqual(primary, 27.0, places=6)

    def test_insufficient_bars_returns_false(self):
        # 只有 3 根 K 线，MA5 算不出来 → c2 的值 None → G2=False → AND=False
        bars = _bars(3, start_close=10.0, step=1.0)
        compiled = se.compile_strategy(
            self.db,
            self._two_cond_logic({"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]})
        )
        hit, _, vals = se.eval_strategy_on_bars(compiled, bars)
        self.assertFalse(hit)
        self.assertIsNone(vals.get(2))

    def test_same_indicator_shared_between_conditions(self):
        """两个条件引用同一指标的同一子线，但阈值不同。应都能按各自阈值判布尔。"""
        bars = _bars(20, start_close=10.0, step=1.0)  # close 最后一根 = 29
        logic = {
            "conditions": [
                {"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "gt", "threshold": 5.0},   # 29 > 5 ✓
                {"id": 2, "user_indicator_id": self.uid_close, "sub_key": "x",
                 "compare_op": "lt", "threshold": 100.0}, # 29 < 100 ✓
            ],
            "groups": [
                {"id": "G1", "condition_ids": [1]},
                {"id": "G2", "condition_ids": [2]},
            ],
            "combiner": {"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]},
            "primary_condition_id": 1,
        }
        compiled = se.compile_strategy(self.db, logic)
        hit, primary, vals = se.eval_strategy_on_bars(compiled, bars)
        self.assertTrue(hit)
        self.assertEqual(vals[1], 29.0)
        self.assertEqual(vals[2], 29.0)


# ─────────────────────────────────────────────────────────────
# 序列求值
# ─────────────────────────────────────────────────────────────

class EvalOnSeriesTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_session()
        self.uid_close = _seed_user_indicator(self.db, "ind_close", _dsl_ref_close())

    def test_series_filtered_by_range(self):
        """close > 15 的日期集合（close 是 10, 11, ..., 29）"""
        bars = _bars(20, start_close=10.0, step=1.0)
        logic = {
            "conditions": [{"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                            "compare_op": "gt", "threshold": 15.0}],
            "groups": [{"id": "G1", "condition_ids": [1]}],
            "combiner": {"ref": "G1"},
            "primary_condition_id": 1,
        }
        compiled = se.compile_strategy(self.db, logic)
        # 只看 2024-01-10 ~ 2024-01-20（索引 9~19，close=19~29）
        result = se.eval_strategy_on_series(
            compiled, bars, date(2024, 1, 10), date(2024, 1, 20)
        )
        # 2024-01-10 索引 9 → close=19 → 19>15 命中
        # 2024-01-06 索引 5 → close=15 → 不在日期范围，不出现
        self.assertEqual(len(result), 11)
        self.assertTrue(all(v[0] for v in result.values()))  # 全部 True
        self.assertEqual(result[date(2024, 1, 10)][1], 19.0)


# ─────────────────────────────────────────────────────────────
# dry-run
# ─────────────────────────────────────────────────────────────

class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.db = _make_session()
        self.uid_close = _seed_user_indicator(self.db, "ind_close", _dsl_ref_close())

    def test_dry_run_returns_details(self):
        bars = _bars(20, start_close=10.0, step=1.0)
        logic = {
            "conditions": [{"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                            "compare_op": "gt", "threshold": 10.0}],
            "groups": [{"id": "G1", "condition_ids": [1]}],
            "combiner": {"ref": "G1"},
            "primary_condition_id": 1,
        }
        compiled = se.compile_strategy(self.db, logic)
        result = se.dry_run_on_bars(compiled, bars)
        self.assertTrue(result["hit"])
        self.assertEqual(result["primary_value"], 29.0)
        self.assertEqual(len(result["conditions"]), 1)
        self.assertTrue(result["conditions"][0]["satisfied"])
        self.assertEqual(result["conditions"][0]["indicator_value"], 29.0)
        self.assertEqual(len(result["groups"]), 1)
        self.assertTrue(result["groups"][0]["satisfied"])

    def test_dry_run_empty_bars(self):
        compiled = se.compile_strategy(self.db, {
            "conditions": [{"id": 1, "user_indicator_id": self.uid_close, "sub_key": "x",
                            "compare_op": "gt", "threshold": 10.0}],
            "groups": [{"id": "G1", "condition_ids": [1]}],
            "combiner": {"ref": "G1"},
            "primary_condition_id": 1,
        })
        result = se.dry_run_on_bars(compiled, [])
        self.assertFalse(result["hit"])
        self.assertIsNone(result["primary_value"])
        self.assertIn("无 K 线", result.get("note", ""))


if __name__ == "__main__":
    unittest.main()
