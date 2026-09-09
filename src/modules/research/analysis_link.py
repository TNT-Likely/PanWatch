"""PanWatch 内部页面链接生成：深度分析详情页等。

全局设置 key: panwatch_base_url(公开访问地址,用于通知里的详情页绝对链接)。
读取模式与 stock_link.py 一致(AppSettings,miss 回退默认)。
"""

from __future__ import annotations

import logging

from src.web.database import SessionLocal
from src.web.models import AppSettings

logger = logging.getLogger(__name__)

SETTING_KEY = "panwatch_base_url"


def get_base_url() -> str:
    """从 AppSettings 读取公开访问地址(去尾部斜杠);未配置 / DB 不可用返回空串。

    包一层兜底:单测或 DB 未初始化(app_settings 表不存在)时,读取设置不应让整个
    分析结果映射崩掉 —— 读不到就降级为空串(不拼详情链接)。
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
            val = (row.value if row and row.value else "").strip()
            return val.rstrip("/")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — DB 未初始化/表缺失等均降级为空
        logger.debug(f"get_base_url 读取失败,降级为空: {e}")
        return ""


def analysis_detail_url(symbol: str, date: str, base_url: str = "") -> str:
    """深度分析详情页 URL: {base}/analysis/{symbol}/{date}。

    base_url 未配置(空)时返回空串 —— 调用方据此决定是否拼接链接。
    """
    if not base_url:
        base_url = get_base_url()
    if not base_url:
        return ""
    return f"{base_url}/analysis/{symbol}/{date}"


def analysis_detail_markdown(
    symbol: str, date: str, label: str = "📊 查看完整分析详情", base_url: str = ""
) -> str:
    """Markdown 链接 [label](url);无 base_url 时返回空串。"""
    url = analysis_detail_url(symbol, date, base_url)
    if not url:
        return ""
    return f"[{label}]({url})"
