"""时区处理工具 - 统一时间存储和显示"""
from datetime import datetime, timezone, timedelta

# 北京时间 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))


def utc_now() -> datetime:
    """获取当前 UTC 时间（带时区信息）"""
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    """获取当前北京时间（带时区信息）"""
    return datetime.now(BEIJING_TZ)


def to_utc(dt: datetime) -> datetime:
    """将时间转换为 UTC"""
    if dt.tzinfo is None:
        # 假设无时区的时间是北京时间
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(timezone.utc)


def to_beijing(dt: datetime) -> datetime:
    """将时间转换为北京时间"""
    if dt.tzinfo is None:
        # 假设无时区的时间是 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ)


def format_beijing(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化为北京时间字符串"""
    return to_beijing(dt).strftime(fmt)


def to_iso_utc(dt: datetime) -> str:
    """转换为 ISO 格式的 UTC 时间字符串（带 Z 后缀）"""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_with_tz(dt: datetime) -> str:
    """转换为 ISO 格式字符串（带时区偏移）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
