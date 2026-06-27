"""雪球采集健康状态探测测试"""

import asyncio
from unittest.mock import AsyncMock, patch

from src.collectors.news_collector import (
    build_xueqiu_cookie_health_record,
    is_netscape_cookie_format,
    normalize_xueqiu_cookies,
    probe_xueqiu_cookie,
    resolve_xueqiu_cookie_health,
)


NETSCAPE_SAMPLE = """# Netscape HTTP Cookie File
.xueqiu.com\tTRUE\t/\tFALSE\t1783301134\txq_a_token\tabc123
.xueqiu.com\tTRUE\t/\tFALSE\t1784597134\txq_r_token\tdef456
"""


def test_is_netscape_cookie_format():
    """识别 Netscape cookies.txt 格式"""
    assert is_netscape_cookie_format(NETSCAPE_SAMPLE) is True
    assert is_netscape_cookie_format("xq_a_token=abc; xq_r_token=def") is False


def test_normalize_xueqiu_cookies_from_netscape():
    """Netscape 格式可转为 HTTP Cookie 头"""
    normalized = normalize_xueqiu_cookies(NETSCAPE_SAMPLE)
    assert normalized == "xq_a_token=abc123; xq_r_token=def456"


def test_normalize_xueqiu_cookies_passthrough():
    """已是请求头格式时原样返回"""
    raw = "xq_a_token=abc; xq_r_token=def"
    assert normalize_xueqiu_cookies(raw) == raw


def test_normalize_xueqiu_cookies_space_separated_netscape():
    """空格分隔的 Netscape 行也可转换"""
    raw = ".xueqiu.com    TRUE    /    FALSE    1783301134    xq_a_token    abc123"
    assert normalize_xueqiu_cookies(raw) == "xq_a_token=abc123"


def test_normalize_xueqiu_cookies_parse_failure_keeps_raw():
    """Netscape 识别成功但解析失败时保留原文，避免清空 Cookie"""
    raw = "# Netscape HTTP Cookie File\ninvalid-line-without-fields"
    assert normalize_xueqiu_cookies(raw) == raw


def test_resolve_xueqiu_cookie_health_unknown_by_default():
    """未检测时返回待检测，且提示 Playwright 无需 Cookie"""
    health = resolve_xueqiu_cookie_health({})
    assert health is not None
    assert health["status"] == "unknown"
    assert health["label"] == "待检测"
    assert "Playwright" in health["message"]


def test_resolve_xueqiu_cookie_health_unknown_when_cookie_present():
    """已配置 Cookie 但未检测时仍返回待检测"""
    health = resolve_xueqiu_cookie_health({"cookies": "xq_a_token=abc"})
    assert health is not None
    assert health["status"] == "unknown"
    assert health["label"] == "待检测"


def test_resolve_xueqiu_cookie_health_uses_cached_record():
    """优先使用缓存的检测结果"""
    health = resolve_xueqiu_cookie_health(
        {
            "cookies": "xq_a_token=abc",
            "cookie_health": {
                "status": "ok",
                "label": "正常",
                "message": "有效",
                "checked_at": "2026-06-26 10:00:00",
                "sample_count": 2,
            },
        }
    )
    assert health is not None
    assert health["status"] == "ok"
    assert health["checked_at"] == "2026-06-26 10:00:00"
    assert health["sample_count"] == 2


def test_build_xueqiu_cookie_health_record():
    """探测结果可写入 config 缓存结构"""
    record = build_xueqiu_cookie_health_record(
        {"status": "error", "label": "检测失败", "message": "请更新", "sample_count": 0}
    )
    assert record["status"] == "error"
    assert record["label"] == "检测失败"
    assert record["message"] == "请更新"
    assert record["checked_at"]


def _mock_browser_client(items=None, error=None):
    mock_client = AsyncMock()
    mock_client.fetch_timeline = AsyncMock(return_value=(items or [], error))
    return patch(
        "src.collectors.news_collector._XueqiuBrowserClient.get",
        AsyncMock(return_value=mock_client),
    )


def test_probe_xueqiu_cookie_ok_without_cookie():
    """无 Cookie 时 Playwright 探测成功"""
    items = [{"id": 1, "title": "测试", "created_at": 1_700_000_000_000}]
    with _mock_browser_client(items=items):
        result = asyncio.run(probe_xueqiu_cookie("", test_symbol="600519"))

    assert result["status"] == "ok"
    assert result["sample_count"] == 1


def test_probe_xueqiu_cookie_blocked_by_waf():
    """WAF 拦截判定为 blocked"""
    with _mock_browser_client(error="被雪球 WAF 拦截"):
        result = asyncio.run(probe_xueqiu_cookie("xq_a_token=abc"))

    assert result["status"] == "blocked"


def test_probe_xueqiu_cookie_playwright_error():
    """Playwright 未安装时返回检测失败"""
    with _mock_browser_client(error="未安装 Playwright，请运行: pip install playwright && playwright install chromium"):
        result = asyncio.run(probe_xueqiu_cookie(""))

    assert result["status"] == "error"
    assert "Playwright" in result["message"]


def test_probe_xueqiu_cookie_ok_with_empty_list():
    """接口正常但无新闻时仍判定为 ok"""
    with _mock_browser_client(items=[]):
        result = asyncio.run(probe_xueqiu_cookie(""))

    assert result["status"] == "ok"
    assert result["sample_count"] == 0
    assert "暂无新闻" in result["message"]
