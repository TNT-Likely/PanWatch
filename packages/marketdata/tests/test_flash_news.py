"""快讯(7×24)vendor 测试:cls/sina/eastmoney 均离线构造样例,monkeypatch 各自模块内 market_get。

沙箱代理会拦真实端点,字段映射按文档尽力构造,未文档化字段(cls 的 level/stock_list、
sina 的 ext.stocks)用防御性样例覆盖,断言不崩且尽量抽取正确。
"""
from __future__ import annotations

from datetime import datetime, timezone

import marketdata.vendors.flash_news as fn
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.types import FlashNews


# ---------------------------------------------------------------------------
# cls(财联社)
# ---------------------------------------------------------------------------

def test_cls_sign_is_deterministic():
    params = {
        "appName": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "last_time": "",
        "refresh_type": 1,
        "rn": 50,
    }
    sign1 = fn._cls_sign(params)
    sign2 = fn._cls_sign(params)
    # 同样的 params -> 同样的 sign(确定性,便于离线测试与调试)
    assert sign1 == sign2
    assert len(sign1) == 32  # md5 hex digest 长度固定
    assert all(c in "0123456789abcdef" for c in sign1)

    # 手算校验:sha1(qs).hexdigest() 再 md5
    import hashlib
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    expected = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    assert sign1 == expected


def test_cls_parses_and_maps_importance_and_symbols(monkeypatch):
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": 1001,
                    "title": "央行公开市场操作",
                    "content": "央行今日开展逆回购操作",
                    "brief": "",
                    "ctime": 1752652800,  # 2025-07-16 08:00:00 UTC 附近的固定戳(可复现)
                    "level": "A",
                    "stock_list": [{"code": "600519", "name": "贵州茅台"}, {"SecurityCode": "000001"}],
                    "shareurl": "https://www.cls.cn/detail/1001",
                },
                {
                    # 无 title/content,回退 brief;level 为未识别字符串 -> importance=0
                    "id": 1002,
                    "title": "",
                    "content": "",
                    "brief": "简讯内容",
                    "ctime": "1752652900",
                    "level": "Z",
                    "stock_list": [],
                    "shareurl": "",
                },
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.ClsFlashNewsVendor().fetch([], {"days": 20})
    assert len(out) == 2
    assert all(isinstance(x, FlashNews) for x in out)

    first = out[0]
    assert first.source == "cls"
    assert first.external_id == "1001"
    assert first.title == "央行公开市场操作"
    assert first.content == "央行今日开展逆回购操作"
    assert first.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert first.importance == 3  # level="A" -> 3
    assert first.symbols == ["600519", "000001"]
    assert first.url == "https://www.cls.cn/detail/1001"

    second = out[1]
    assert second.title == "简讯内容"  # 回退 brief
    assert second.content == "简讯内容"
    assert second.importance == 0  # 未识别 level
    assert second.symbols == []


def test_cls_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"data": {"roll_data": []}})
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []


def test_cls_tolerates_broken_item(monkeypatch):
    """单条解析异常应被跳过而非整体失败。"""
    payload = {"data": {"roll_data": [{"id": 1, "title": None, "content": None, "brief": None}]}}
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []


# ---------------------------------------------------------------------------
# sina(新浪直播)
# ---------------------------------------------------------------------------

def test_sina_parses_ext_stocks_and_time(monkeypatch):
    payload = {
        "result": {
            "data": {
                "feed": {
                    "list": [
                        {
                            "id": "2001",
                            "rich_text": "沪指高开0.5%,两市成交额破万亿",
                            "create_time": "2026-07-16 09:31:00",
                            "ext": '{"stocks": [{"symbol": "sh600519", "name": "贵州茅台"}, {"code": "000001"}]}',
                        },
                        {
                            # 无 rich_text,回退 content;create_time 为秒戳字符串
                            "id": "2002",
                            "content": "深成指低开",
                            "create_time": "1752652800",
                            "ext": "",
                        },
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.SinaFlashNewsVendor().fetch([], {"days": 20})
    assert len(out) == 2
    first = out[0]
    assert first.source == "sina"
    assert first.external_id == "2001"
    assert first.content == "沪指高开0.5%,两市成交额破万亿"
    assert first.publish_time == datetime(2026, 7, 16, 9, 31, 0, tzinfo=timezone.utc)
    assert first.symbols == ["sh600519", "000001"]

    second = out[1]
    assert second.content == "深成指低开"  # 回退 content
    assert second.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert second.symbols == []


def test_sina_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.SinaFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"result": {"data": {"feed": {"list": []}}}})
    assert fn.SinaFlashNewsVendor().fetch([], {}) == []


def test_sina_tolerates_broken_ext_json(monkeypatch):
    """ext 不是合法 JSON 时不应崩,symbols 回退空列表。"""
    payload = {
        "result": {
            "data": {
                "feed": {
                    "list": [
                        {"id": "3", "rich_text": "测试内容", "create_time": "2026-07-16 10:00:00", "ext": "{not json"}
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)
    out = fn.SinaFlashNewsVendor().fetch([], {})
    assert len(out) == 1
    assert out[0].symbols == []


# ---------------------------------------------------------------------------
# eastmoney(东财快讯)
# ---------------------------------------------------------------------------

def test_eastmoney_parses_title_summary_time(monkeypatch):
    payload = {
        "data": {
            "fastNewsList": [
                {
                    "id": "4001",
                    "title": "机构:三季度A股有望震荡上行",
                    "summary": "多家机构发布三季度策略展望",
                    "showTime": "2026-07-16 11:20:00",
                },
                {
                    # showTime 格式不可解析 -> 回退 EPOCH,不崩
                    "id": "4002",
                    "title": "快讯标题",
                    "summary": "摘要内容",
                    "showTime": "not-a-date",
                },
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.EastmoneyFlashNewsVendor().fetch([], {"days": 30})
    assert len(out) == 2
    first = out[0]
    assert first.source == "eastmoney"
    assert first.title == "机构:三季度A股有望震荡上行"
    assert first.content == "多家机构发布三季度策略展望"
    assert first.publish_time == datetime(2026, 7, 16, 11, 20, 0, tzinfo=timezone.utc)
    assert first.symbols == []
    assert first.importance == 0

    second = out[1]
    assert second.publish_time == fn._EPOCH  # 不可解析时间的防御回退


def test_eastmoney_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.EastmoneyFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"data": {"fastNewsList": []}})
    assert fn.EastmoneyFlashNewsVendor().fetch([], {}) == []


# ---------------------------------------------------------------------------
# MarketData.flash_news() —— 走单源 Engine 出数
# ---------------------------------------------------------------------------

def test_marketdata_flash_news_single_source(monkeypatch):
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": "9001",
                    "title": "测试快讯标题",
                    "content": "测试快讯正文包含关键字ABC",
                    "brief": "",
                    "ctime": 1752652800,
                    "level": "B",
                    "stock_list": [],
                    "shareurl": "",
                }
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    md = MarketData(
        config=StaticConfigProvider(
            {"flash_news": [SourceConfig(vendor="cls", config={}, enabled=True)]}
        )
    )
    out = md.flash_news(limit=20)
    assert len(out) == 1
    assert out[0].title == "测试快讯标题"
    assert out[0].importance == 2  # level="B" -> 2

    # keyword 过滤:命中 title/content 才保留
    out_kw = md.flash_news(limit=20, keyword="ABC")
    assert len(out_kw) == 1
    out_kw_miss = md.flash_news(limit=20, keyword="不存在的关键字")
    assert out_kw_miss == []
