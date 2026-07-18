"""新闻资讯 vendor 测试:xueqiu/eastmoney_news/eastmoney 均离线构造样例,
monkeypatch news 模块内 market_get(三个 vendor 共享同一个模块级引用)。

沙箱代理会拦真实端点,字段映射按 src/collectors/news_collector.py 尽力构造;
雪球已知被阿里云 WAF 拦截,用构造的 WAF HTML 挑战页样例验证防御分支。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import marketdata.vendors.news as news_mod
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.http import capture_errors
from marketdata.ports import SourceConfig
from marketdata.symbol import Symbol
from marketdata.types import NewsArticle


def _jsonp(payload: dict) -> str:
    return "jQuery(" + json.dumps(payload) + ")"


# ---------------------------------------------------------------------------
# xueqiu(雪球个股新闻)
# ---------------------------------------------------------------------------

def test_xueqiu_parses_item_and_importance(monkeypatch):
    payload = {
        "list": [
            {
                "id": 5001,
                "title": "<b>重磅</b>:公司获得新订单",
                "description": "详细描述内容",
                "created_at": 1752652800000,  # ms -> 2026-07-16 08:00:00 UTC
                "target": "https://xueqiu.com/1234/5001",
            }
        ]
    }
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: json.dumps(payload))

    out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {"cookies": "abc=1"})
    assert len(out) == 1
    a = out[0]
    assert isinstance(a, NewsArticle)
    assert a.source == "xueqiu"
    assert a.external_id == "5001"
    assert a.title == "重磅:公司获得新订单"  # HTML 标签被清理
    assert a.content == "详细描述内容"
    assert a.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert a.symbols == ["600519"]
    assert a.importance == 2  # 命中"重磅"
    assert a.url == "https://xueqiu.com/1234/5001"


def test_xueqiu_symbol_id_prefix_rules(monkeypatch):
    """SH/SZ 加前缀,BJ 保留原值(雪球不识别 BJ 代码)。"""
    captured = {}

    def fake(url, *, params, **kwargs):
        captured["symbol_id"] = params["symbol_id"]
        return json.dumps({"list": []})

    monkeypatch.setattr(news_mod, "market_get", fake)

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})
    assert captured["symbol_id"] == "SH600519"

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("000001")], {})
    assert captured["symbol_id"] == "SZ000001"

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("920001")], {})
    assert captured["symbol_id"] == "920001"  # BJ 原值透传


def test_xueqiu_no_a_share_symbols_returns_empty():
    # 港股/美股代码不是 6 位数字 -> 直接返回 [],不发请求
    assert news_mod.XueqiuNewsVendor().fetch([Symbol.parse("00700"), Symbol.parse("AAPL")], {}) == []


def test_xueqiu_waf_blocked_records_error(monkeypatch):
    """WAF 挑战页(HTML)命中特征字符串 -> 返回 [] 且 record_error 带 "WAF" 字样。"""
    html = "<html><body><textarea>window._waf_challenge = 1;</textarea></body></html>"
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: html)

    with capture_errors() as errs:
        out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})

    assert out == []
    assert any("WAF" in e for e in errs)


def test_xueqiu_non_json_non_waf_response_returns_empty(monkeypatch):
    """既不含 WAF 特征、也不是合法 JSON 的响应 -> 视为非预期结构,同样 record_error 且返回 []。"""
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: "not json at all")

    with capture_errors() as errs:
        out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})

    assert out == []
    assert any("WAF" in e for e in errs)


def test_xueqiu_market_get_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {}) == []


# ---------------------------------------------------------------------------
# eastmoney_news(东财个股新闻搜索)
# ---------------------------------------------------------------------------

def _em_news_payload(code="202607170001", title="赛力斯发布新车型<em>亮点</em>"):
    item = {
        "code": code,
        "title": title,
        "content": "详细内容",
        "url": f"https://finance.eastmoney.com/a/{code}.html",
        "date": "2026-07-17 09:30:00",
    }
    return {"code": 0, "result": {"cmsArticleWebOld": [item]}}


def test_eastmoney_news_parses_and_uses_names(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload())

    monkeypatch.setattr(news_mod, "market_get", fake)

    vendor = news_mod.EastmoneyStockNewsVendor()
    out = vendor.fetch([Symbol.parse("601127")], {"symbol_names": {"601127": "赛力斯"}})

    assert len(out) == 1
    a = out[0]
    assert a.source == "eastmoney_news"
    assert a.external_id == "202607170001"
    assert a.title == "赛力斯发布新车型亮点"  # 高亮标签被清理
    assert a.content == "详细内容"
    assert a.publish_time == datetime(2026, 7, 17, 9, 30, 0, tzinfo=timezone.utc)
    assert a.symbols == ["601127"]
    assert a.url == "https://finance.eastmoney.com/a/202607170001.html"

    # names 生效:搜索关键词应为股票名称而非代码
    assert len(calls) == 1
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "赛力斯"


def test_eastmoney_news_falls_back_to_code_without_names(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload())

    monkeypatch.setattr(news_mod, "market_get", fake)

    news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("601127")], {})
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "601127"  # 缺名 fallback 用代码搜索


def test_eastmoney_news_dedup_across_symbols(monkeypatch):
    """同一条新闻可能出现在多只股票的搜索结果里,fetch() 内部按 external_id 去重。"""
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _jsonp(_em_news_payload(code="DUP1")))

    out = news_mod.EastmoneyStockNewsVendor().fetch(
        [Symbol.parse("601127"), Symbol.parse("600519")],
        {"symbol_names": {"601127": "赛力斯", "600519": "贵州茅台"}},
    )
    assert len(out) == 1  # 两次搜索都命中同一条 DUP1,去重后只剩 1 条


def test_eastmoney_news_code_not_zero_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _jsonp({"code": 1, "result": {}}))
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_non_jsonp_response_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: "<html>not jsonp</html>")
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_market_get_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_fetch_by_keyword(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload(code="KW1", title="新能源汽车行业周报"))

    monkeypatch.setattr(news_mod, "market_get", fake)

    out = news_mod.EastmoneyStockNewsVendor.fetch_by_keyword("新能源汽车")
    assert len(out) == 1
    assert out[0].title == "新能源汽车行业周报"
    assert out[0].symbols == ["新能源汽车"]  # keyword 本身作为 symbol 标记(照搬原逻辑)

    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "新能源汽车"


# ---------------------------------------------------------------------------
# eastmoney(东财公告)
# ---------------------------------------------------------------------------

def _ann_payload():
    return {
        "success": True,
        "data": {
            "list": [
                {
                    "art_code": "AN202607170001",
                    "title": "贵州茅台关于分红派息的公告",
                    "notice_date": "2026-07-17 08:00:00",
                    "columns": [{"column_name": "定期公告"}],
                    "codes": [{"stock_code": "600519"}],
                }
            ]
        },
    }


def test_eastmoney_ann_parses(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _ann_payload())

    out = news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {})
    assert len(out) == 1
    a = out[0]
    assert a.source == "eastmoney"
    assert a.external_id == "AN202607170001"
    assert a.title == "贵州茅台关于分红派息的公告"
    assert a.content == ""  # 公告只有标题
    assert a.publish_time == datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
    assert a.symbols == ["600519"]
    assert a.importance == 2  # 命中"分红"
    assert a.url == "https://data.eastmoney.com/notices/detail/600519/AN202607170001.html"


def test_eastmoney_ann_non_a_share_returns_empty():
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("00700")], {}) == []


def test_eastmoney_ann_failure_response_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: {"success": False})
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {}) == []

    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {}) == []


# ---------------------------------------------------------------------------
# MarketData.news() —— 聚合(非失败转移):合并多源 + 去重 + 排序 + since 过滤
# ---------------------------------------------------------------------------

def _fake_agg_market_get(url, *, host_key, params=None, **kwargs):
    if host_key == news_mod._EM_NEWS_HOST:
        return _jsonp(
            {
                "code": 0,
                "result": {
                    "cmsArticleWebOld": [
                        {
                            "code": "A1",
                            "title": "个股新闻标题",
                            "content": "个股新闻内容",
                            "url": "https://finance.eastmoney.com/a/A1.html",
                            "date": "2026-07-17 10:00:00",
                        }
                    ]
                },
            }
        )
    if host_key == news_mod._EM_ANN_HOST:
        return {
            "success": True,
            "data": {
                "list": [
                    {
                        "art_code": "AN1",
                        "title": "重大事项公告",
                        "notice_date": "2026-07-17 12:00:00",
                        "columns": [],
                        "codes": [{"stock_code": "600519"}],
                    },
                    {
                        # 36.5 小时前:超出 2h 常规窗口,但在 72h 公告窗口内 —— 验证宽窗口生效
                        "art_code": "AN2",
                        "title": "季报点评",
                        "notice_date": "2026-07-16 00:00:00",
                        "columns": [],
                        "codes": [{"stock_code": "600519"}],
                    },
                ]
            },
        }
    return None


def _agg_md() -> MarketData:
    return MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="eastmoney_news", priority=1),
                    SourceConfig(vendor="eastmoney", priority=2),
                ]
            }
        )
    )


def test_news_merges_multiple_sources_and_sorts_desc(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    md = _agg_md()
    out = md.news(["600519"], since_hours=2, names={"600519": "贵州茅台"})

    # 按时间倒序:AN1(07-17 12:00) > A1(07-17 10:00) > AN2(07-16 00:00)
    assert [a.external_id for a in out] == ["AN1", "A1", "AN2"]


def test_news_no_now_skips_since_filter(monkeypatch):
    """不传 now 则不做时间过滤,即便 since_hours 很小,全部合并结果原样返回。"""
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    out = _agg_md().news(["600519"], since_hours=1)
    assert len(out) == 3


def test_news_since_filter_with_now_uses_wider_announcement_window(monkeypatch):
    """传入 now 后:常规源用 since_hours(2h)过滤,公告源(eastmoney)用 max(since_hours,72)h。
    AN2 距 now 约 36.5 小时 —— 若按 2h 窗口会被过滤掉,但作为公告应享受 72h 窗口而保留;
    A1(eastmoney_news,~2.5 小时前)按 2h 窗口应被过滤掉。
    """
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    now = datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc)
    out = _agg_md().news(["600519"], since_hours=2, now=now)

    assert [a.external_id for a in out] == ["AN1", "AN2"]


def test_news_dedup_keeps_first_seen_across_vendors(monkeypatch):
    """跨 vendor 出现相同 external_id 时,按 external_id 去重,保留先处理(优先级更高)的源。"""

    def fake(url, *, host_key, params=None, **kwargs):
        if host_key == news_mod._EM_NEWS_HOST:
            return _jsonp(
                {
                    "code": 0,
                    "result": {
                        "cmsArticleWebOld": [
                            {
                                "code": "DUP1",
                                "title": "来自东财的标题",
                                "content": "东财内容",
                                "url": "https://finance.eastmoney.com/a/DUP1.html",
                                "date": "2026-07-17 10:00:00",
                            }
                        ]
                    },
                }
            )
        if host_key == news_mod._XUEQIU_HOST:
            return json.dumps(
                {
                    "list": [
                        {
                            "id": "DUP1",
                            "title": "来自雪球的标题",
                            "description": "雪球内容",
                            "created_at": 1752739200000,  # 2026-07-17 12:00:00 UTC(更新,但优先级更低)
                            "target": "https://xueqiu.com/x/DUP1",
                        }
                    ]
                }
            )
        return None

    monkeypatch.setattr(news_mod, "market_get", fake)

    md = MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="eastmoney_news", priority=1),
                    SourceConfig(vendor="xueqiu", priority=2),
                ]
            }
        )
    )
    out = md.news(["600519"])
    assert len(out) == 1
    assert out[0].source == "eastmoney_news"  # 先处理的优先级更高的源被保留
    assert out[0].title == "来自东财的标题"


def test_news_unknown_or_disabled_source_skipped(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)

    md = MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="not_a_real_vendor", priority=1),
                    SourceConfig(vendor="eastmoney", priority=2, enabled=False),
                ]
            }
        )
    )
    assert md.news(["600519"]) == []


def test_news_records_metrics(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    md = _agg_md()
    md.news(["600519"])
    snap = md.health()
    assert snap["eastmoney_news"]["success_rate"] == 1.0
    assert snap["eastmoney"]["success_rate"] == 1.0


# ---------------------------------------------------------------------------
# MarketData.news_by_keyword()
# ---------------------------------------------------------------------------

def test_marketdata_news_by_keyword(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload(code="KW2", title="光伏板块行业动态"))

    monkeypatch.setattr(news_mod, "market_get", fake)

    md = MarketData(config=StaticConfigProvider({}))
    out = md.news_by_keyword("光伏")

    assert len(out) == 1
    assert out[0].title == "光伏板块行业动态"
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "光伏"
