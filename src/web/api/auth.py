"""认证 API - 简单的单用户 JWT 认证"""
import os
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import jwt

from src.web.database import get_db
from src.web.models import AppSettings

router = APIRouter()
security = HTTPBearer(auto_error=False)

# JWT 配置
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# 设置项 key
PASSWORD_HASH_KEY = "auth_password_hash"


class LoginRequest(BaseModel):
    password: str


class SetupRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    token: str
    expires_at: str


def hash_password(password: str) -> str:
    """简单的密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


def create_token(expires_days: int = JWT_EXPIRE_DAYS) -> tuple[str, datetime]:
    """创建 JWT token"""
    expires_at = datetime.utcnow() + timedelta(days=expires_days)
    payload = {
        "exp": expires_at,
        "iat": datetime.utcnow(),
        "sub": "user",
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires_at


def verify_token(token: str) -> bool:
    """验证 JWT token"""
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


def get_password_hash(db: Session) -> Optional[str]:
    """获取存储的密码哈希"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    return setting.value if setting else None


def set_password_hash(db: Session, password_hash: str):
    """设置密码哈希"""
    setting = db.query(AppSettings).filter(AppSettings.key == PASSWORD_HASH_KEY).first()
    if setting:
        setting.value = password_hash
    else:
        setting = AppSettings(key=PASSWORD_HASH_KEY, value=password_hash, description="认证密码哈希")
        db.add(setting)
    db.commit()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    """验证当前用户（用作依赖）"""
    # 检查是否已设置密码
    password_hash = get_password_hash(db)
    if not password_hash:
        # 未设置密码，允许访问（初始状态）
        return None

    # 已设置密码，需要验证 token
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "user"


@router.get("/status")
async def auth_status(db: Session = Depends(get_db)):
    """获取认证状态"""
    password_hash = get_password_hash(db)
    return {
        "initialized": password_hash is not None,
    }


@router.post("/setup", response_model=TokenResponse)
async def setup_password(data: SetupRequest, db: Session = Depends(get_db)):
    """首次设置密码"""
    if get_password_hash(db):
        raise HTTPException(400, "密码已设置，请使用登录接口")

    if len(data.password) < 6:
        raise HTTPException(400, "密码长度至少 6 位")

    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: Session = Depends(get_db)):
    """登录"""
    stored_hash = get_password_hash(db)
    if not stored_hash:
        raise HTTPException(400, "请先设置密码")

    if hash_password(data.password) != stored_hash:
        raise HTTPException(401, "密码错误")

    token, expires_at = create_token()
    return TokenResponse(token=token, expires_at=expires_at.isoformat())


@router.post("/change-password")
async def change_password(
    data: SetupRequest,
    db: Session = Depends(get_db),
    _: str = Depends(get_current_user),
):
    """修改密码"""
    if len(data.password) < 6:
        raise HTTPException(400, "密码长度至少 6 位")

    password_hash = hash_password(data.password)
    set_password_hash(db, password_hash)

    return {"message": "密码已更新"}


@router.get("/me")
async def get_me(user: str = Depends(get_current_user)):
    """获取当前用户信息"""
    return {"user": user or "guest"}
