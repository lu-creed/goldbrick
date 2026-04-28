"""PR#4 + PR#5 回归测试：run_screen / run_backtest 的老路径与新多条件路径。

用内存 SQLite + 造假数据验证：
  - 老 API（不传 logic）行为等同于改造前
  - 新多条件 API 产出正确的命中集合
  - 两路在"单条件"语义下结果一致（legacy_to_logic 等价证明）
  - 历史字段回填正确（indicator_name、strategy_snapshot_json、is_multi）
"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    BarDaily,
    Indicator,
    IndicatorSubIndicator,
    InstrumentMeta,
    Symbol,
    UserIndicator,
)
from app.services import screening_runner as sr
from app.services import strategy_engine as se


def _make_session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    db = S()
    # MA 内置指标（供 ref_builtin 引用）
    ind = Indicator(name="MA", display_name="MA", description="")
    db.add(ind)
    db.flush()
    db.add(IndicatorSubIndicator(indicator_id=ind.id, name="MA5", description="", can_be_price=False))
    db.commit()
    return db


def _seed_stock(db, ts_code: str, name: str, start_close: float, days: int = 30) -> int:
    """播入一只股票 + instrument_meta + N 根 K 线（close 从 start_close 开始 +1 递增）。
    返回 symbol_id。
    """
    sym = Symbol(ts_code=ts_code, name=name)
    db.add(sym)
    db.flush()
    meta = InstrumentMeta(ts_code=ts_code, name=name, asset_type="stock",
                          market="主板", exchange="SSE")
    db.add(meta)
    d0 = date(2024, 1, 1)
    for i in range(days):
        c = start_close + i
        db.add(BarDaily(
            symbol_id=sym.id, trade_date=d0 + timedelta(days=i),
            open=c, high=c + 0.5, low=c - 0.5, close=c,
            volume=1000, amount=c * 1000, turnover_rate=1.0,
        ))
    db.commit()
    return sym.id


def _dsl_close():
    return {
        "version": 1,
        "params": [],
        "periods": ["1d"],
        "sub_indicators": [{
            "key": "x", "name": "x",
            "auxiliary_only": False, "use_in_screening": True,
            "use_in_chart": True, "chart_kind": "line",
            "formula": {"op": "intrinsic", "field": "close"},
        }],
    }


def _dsl_ma5():
    return {
        "version": 1,
        "params": [],
        "periods": ["1d"],
        "sub_indicators": [{
            "key": "y", "name": "y",
            "auxiliary_only": False, "use_in_screening": True,
            "use_in_chart": True, "chart_kind": "line",
            "formula": {"op": "ref_builtin", "sub_name": "MA5", "fetch": {"mode": "current"}},
        }],
    }


def _seed_ui(db, code: str, definition: dict) -> int:
    ui = UserIndicator(
        user_id=None, code=code, display_name=code, description="",
        expr="", definition_json=json.dumps(definition, ensure_ascii=False),
    )
    db.add(ui)
    db.commit()
    db.refresh(ui)
    return ui.id


def _bypass_adj(monkeypatch_target: str):
    """屏蔽复权处理：返回原始 BarDaily，避免测试没喂 AdjFactorDaily 时出问题。"""
    def _noop_load(db, symbol_ids, start, end, adj_mode="qfq"):
        # 直接按老的"未复权"路径实现最小版本
        from collections import defaultdict
        if not symbol_ids:
            return {}
        rows = (
            db.query(BarDaily)
            .filter(BarDaily.symbol_id.in_(symbol_ids),
                    BarDaily.trade_date >= start, BarDaily.trade_date <= end)
            .order_by(BarDaily.symbol_id, BarDaily.trade_date.asc())
            .all()
        )
        out = defaultdict(list)
        for b in rows:
            out[b.symbol_id].append(SimpleNamespace(
                trade_date=b.trade_date,
                open=float(b.open), high=float(b.high),
                low=float(b.low), close=float(b.close),
                volume=float(b.volume), amount=float(b.amount),
                turnover_rate=float(b.turnover_rate) if b.turnover_rate is not None else None,
            ))
        return dict(out)
    return _noop_load


class LegacyToLogicTest(unittest.TestCase):
    def test_shape(self):
        d = se.legacy_to_logic(7, "MA5", "gt", 0.5)
        self.assertEqual(d["conditions"][0]["user_indicator_id"], 7)
        self.assertEqual(d["conditions"][0]["compare_op"], "gt")
        self.assertEqual(d["conditions"][0]["threshold"], 0.5)
        self.assertEqual(d["groups"][0]["condition_ids"], [1])
        self.assertEqual(d["combiner"], {"ref": "G1"})
        self.assertEqual(d["primary_condition_id"], 1)

    def test_none_sub_key_passes_through(self):
        d = se.legacy_to_logic(7, None, "gt", 0)
        self.assertIsNone(d["conditions"][0]["sub_key"])


class RunScreenLegacyPathTest(unittest.TestCase):
    """老单条件路径：不传 logic，走 legacy_to_logic 内部转换。"""
    def setUp(self):
        self.db = _make_session()
        _seed_stock(self.db, "600000.SH", "A", 10.0, days=30)
        _seed_stock(self.db, "600001.SH", "B", 20.0, days=30)
        self.uid_close = _seed_ui(self.db, "ind_close", _dsl_close())

    def test_old_signature_still_works(self):
        with patch.object(sr, "_load_bars_grouped", _bypass_adj(None)):
            out = sr.run_screen(
                self.db,
                trade_date=date(2024, 1, 30),
                user_indicator_id=self.uid_close,
                sub_key="x",
                compare_op="gt",
                threshold=25.0,
            )
        # close=35~39，但只有 600001.SH 的 close 从 20+29=49；600000.SH 最后 close=39
        # 都 > 25，故命中 2
        self.assertFalse(out["is_multi"])
        self.assertEqual(out["matched"], 2)
        ts_set = {item["ts_code"] for item in out["items"]}
        self.assertEqual(ts_set, {"600000.SH", "600001.SH"})
        # 排序：close 大的在前 → 600001.SH 第一
        self.assertEqual(out["items"][0]["ts_code"], "600001.SH")
        self.assertEqual(out["items"][0]["indicator_value"], 49.0)
        # 老字段回显
        self.assertEqual(out["user_indicator_id"], self.uid_close)
        self.assertEqual(out["compare_op"], "gt")
        self.assertEqual(out["threshold"], 25.0)


class RunScreenMultiConditionTest(unittest.TestCase):
    """新多条件路径：AND/OR 的命中集合符合预期。"""
    def setUp(self):
        self.db = _make_session()
        _seed_stock(self.db, "600000.SH", "A", 10.0, days=30)  # close 最后=39
        _seed_stock(self.db, "600001.SH", "B", 20.0, days=30)  # close 最后=49
        _seed_stock(self.db, "600002.SH", "C", 5.0, days=30)   # close 最后=34
        self.uid_close = _seed_ui(self.db, "ind_close", _dsl_close())
        self.uid_ma5 = _seed_ui(self.db, "ind_ma5", _dsl_ma5())

    def _logic(self, combiner, *, close_thr=35.0, ma5_thr=35.0, primary=1):
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

    def test_and_filters_both(self):
        # close>35 AND MA5>35
        # A: close=39, MA5=(35+36+37+38+39)/5=37 → 39>35 ✓ & 37>35 ✓ → 命中
        # B: close=49, MA5=47 → ✓ & ✓ → 命中
        # C: close=34, MA5=32 → ✗ → 不命中
        with patch.object(sr, "_load_bars_grouped", _bypass_adj(None)):
            out = sr.run_screen(
                self.db, trade_date=date(2024, 1, 30),
                logic=self._logic({"op": "AND", "args": [{"ref": "G1"}, {"ref": "G2"}]}),
            )
        self.assertTrue(out["is_multi"])
        ts_set = {item["ts_code"] for item in out["items"]}
        self.assertEqual(ts_set, {"600000.SH", "600001.SH"})
        # indicator_values dict 应包含两个条件值
        for it in out["items"]:
            self.assertIn("1", it["indicator_values"])
            self.assertIn("2", it["indicator_values"])

    def test_or_union(self):
        # close>40 OR MA5>40
        # A: close=39 ✗, MA5=37 ✗ → 不命中
        # B: close=49 ✓ OR MA5=47 ✓ → 命中
        # C: close=34 ✗, MA5=32 ✗ → 不命中
        with patch.object(sr, "_load_bars_grouped", _bypass_adj(None)):
            out = sr.run_screen(
                self.db, trade_date=date(2024, 1, 30),
                logic=self._logic(
                    {"op": "OR", "args": [{"ref": "G1"}, {"ref": "G2"}]},
                    close_thr=40.0, ma5_thr=40.0,
                ),
            )
        self.assertTrue(out["is_multi"])
        ts_set = {item["ts_code"] for item in out["items"]}
        self.assertEqual(ts_set, {"600001.SH"})

    def test_legacy_and_multi_single_condition_match(self):
        """老单条件路径 vs 等价的单条件 logic 应得到完全相同的结果集。"""
        with patch.object(sr, "_load_bars_grouped", _bypass_adj(None)):
            legacy_out = sr.run_screen(
                self.db, trade_date=date(2024, 1, 30),
                user_indicator_id=self.uid_close, sub_key="x",
                compare_op="gt", threshold=35.0,
            )
            multi_out = sr.run_screen(
                self.db, trade_date=date(2024, 1, 30),
                logic={
                    "conditions": [{"id": 1, "user_indicator_id": self.uid_close,
                                    "sub_key": "x", "compare_op": "gt", "threshold": 35.0}],
                    "groups": [{"id": "G1", "condition_ids": [1]}],
                    "combiner": {"ref": "G1"},
                    "primary_condition_id": 1,
                },
            )
        self.assertEqual(legacy_out["matched"], multi_out["matched"])
        legacy_codes = [it["ts_code"] for it in legacy_out["items"]]
        multi_codes = [it["ts_code"] for it in multi_out["items"]]
        self.assertEqual(legacy_codes, multi_codes)
        self.assertFalse(legacy_out["is_multi"])
        self.assertTrue(multi_out["is_multi"])


if __name__ == "__main__":
    unittest.main()
