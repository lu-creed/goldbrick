"""backtest_runner 成本模型与成交价模式的小数据集单测。

手算校验：
- 初始 100,000，max_positions=1（单仓）
- commission_rate=0.00025、commission_min=5、stamp_duty_rate=0.001、slippage_bps=10
- lot_size=100，execution_price='close'：T 日信号 T 日成交
- 股票 A：T 日价 10.00 触发买，T+1 价 11.00 触发卖

买入：
  buy_price = 10.00 × (1 + 0.001) = 10.01
  shares 目标 = floor((100000 - max(5, 100000×0.00025)) / 10.01 / 100) × 100
             = floor((100000 - 25) / 10.01 / 100) × 100
             = floor(99.87512...) × 100 = 9900
  gross = 9900 × 10.01 = 99099.00
  fee   = max(5, 99099 × 0.00025) = max(5, 24.77475) = 24.77
  cost_basis = 99099.00 + 24.77 = 99123.77
  cash = 100000 - 99123.77 = 876.23

卖出：
  sell_price = 11.00 × (1 - 0.001) = 10.989
  gross = 9900 × 10.989 = 108791.10
  fee   = max(5, 108791.10 × 0.00025) = 27.19778 ≈ 27.20
  stamp = 108791.10 × 0.001 = 108.7911 ≈ 108.79
  net   = 108791.10 - 27.20 - 108.79 = 108655.11
  pnl   = 108655.11 - 99123.77 = 9531.34

commission_cost_total = 24.77 + 27.20 + 108.79 = 160.76
"""
from __future__ import annotations

import math

from app.services import backtest_runner


def test_compute_commission_min_floor() -> None:
    """成交金额很小时，佣金按最低 5 元。"""
    assert math.isclose(backtest_runner._compute_commission(1000.0, 0.00025, 5.0), 5.0)


def test_compute_commission_rate_applied() -> None:
    """成交金额较大时，佣金按费率收。"""
    # 1,000,000 × 0.00025 = 250 > 最低 5
    assert math.isclose(backtest_runner._compute_commission(1_000_000.0, 0.00025, 5.0), 250.0)


def test_commission_min_is_per_leg() -> None:
    """边界：成交金额刚好在费率 = 最低 5 元的转折点。"""
    # gross * 0.00025 = 5  →  gross = 20_000
    assert math.isclose(backtest_runner._compute_commission(20_000.0, 0.00025, 5.0), 5.0)
    assert math.isclose(backtest_runner._compute_commission(20_001.0, 0.00025, 5.0), 20_001.0 * 0.00025)
