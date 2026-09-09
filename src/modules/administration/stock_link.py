"""股票外部链接生成工具：根据股票代码、市场和用户选择的平台生成行情页 URL。

全局设置 key: stock_link_platform (默认 xueqiu)
"""

from __future__ import annotations

import logging

from src.web.database import SessionLocal
from src.web.models import AppSettings

logger = logging.getLogger(__name__)

# 支持的平台 {code: 中文名}
PLATFORMS = {
    "xueqiu": "雪球",
}

DEFAULT_PLATFORM = "xueqiu"
SETTING_KEY = "stock_link_platform"


def get_platform() -> str:
    """从 AppSettings 读取当前配置的平台代码。"""
    db = SessionLocal()
    try:
        row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
        return (row.value if row and row.value else DEFAULT_PLATFORM)
    finally:
        db.close()


def stock_url(symbol: str, market: str, platform: str = "") -> str:
    """生成股票行情页 URL。

    Args:
        symbol: 股票代码，如 "002837", "AAPL", "00883"
        market: 市场代码，如 "CN", "US", "HK"
        platform: 平台代码，为空时从全局设置读取
    """
    if not platform:
        platform = get_platform()

    m = market.upper()

    if platform == "xueqiu":
        return _xueqiu_url(symbol, m)

    # 兜底
    return _xueqiu_url(symbol, m)


def stock_link_markdown(symbol: str, market: str, platform: str = "") -> str:
    """生成 Markdown 格式的股票链接: [002837.CN](https://xueqiu.com/S/SZ002837)"""
    code = f"{symbol}.{market}"
    url = stock_url(symbol, market, platform)
    return f"[{code}]({url})"


# ---------------------------------------------------------------------------
# 各平台 URL 生成
# ---------------------------------------------------------------------------

def _xueqiu_url(symbol: str, market: str) -> str:
    if market == "US":
        return f"https://xueqiu.com/S/{symbol}"
    if market == "HK":
        return f"https://xueqiu.com/S/{symbol}"
    # CN A股
    from src.core.cn_symbol import get_cn_prefix
    prefix = get_cn_prefix(symbol, upper=True)
    return f"https://xueqiu.com/S/{prefix}{symbol}"
