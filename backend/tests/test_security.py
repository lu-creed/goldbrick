"""app/security.py 单元测试：IP 白名单解析 + 匹配、限流装饰器的 no-op 路径。

不涉及 FastAPI 集成测试（那依赖 app.main 的 jose 导入，smoke 已知受限）；
这里只覆盖可以离线单测的纯逻辑。
"""
from __future__ import annotations

import ipaddress

import pytest

from app import security


def test_parse_whitelist_empty() -> None:
    assert security._parse_whitelist("") == []
    assert security._parse_whitelist("   ") == []
    assert security._parse_whitelist(None) == []  # type: ignore[arg-type]


def test_parse_whitelist_single_ip() -> None:
    nets = security._parse_whitelist("127.0.0.1")
    assert len(nets) == 1
    assert ipaddress.ip_address("127.0.0.1") in nets[0]


def test_parse_whitelist_cidr() -> None:
    nets = security._parse_whitelist("10.0.0.0/8, 192.168.1.0/24")
    assert len(nets) == 2
    assert ipaddress.ip_address("10.5.6.7") in nets[0]
    assert ipaddress.ip_address("192.168.1.100") in nets[1]
    assert ipaddress.ip_address("192.168.2.1") not in nets[1]


def test_parse_whitelist_localhost_alias() -> None:
    nets = security._parse_whitelist("localhost")
    assert any(ipaddress.ip_address("127.0.0.1") in n for n in nets)
    assert any(ipaddress.ip_address("::1") in n for n in nets)


def test_parse_whitelist_ignores_bad_entries(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        nets = security._parse_whitelist("127.0.0.1, not-an-ip, 10.0.0.0/8")
    assert len(nets) == 2
    assert any("not-an-ip" in rec.message for rec in caplog.records)


def test_ip_in_whitelist_matches() -> None:
    nets = security._parse_whitelist("10.0.0.0/8,100.64.0.0/10")
    assert security._ip_in_whitelist("10.1.2.3", nets) is True
    assert security._ip_in_whitelist("100.65.0.1", nets) is True
    assert security._ip_in_whitelist("8.8.8.8", nets) is False
    # 非法 IP 字符串应安全返回 False
    assert security._ip_in_whitelist("not-an-ip", nets) is False


def test_noop_limiter_decorator_passthrough() -> None:
    """默认 settings.rate_limit_enabled=False 时 limiter 是 no-op，装饰后函数行为不变。"""
    assert isinstance(security.limiter, security._NoOpLimiter)

    @security.limiter.limit("5/minute")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_bypass_paths_shape() -> None:
    """健康检查和 OpenAPI 路径必须在 bypass 列表里，保证监控/文档不被 IP 白名单挡住。"""
    assert "/api/health" in security._BYPASS_PATHS
    assert "/openapi.json" in security._BYPASS_PATHS


def test_admin_path_prefixes_cover_sensitive_routes() -> None:
    """管理员白名单必须覆盖同步、Tushare token、自动更新、用户管理入口。"""
    for p in ("/api/sync", "/api/admin-tushare", "/api/auto-update", "/api/auth/users"):
        assert p in security._ADMIN_PATH_PREFIXES
