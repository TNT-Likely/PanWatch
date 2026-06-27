"""雪球 Cookie 健康状态探测测试"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_resolve_xueqiu_cookie_health_not_configured():
    """未配置 Cookie 时返回未配置状态"""
    health = resolve_xueqiu_cookie_health({})
    assert health is not None
    assert health["status"] == "not_configured"
    assert health["label"] == "未配置"


def test_resolve_xueqiu_cookie_health_unknown_when_cookie_present():
    """已配置但未检测时返回待检测"""
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
        {"status": "expired", "label": "已过期", "message": "请更新", "sample_count": 0}
    )
    assert record["status"] == "expired"
    assert record["label"] == "已过期"
    assert record["message"] == "请更新"
    assert record["checked_at"]


def test_probe_xueqiu_cookie_not_configured():
    """空 Cookie 探测返回未配置"""
    result = asyncio.run(probe_xueqiu_cookie(""))
    assert result["status"] == "not_configured"


def test_probe_xueqiu_cookie_ok():
    """有效 JSON 响应判定为正常"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"list":[{"id":1,"title":"测试"}]}'
    mock_resp.json.return_value = {"list": [{"id": 1, "title": "测试"}]}
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.news_collector.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(probe_xueqiu_cookie("xq_a_token=abc", test_symbol="600519"))

    assert result["status"] == "ok"
    assert result["sample_count"] == 1


def test_probe_xueqiu_cookie_blocked_by_waf():
    """WAF 页面判定为拦截"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html>aliyun_waf challenge</html>"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.news_collector.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(probe_xueqiu_cookie("xq_a_token=abc"))

    assert result["status"] == "blocked"


def test_probe_xueqiu_cookie_netscape_blocked_message():
    """Netscape 格式被 WAF 拦截时提示改用请求头 Cookie"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '<textarea id="renderData">{"_waf_abc":"x"}</textarea>'

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.news_collector.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(probe_xueqiu_cookie(NETSCAPE_SAMPLE))

    assert result["status"] == "blocked"
    assert "Netscape" in result["message"]
    assert "Request Headers" in result["message"]


def test_probe_xueqiu_cookie_expired_on_400():
    """HTTP 400 判定为过期"""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = "bad request"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("src.collectors.news_collector.httpx.AsyncClient", return_value=mock_client):
        result = asyncio.run(probe_xueqiu_cookie("xq_a_token=abc"))

    assert result["status"] == "expired"
