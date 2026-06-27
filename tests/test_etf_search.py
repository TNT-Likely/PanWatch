"""ETF 搜索解阻测试 —— 东财 suggest 返回的基金/期权/指数分类处理。"""

from unittest.mock import MagicMock

from src.web import stock_list


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patch_search(monkeypatch, items):
    """让 _realtime_search 的 httpx 调用返回构造的 items。"""
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = None
    fake_client.get.return_value = _FakeResp(
        {"QuotationCodeTable": {"Data": items}}
    )
    monkeypatch.setattr(stock_list.httpx, "Client", lambda *a, **k: fake_client)
    # 关闭缓存补全,避免命中真实缓存文件
    monkeypatch.setattr(stock_list, "_cached_search", lambda *a, **k: [])


def test_search_returns_cn_etf_with_security_type(monkeypatch):
    """场内 ETF(5/15/16 开头)归入 CN 市场并标记 security_type=etf。"""
    _patch_search(
        monkeypatch,
        [
            {"Code": "510300", "Name": "沪深300ETF华泰柏瑞", "Classify": "Fund", "SecurityTypeName": "基金", "MktNum": "1"},
            {"Code": "159915", "Name": "创业板ETF易方达", "Classify": "Fund", "SecurityTypeName": "基金", "MktNum": "0"},
            {"Code": "160119", "Name": "500ETF联接LOF", "Classify": "Fund", "SecurityTypeName": "基金", "MktNum": "0"},
        ],
    )
    results = stock_list.search_stocks("510300")
    by_code = {r["symbol"]: r for r in results}
    assert by_code["510300"]["market"] == "CN"
    assert by_code["510300"]["security_type"] == "etf"
    assert by_code["159915"]["security_type"] == "etf"
    assert by_code["160119"]["security_type"] == "etf"


def test_search_excludes_options_and_otc(monkeypatch):
    """期权(8 位代码)与场外基金(0/1 非场内前缀)被排除。"""
    _patch_search(
        monkeypatch,
        [
            {"Code": "510300", "Name": "沪深300ETF", "Classify": "Fund", "SecurityTypeName": "基金", "MktNum": "1"},
            {"Code": "10011007", "Name": "500ETF购9月7750", "Classify": "Option", "SecurityTypeName": "期权", "MktNum": "10"},
            {"Code": "001186", "Name": "某场外基金", "Classify": "Fund", "SecurityTypeName": "基金", "MktNum": "0"},
        ],
    )
    results = stock_list.search_stocks("500ETF")
    codes = {r["symbol"] for r in results}
    assert "510300" in codes
    assert "10011007" not in codes  # 期权排除
    assert "001186" not in codes    # 场外基金排除


def test_search_stock_keeps_security_type_stock(monkeypatch):
    """普通股票仍归 stock(回归保护)。"""
    _patch_search(
        monkeypatch,
        [
            {"Code": "600519", "Name": "贵州茅台", "Classify": "AStock", "SecurityTypeName": "沪A"},
        ],
    )
    results = stock_list.search_stocks("600519")
    assert results[0]["market"] == "CN"
    assert results[0]["security_type"] == "stock"
