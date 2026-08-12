from datetime import date, timedelta

import marketdata.vendors.events as ev
from marketdata.symbol import Symbol
from marketdata.types import EventItem


def test_events_parses_and_filters(monkeypatch):
    # 东财 ann 响应结构与字段名以 src/collectors/events_collector.py 的 _parse_item 实际读取为准:
    # data.list[].{art_code, title, notice_date, columns[].column_name, codes[].stock_code}
    #
    # 日期相对「今天」生成:since_days 窗口是滚动的,写死日期的用例会在跨过窗口那天
    # 突然全被过滤掉而失败(曾发生)。
    recent = date.today() - timedelta(days=2)
    older = date.today() - timedelta(days=4)
    recent_code = f"AN{recent:%Y%m%d}0002"
    older_code = f"AN{older:%Y%m%d}0001"

    payload = {
        "success": True,
        "data": {
            "list": [
                # 较新、"回购" -> event_type=repurchase, importance=2
                {
                    "art_code": recent_code,
                    "title": "贵州茅台股份有限公司关于回购股份的公告",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "临时公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # 较旧、"重大资产重组" -> event_type=restructuring, importance=3
                {
                    "art_code": older_code,
                    "title": "贵州茅台股份有限公司关于重大资产重组的公告",
                    "notice_date": f"{older:%Y-%m-%d} 09:00:00",
                    "columns": [{"column_name": "重大事项"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # 与第一条重复的 art_code -> 应被去重
                {
                    "art_code": recent_code,
                    "title": "贵州茅台股份有限公司关于回购股份的公告(重复)",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "临时公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
            ]
        },
    }
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: payload)

    # 混入一个非 A 股代码(5 位港股),验证 A 股过滤只对 symbols 生效、不影响返回结构。
    symbols = [Symbol.parse("600519"), Symbol.parse("00700")]
    out = ev.EventsVendor().fetch(symbols, {"since_days": 30})

    assert all(isinstance(x, EventItem) for x in out)
    # 去重生效:3 条输入 -> 2 条唯一 (source, external_id)
    assert len(out) == 2

    # 排序:按 (publish_time, importance) 降序 -> 较新的回购公告排第一
    assert out[0].external_id == recent_code
    assert out[0].event_type == "repurchase"
    assert out[0].importance == 2
    assert out[0].symbols == ["600519"]
    assert out[0].source == "eastmoney"
    assert (
        out[0].url
        == f"https://data.eastmoney.com/notices/detail/600519/{recent_code}.html"
    )

    assert out[1].external_id == older_code
    assert out[1].event_type == "restructuring"
    assert out[1].importance == 3


def test_events_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"success": True, "data": {"list": []}})
    assert ev.EventsVendor().fetch([Symbol.parse("600519")], {}) == []
