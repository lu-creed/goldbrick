"""
安全中间件（0.0.4-dev）：API 限流 + IP 白名单。

开关通过 `settings.rate_limit_enabled` 控制，未开启时所有装饰器/中间件都是 no-op，
本地开发不受影响；公测/上线前把 .env 里 RATE_LIMIT_ENABLED=true 打开。

## IP 白名单两层

- `settings.ip_whitelist`：全站白名单，对所有 /api/* 请求生效；空字符串=放开。
- `settings.admin_ip_whitelist`：仅对管理员敏感端点生效（同步、Tushare token、自动更新、
  用户管理）；空时回退到 `ip_whitelist`；两层都空则完全放开。

白名单条目支持：
  - 单 IP：  "127.0.0.1"
  - CIDR： "10.0.0.0/8" / "100.64.0.0/10"
  - 本机别名："localhost" 会等价于 127.0.0.1 / ::1

## 限流

Limiter 以「客户端 IP」为 key，依赖 slowapi；`@limiter.limit("5/minute")` 挂到路由即可。
触发限流返回 429 + Retry-After 头。
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Iterable, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

log = logging.getLogger(__name__)


# ── 限流器（全局单例，路由处通过 @limiter.limit 装饰）────────────────────
# 未启用时返回一个 no-op 装饰器工厂，避免路由代码分两套
class _NoOpLimiter:
    """当限流关闭时，模拟 slowapi Limiter 的 limit 装饰器接口但不做任何事。"""
    def limit(self, *_args, **_kwargs):
        def deco(fn):
            return fn
        return deco


def _make_limiter():
    if not settings.rate_limit_enabled:
        return _NoOpLimiter()
    return Limiter(
        key_func=get_remote_address,
        default_limits=[settings.rate_limit_default],
        headers_enabled=True,
    )


limiter = _make_limiter()


# ── IP 白名单解析 ────────────────────────────────────────────────
def _parse_whitelist(raw: str) -> list[ipaddress._BaseNetwork]:
    """把逗号分隔的 IP / CIDR 字符串解析成 ip_network 列表。

    无法解析的条目被跳过并打 warning。返回空列表表示"未配置"（在中间件里等价放开）。
    """
    out: list[ipaddress._BaseNetwork] = []
    if not raw or not raw.strip():
        return out
    for item in raw.split(","):
        s = item.strip()
        if not s:
            continue
        if s.lower() == "localhost":
            out.append(ipaddress.ip_network("127.0.0.1/32"))
            out.append(ipaddress.ip_network("::1/128"))
            continue
        try:
            # 单 IP 不带 /N 时 strict=False 自动补 /32 或 /128
            out.append(ipaddress.ip_network(s, strict=False))
        except ValueError:
            log.warning("ip_whitelist: 忽略无法解析的条目 %r", s)
    return out


_GLOBAL_WHITELIST = _parse_whitelist(settings.ip_whitelist)
_ADMIN_WHITELIST_RAW = _parse_whitelist(settings.admin_ip_whitelist)
# admin_ip_whitelist 为空时回退到全站白名单
_ADMIN_WHITELIST = _ADMIN_WHITELIST_RAW or _GLOBAL_WHITELIST


# 管理员端点前缀：以 /api 开头再加这些前缀的请求走 admin 白名单
_ADMIN_PATH_PREFIXES: tuple[str, ...] = (
    "/api/sync",
    "/api/admin-tushare",
    "/api/auto-update",
    "/api/auth/users",      # 用户管理（仅 admin 能访问但也加 IP 白名单作第二道锁）
)

# 无条件放行的路径（健康检查、OpenAPI 文档）
_BYPASS_PATHS: tuple[str, ...] = (
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _ip_in_whitelist(ip_str: str, whitelist: Iterable[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for net in whitelist:
        if ip in net:
            return True
    return False


def _client_ip(request: Request) -> Optional[str]:
    # 优先用 X-Forwarded-For（部署在 Nginx 后时）；取最左一跳
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """按 path 前缀选择白名单并放行/拒绝。配置全空时中间件完全透明。"""

    async def dispatch(self, request: Request, call_next):
        # 只作用于 /api/*，静态资源和前端路由不拦截
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        if request.url.path in _BYPASS_PATHS:
            return await call_next(request)

        is_admin_path = any(request.url.path.startswith(p) for p in _ADMIN_PATH_PREFIXES)
        whitelist = _ADMIN_WHITELIST if is_admin_path else _GLOBAL_WHITELIST
        if not whitelist:
            return await call_next(request)

        ip = _client_ip(request)
        if ip and _ip_in_whitelist(ip, whitelist):
            return await call_next(request)

        log.warning(
            "IP 白名单拒绝：path=%s ip=%s admin=%s", request.url.path, ip, is_admin_path
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"code": "ip_blocked", "message": "请求来源 IP 未在白名单内"},
        )


# ── 接入 FastAPI ────────────────────────────────────────────────
def install_security(app: FastAPI) -> None:
    """在 main.py lifespan 之前调用，把限流器/中间件挂到 app 上。

    只要 settings.rate_limit_enabled=False 且两份白名单都空，此函数不产生任何行为影响。
    """
    if settings.rate_limit_enabled:
        # slowapi 要求 app.state.limiter = limiter
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
        log.info(
            "rate limit enabled: default=%s login=%s",
            settings.rate_limit_default, settings.rate_limit_login,
        )
    if _GLOBAL_WHITELIST or _ADMIN_WHITELIST:
        app.add_middleware(IPWhitelistMiddleware)
        log.info(
            "ip whitelist: global=%d nets, admin=%d nets",
            len(_GLOBAL_WHITELIST), len(_ADMIN_WHITELIST),
        )


__all__ = ["limiter", "install_security", "HTTPException"]
