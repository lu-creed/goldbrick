"""用户指标 DSL：校验与试算回归。"""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Indicator, IndicatorSubIndicator
from app.services.user_indicator_compute import compute_definition_series
from app.services.user_indicator_dsl import parse_and_validate_definition


def _session_with_ma_sub() -> sessionmaker:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    db = S()
    ind = Indicator(name="MA", display_name="MA", description="x")
    db.add(ind)
    db.flush()
    db.add(IndicatorSubIndicator(indicator_id=ind.id, name="MA5", description="", can_be_price=False))
    db.commit()
    return db


def _bars(n: int = 40):
    out = []
    for i in range(n):
        out.append(
            SimpleNamespace(
                trade_date=date(2024, 1, 1) + timedelta(days=i),
                open=10.0,
                high=11.0,
                low=9.0,
                close=10.0 + i * 0.1,
                volume=100,
                amount=1000.0,
                turnover_rate=1.0,
            )
        )
    return out


class UserIndicatorDslTest(unittest.TestCase):
    def test_parse_range_close_avg(self) -> None:
        db = _session_with_ma_sub()
        d = {
            "version": 1,
            "params": [{"name": "N", "description": "", "default_value": "3"}],
            "periods": ["1d"],
            "sub_indicators": [
                {
                    "key": "raw",
                    "name": "均线",
                    "auxiliary_only": False,
                    "use_in_screening": True,
                    "use_in_chart": True,
                    "chart_kind": "line",
                    "formula": {
                        "op": "ref_builtin",
                        "sub_name": "MA5",
                        "fetch": {"mode": "current"},
                    },
                }
            ],
        }
        p = parse_and_validate_definition(db, d)
        self.assertEqual(p.version, 1)

    def test_reject_cycle(self) -> None:
        db = _session_with_ma_sub()
        d = {
            "version": 1,
            "params": [],
            "periods": ["1d"],
            "sub_indicators": [
                {
                    "key": "a",
                    "name": "a",
                    "auxiliary_only": False,
                    "use_in_screening": True,
                    "use_in_chart": True,
                    "chart_kind": "line",
                    "formula": {"op": "ref_sibling", "sub_key": "b", "fetch": {"mode": "current"}},
                },
                {
                    "key": "b",
                    "name": "b",
                    "auxiliary_only": False,
                    "use_in_screening": True,
                    "use_in_chart": True,
                    "chart_kind": "line",
                    "formula": {"op": "ref_sibling", "sub_key": "a", "fetch": {"mode": "current"}},
                },
            ],
        }
        with self.assertRaises(ValueError):
            parse_and_validate_definition(db, d)

    def test_parse_rolling_close_avg(self) -> None:
        db = _session_with_ma_sub()
        d = {
            "version": 1,
            "params": [{"name": "N", "description": "", "default_value": "3"}],
            "periods": ["1d"],
            "sub_indicators": [
                {
                    "key": "r",
                    "name": "roll",
                    "auxiliary_only": False,
                    "use_in_screening": True,
                    "use_in_chart": True,
                    "chart_kind": "line",
                    "formula": {"op": "rolling", "field": "close", "n_param": "N", "stat": "avg"},
                }
            ],
        }
        parse_and_validate_definition(db, d)

    def test_compute_builtin_ma5_tail(self) -> None:
        db = _session_with_ma_sub()
        d = {
            "version": 1,
            "params": [],
            "periods": ["1d"],
            "sub_indicators": [
                {
                    "key": "x",
                    "name": "x",
                    "auxiliary_only": False,
                    "use_in_screening": True,
                    "use_in_chart": True,
                    "chart_kind": "line",
                    "formula": {
                        "op": "ref_builtin",
                        "sub_name": "MA5",
                        "fetch": {"mode": "current"},
                    },
                }
            ],
        }
        p = parse_and_validate_definition(db, d)
        bars = _bars(40)
        series = compute_definition_series(p, bars)
        self.assertIsNotNone(series["x"][-1])
        self.assertIsNone(series["x"][2])


if __name__ == "__main__":
    unittest.main()
