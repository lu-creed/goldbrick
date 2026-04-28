"""用户鉴权 API（路径前缀 /api/auth/）。

接口列表：
  POST /api/auth/login                 登录，返回 JWT token
  POST /api/auth/register              注册（需开放注册开关）
  GET  /api/auth/me                    获取当前登录用户信息
  GET  /api/auth/users                 [admin] 用户列表
  POST /api/auth/users                 [admin] 创建用户
  PATCH /api/auth/users/{id}           [admin] 修改用户（重置密码/停用）
  PATCH /api/auth/settings/registration [admin] 开关开放注册
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_admin, get_current_user, get_password_hash, verify_password
from app.config import settings
from app.database import get_db
from app.models import AppSetting, User
from app.security import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

_ALLOW_REGISTRATION_KEY = "allow_registration"


# ── Schemas ──────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RegisterIn(BaseModel):
    username: str
    password: str


class UserCreateIn(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class UserPatchIn(BaseModel):
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class RegistrationSettingIn(BaseModel):
    allow: bool


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
@limiter.limit(settings.rate_limit_login)
def login(request: Request, body: LoginIn, db: Session = Depends(get_db)):
    """用户名 + 密码登录，返回 JWT Bearer Token。

    防暴力破解：限流按客户端 IP 计，默认 5/min（由 settings.rate_limit_login 控制）。
    未开启限流时 request 参数仍然保留（slowapi 装饰器的签名要求）。
    """
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    return TokenOut(access_token=create_access_token(user.username))


@router.post("/register", response_model=UserOut, status_code=201)
@limiter.limit(settings.rate_limit_login)
def register(request: Request, body: RegisterIn, db: Session = Depends(get_db)):
    """开放注册（仅当管理员已开启 allow_registration 时可用）。

    同 login：限流防刷。未开启限流时 no-op。
    """
    setting = db.query(AppSetting).filter(AppSetting.key == _ALLOW_REGISTRATION_KEY).first()
    if not setting or setting.value != "true":
        raise HTTPException(status_code=403, detail="注册功能未开放，请联系管理员创建账号")
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=username,
        hashed_password=get_password_hash(body.password),
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    """返回当前登录用户的基本信息（前端用于验证 Token 有效性）。"""
    return current_user


@router.get("/users", response_model=List[UserOut])
def list_users(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[管理员] 获取全部用户列表（按注册时间升序）。"""
    return db.query(User).order_by(User.created_at.asc()).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreateIn,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[管理员] 创建新用户（无需开放注册开关）。"""
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=username,
        hashed_password=get_password_hash(body.password),
        is_admin=body.is_admin,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserPatchIn,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[管理员] 修改用户信息（重置密码、启停账号、升降权限）。"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.password is not None:
        user.hashed_password = get_password_hash(body.password)
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    db.commit()
    db.refresh(user)
    return user


@router.get("/settings/registration")
def get_registration_setting(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[管理员] 查询当前开放注册开关状态。"""
    setting = db.query(AppSetting).filter(AppSetting.key == _ALLOW_REGISTRATION_KEY).first()
    allow = setting is not None and setting.value == "true"
    return {"allow_registration": allow}


@router.patch("/settings/registration")
def toggle_registration(
    body: RegistrationSettingIn,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[管理员] 开关开放注册功能（写入 app_settings 表）。"""
    value = "true" if body.allow else "false"
    setting = db.query(AppSetting).filter(AppSetting.key == _ALLOW_REGISTRATION_KEY).first()
    if setting:
        setting.value = value
    else:
        setting = AppSetting(key=_ALLOW_REGISTRATION_KEY, value=value)
        db.add(setting)
    db.commit()
    return {"allow_registration": body.allow}
