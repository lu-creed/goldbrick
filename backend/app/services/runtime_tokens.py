from __future__ import annotations

import threading
from typing import Optional

from app.config import settings
from app.database import SessionLocal
from app.models import AppSetting

_lock = threading.Lock()
_runtime_tushare_token: Optional[str] = None
_TUSHARE_TOKEN_KEY = "tushare_token"
_STOCK_LIST_LAST_SYNC_KEY = "stock_list_last_sync_date"


def _get_persisted_token() -> Optional[str]:
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _TUSHARE_TOKEN_KEY).one_or_none()
        if not row:
            return None
        token = (row.value or "").strip()
        return token or None
    finally:
        db.close()


def _set_persisted_token(token: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _TUSHARE_TOKEN_KEY).one_or_none()
        if not row:
            row = AppSetting(key=_TUSHARE_TOKEN_KEY, value=token)
            db.add(row)
        else:
            row.value = token
        db.commit()
    finally:
        db.close()


def get_tushare_token() -> Optional[str]:
    """优先级：运行时内存 > DB持久化 > .env。"""
    with _lock:
        if _runtime_tushare_token:
            return _runtime_tushare_token
    persisted = _get_persisted_token()
    if persisted:
        return persisted
    if settings.tushare_token:
        return settings.tushare_token
    return None


def set_runtime_tushare_token(token: str) -> None:
    token = (token or "").strip()
    if not token:
        raise ValueError("token is empty")
    _set_persisted_token(token)
    with _lock:
        global _runtime_tushare_token
        _runtime_tushare_token = token


def get_tushare_token_status() -> dict:
    """只返回是否配置，不返回 token 明文。"""
    with _lock:
        has_runtime = bool(_runtime_tushare_token)
    has_db = bool(_get_persisted_token())
    has_env = bool(settings.tushare_token)
    db = SessionLocal()
    try:
        row = db.query(AppSetting).filter(AppSetting.key == _STOCK_LIST_LAST_SYNC_KEY).one_or_none()
        stock_list_last_sync_date = (row.value or "").strip() if row and row.value else None
    finally:
        db.close()
    return {
        "hasRuntime": has_runtime,
        "hasDb": has_db,
        "hasEnv": has_env,
        "configured": has_runtime or has_db or has_env,
        "stockListLastSyncDate": stock_list_last_sync_date,
    }

