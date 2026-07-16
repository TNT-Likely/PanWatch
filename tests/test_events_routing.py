"""事件取数 flag 门控路由测试"""
import asyncio
from datetime import datetime, timedelta

import src.collectors.events_collector as ec


def test_fetch_events_flag_on_uses_marketdata(monkeypatch):
    """flag 开:走 marketdata 包的 events,转换为 PanWatch EventItem"""
    from marketdata.types import EventItem as MdEventItem

    monkeypatch.setattr(ec, "_use_marketdata_events", lambda: True)

    now = datetime.now()

    class _MD:
        def events(self, symbols, *, market="CN", since_days=7):
            return [
                MdEventItem(
                    source="eastmoney",
                    external_id="AN001",
                    event_type="earnings",
                    title="业绩预告",
                    publish_time=now,
                    symbols=["600519"],
                    importance=3,
                    url="https://data.eastmoney.com/notices/detail/600519/AN001.html",
                )
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events(["600519"]))

    assert len(out) == 1
    item = out[0]
    assert isinstance(item, ec.EventItem)
    assert item.source == "eastmoney"
    assert item.external_id == "AN001"
    assert item.event_type == "earnings"
    assert item.title == "业绩预告"
    assert item.symbols == ["600519"]
    assert item.importance == 3


def test_fetch_events_flag_on_no_symbols_returns_empty(monkeypatch):
    """flag 开:symbols 为空时短路返回空列表,不触达 marketdata。"""
    monkeypatch.setattr(ec, "_use_marketdata_events", lambda: True)

    def _boom():
        raise AssertionError("不应调用 get_market_data")

    monkeypatch.setattr(ec, "get_market_data", _boom)

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events([]))
    assert out == []


def test_fetch_events_flag_on_applies_since_filter(monkeypatch):
    """flag 开:md.events 按天窗返回的结果仍需用原 since 精确重过滤。"""
    from marketdata.types import EventItem as MdEventItem

    monkeypatch.setattr(ec, "_use_marketdata_events", lambda: True)

    now = datetime.now()
    old_time = now - timedelta(days=10)
    since = now - timedelta(days=3)

    class _MD:
        def events(self, symbols, *, market="CN", since_days=7):
            return [
                MdEventItem(
                    source="eastmoney", external_id="NEW", event_type="notice",
                    title="新公告", publish_time=now, symbols=["600519"],
                    importance=0, url="",
                ),
                MdEventItem(
                    source="eastmoney", external_id="OLD", event_type="notice",
                    title="旧公告", publish_time=old_time, symbols=["600519"],
                    importance=0, url="",
                ),
            ]

    monkeypatch.setattr(ec, "get_market_data", lambda: _MD())

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events(["600519"], since=since))

    assert len(out) == 1
    assert out[0].external_id == "NEW"


def test_fetch_events_flag_off_unchanged(monkeypatch):
    """flag 关:原 httpx 取数/解析路径保持不变。"""
    monkeypatch.setattr(ec, "_use_marketdata_events", lambda: False)

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "success": True,
                "data": {
                    "list": [
                        {
                            "art_code": "AN002",
                            "title": "重大事项停牌公告",
                            "notice_date": "2026-07-10 08:00:00",
                            "codes": [{"stock_code": "600519"}],
                            "columns": [{"column_name": "临时公告"}],
                        }
                    ]
                },
            }

        text = ""

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse()

    monkeypatch.setattr(ec.httpx, "AsyncClient", _FakeAsyncClient)

    collector = ec.EastMoneyEventsCollector()
    out = asyncio.run(collector.fetch_events(["600519"]))

    assert len(out) == 1
    item = out[0]
    assert item.external_id == "AN002"
    assert item.event_type == "suspension"  # "停牌" 命中启发式
    assert item.symbols == ["600519"]
