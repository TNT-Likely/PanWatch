"""时区处理工具 - 统一时间存储和显示。

默认时区可通过环境变量覆盖：
- TZ（推荐）

未设置时默认 Asia/Shanghai。
"""

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo


def _get_app_tz() -> ZoneInfo:
    tz_name = os.environ.get("TZ") or os.environ.get("APP_TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    """获取当前 UTC 时间（带时区信息）"""
    return datetime.now(timezone.utc)


def app_now() -> datetime:
    """获取当前默认时区时间（带时区信息）"""
    return datetime.now(_get_app_tz())


def to_utc(dt: datetime) -> datetime:
    """将时间转换为 UTC"""
    if dt.tzinfo is None:
        # 假设无时区的时间是默认时区
        dt = dt.replace(tzinfo=_get_app_tz())
    return dt.astimezone(timezone.utc)


def to_app_timezone(dt: datetime) -> datetime:
    """将时间转换为默认时区"""
    if dt.tzinfo is None:
        # 假设无时区的时间是 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_get_app_tz())


def format_app_time(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化为默认时区字符串"""
    return to_app_timezone(dt).strftime(fmt)


def to_iso_utc(dt: datetime) -> str:
    """转换为 ISO 格式的 UTC 时间字符串（带 Z 后缀）"""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_with_tz(dt: datetime) -> str:
    """转换为 ISO 格式字符串（带时区偏移）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# Historical imports remain compatible; this clock follows deployment TZ.
beijing_now = app_now
to_beijing = to_app_timezone
format_beijing = format_app_time
