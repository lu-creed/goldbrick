"""JWT 鉴权工具层：密码哈希、Token 创建/解码、FastAPI 依赖注入。"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(username: str) -> str:
    expire = datetime.now() + timedelta(days=settings.jwt_expire_days)
    return jwt.encode({"sub": username, "exp": expire}, settings.jwt_secret_key, algorithm="HS256")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """FastAPI 依赖：解析 Bearer Token，返回当前登录的 User ORM 对象。"""
    from app.models import User
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="认证失败，请重新登录",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        username: Optional[str] = payload.get("sub")
        if not username:
            raise exc
    except JWTError:
        raise exc
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if user is None:
        raise exc
    return user


def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: Session = Depends(get_db),
):
    """FastAPI 依赖（可选鉴权）：Token 有效返回 User；无 Token / Token 无效 / 用户不存在或被禁用一律返回 None，不抛 401。

    用于允许访客浏览但登录用户可获得个性化数据的公开接口。调用方可通过判断返回值是否为 None 区分访客态与登录态。
    """
    from app.models import User
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        username: Optional[str] = payload.get("sub")
        if not username:
            return None
    except JWTError:
        return None
    return db.query(User).filter(User.username == username, User.is_active.is_(True)).first()


def get_current_admin(current_user=Depends(get_current_user)):
    """FastAPI 依赖：在 get_current_user 基础上额外要求 is_admin=True。"""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return current_user
