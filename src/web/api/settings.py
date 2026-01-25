import os
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import AppSettings
from src.config import Settings

router = APIRouter()


def get_app_version() -> str:
    """获取应用版本号"""
    # 优先从环境变量读取
    version = os.getenv("APP_VERSION")
    if version:
        return version

    # 从 VERSION 文件读取（支持多个位置）
    possible_paths = [
        "VERSION",  # 当前工作目录（开发和生产）
        os.path.join(os.path.dirname(__file__), "../../../VERSION"),  # 相对于本文件
    ]
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return "dev"


class SettingUpdate(BaseModel):
    value: str


class SettingResponse(BaseModel):
    key: str
    value: str
    description: str

    class Config:
        from_attributes = True


# 配置项描述
SETTING_DESCRIPTIONS = {
    "http_proxy": "HTTP 代理地址",
}

SETTING_KEYS = list(SETTING_DESCRIPTIONS.keys())


def _get_env_defaults() -> dict[str, str]:
    """从 .env / 环境变量读取当前值作为默认"""
    s = Settings()
    return {
        "http_proxy": s.http_proxy,
    }


@router.get("", response_model=list[SettingResponse])
def list_settings(db: Session = Depends(get_db)):
    settings = db.query(AppSettings).all()
    existing_map = {s.key: s for s in settings}

    env_defaults = _get_env_defaults()

    result = []
    for key in SETTING_KEYS:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        env_val = env_defaults.get(key, "")

        if key not in existing_map:
            s = AppSettings(key=key, value=env_val, description=desc)
            db.add(s)
            result.append(s)
        else:
            s = existing_map[key]
            if not s.description:
                s.description = desc
            result.append(s)
    db.commit()

    return result


@router.put("/{key}", response_model=SettingResponse)
def update_setting(key: str, update: SettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(AppSettings).filter(AppSettings.key == key).first()
    if not setting:
        desc = SETTING_DESCRIPTIONS.get(key, "")
        setting = AppSettings(key=key, value=update.value, description=desc)
        db.add(setting)
    else:
        setting.value = update.value

    db.commit()
    db.refresh(setting)
    return setting


@router.get("/version")
def get_version():
    """获取应用版本号"""
    return {"version": get_app_version()}
