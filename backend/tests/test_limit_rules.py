"""涨跌停规则模块单元测试。"""
import unittest
from datetime import date

from app.services.limit_rules import (
    board_limit_pct,
    effective_limit_pct,
    hits_limit_down,
    hits_limit_up,
    is_ipo_trade_day,
    is_st_stock,
)


class TestLimitRules(unittest.TestCase):
    def test_is_st(self) -> None:
        self.assertTrue(is_st_stock("*ST康美"))
        self.assertTrue(is_st_stock("st景谷"))
        self.assertFalse(is_st_stock("贵州茅台"))

    def test_ipo_day(self) -> None:
        d = date(2024, 6, 1)
        self.assertTrue(is_ipo_trade_day(d, d))
        self.assertFalse(is_ipo_trade_day(d, date(2024, 6, 2)))
        self.assertFalse(is_ipo_trade_day(None, d))

    def test_board_pct(self) -> None:
        self.assertEqual(board_limit_pct("主板", "SSE", "600000.SH"), 0.10)
        self.assertEqual(board_limit_pct("创业板", "SZSE", "300001.SZ"), 0.20)
        self.assertEqual(board_limit_pct("科创板", "SSE", "688001.SH"), 0.20)
        self.assertEqual(board_limit_pct("", "BSE", "920001.BJ"), 0.30)

    def test_effective_order(self) -> None:
        d = date(2024, 1, 2)
        # ST 优先于板块
        self.assertEqual(effective_limit_pct("ST股", "创业板", "SZSE", "300001.SZ", d, date(2020, 1, 1)), 0.05)
        # 新股首日不计涨跌停
        self.assertIsNone(effective_limit_pct("新股", "主板", "SSE", "603000.SH", d, d))
        # 普通主板
        self.assertEqual(effective_limit_pct("平安", "主板", "SSE", "601318.SH", d, date(2007, 1, 1)), 0.10)

    def test_hits_limit(self) -> None:
        prev = 10.0
        self.assertTrue(hits_limit_up(10.99, prev, 0.10))  # ~9.9%
        self.assertFalse(hits_limit_up(10.5, prev, 0.10))
        self.assertFalse(hits_limit_up(11.0, prev, None))
        self.assertTrue(hits_limit_down(9.01, prev, 0.10))


if __name__ == "__main__":
    unittest.main()
