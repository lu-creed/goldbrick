"""Tushare 配额错误识别 + AKShare fallback 链路的单元测试。

不触碰真实网络：AKShare fetch 本身由 `fetch_stock_bars_akshare` 封装并有自己的重试机制，
这里只覆盖"识别 Tushare 配额错误 → 触发 fallback 分支"的判断逻辑与关键词。
"""
from __future__ import annotations

import pytest

from app.services.ingestion import _is_tushare_quota_error


@pytest.mark.parametrize(
    "msg",
    [
        "抱歉，您每分钟最多访问该接口500次",
        "抱歉，您每小时最多访问该接口2次",
        "抱歉，您每天最多访问该接口2次",
        "您没有调用该接口的权限，请购买相应积分",
        "您的积分不足，无法调用该接口",
        "超出限制：请稍后再试",
        "quota exceeded",
        "rate limit exceeded",
        "Permission denied",
    ],
)
def test_quota_error_recognized(msg: str) -> None:
    assert _is_tushare_quota_error(Exception(msg)) is True, msg


@pytest.mark.parametrize(
    "msg",
    [
        "Connection reset by peer",
        "ReadTimeoutError: HTTPSConnectionPool",
        "invalid ts_code: 999999.XX",
        "json decode error",
        "empty response",
    ],
)
def test_non_quota_error_not_recognized(msg: str) -> None:
    """网络 / 业务 / 格式错误不应被误识别为配额错误（否则会错误跳到 AKShare）。"""
    assert _is_tushare_quota_error(Exception(msg)) is False, msg
