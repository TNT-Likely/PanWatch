"""ETF 采集器测试 —— akshare 字段映射、缓存、异常兜底(全 mock,不联网)。"""

from unittest.mock import MagicMock

import pandas as pd

from src.collectors import etf_collector


def _mock_ak(monkeypatch, **mapping):
    """按函数名映射 akshare 调用,返回构造的 DataFrame。"""
    fake_ak = MagicMock()
    for name, ret in mapping.items():
        getattr(fake_ak, name).return_value = ret
    monkeypatch.setattr(etf_collector, "ak", fake_ak)
    return fake_ak


def test_get_spot_maps_iopv_and_premium(monkeypatch):
    """fund_etf_spot_em 字段映射为 IOPV/折价率/规模/成交额。"""
    etf_collector._ETF_SPOT_CACHE.clear()
    spot = pd.DataFrame([
        {
            "代码": "510300", "名称": "沪深300ETF华泰柏瑞", "最新价": 5.044,
            "IOPV实时估值": 5.0507, "基金折价率": 0.13, "涨跌幅": 1.55,
            "成交额": 8649918773.0, "总市值": 124469212335.0, "换手率": 6.99,
            "更新时间": pd.Timestamp("2026-06-25 12:22:20"),
        },
        {
            "代码": "159915", "名称": "创业板ETF易方达", "最新价": 2.0,
            "IOPV实时估值": 2.01, "基金折价率": 0.5, "涨跌幅": -0.3,
            "成交额": 1.0, "总市值": 2.0, "换手率": 1.0,
            "更新时间": pd.Timestamp("2026-06-25 12:22:20"),
        },
    ])
    _mock_ak(monkeypatch, fund_etf_spot_em=spot)

    r = etf_collector.get_etf_spot("510300")
    assert r is not None
    assert r["symbol"] == "510300"
    assert r["price"] == 5.044
    assert r["iopv"] == 5.0507
    assert r["premium_pct"] == 0.13  # 折价率(正=溢价)
    assert r["total_value"] == 124469212335.0
    assert r["turnover"] == 8649918773.0


def test_get_spot_cache_avoids_refetch(monkeypatch):
    """15min TTL 内不重复拉全量 spot。"""
    etf_collector._ETF_SPOT_CACHE.clear()
    fake = _mock_ak(
        monkeypatch,
        fund_etf_spot_em=pd.DataFrame([
            {"代码": "510300", "名称": "x", "最新价": 5.0, "IOPV实时估值": 5.0,
             "基金折价率": 0.0, "涨跌幅": 0.0, "成交额": 1.0, "总市值": 1.0,
             "换手率": 0.0, "更新时间": pd.Timestamp("2026-06-25 12:00:00")},
        ]),
    )
    etf_collector.get_etf_spot("510300")
    etf_collector.get_etf_spot("510300")
    assert fake.fund_etf_spot_em.call_count == 1  # 第二次命中缓存


def test_get_spot_unknown_symbol_returns_none(monkeypatch):
    """未上市的代码返回 None 而非抛错。"""
    etf_collector._ETF_SPOT_CACHE.clear()
    _mock_ak(
        monkeypatch,
        fund_etf_spot_em=pd.DataFrame([
            {"代码": "510300", "名称": "x", "最新价": 5.0, "IOPV实时估值": 5.0,
             "基金折价率": 0.0, "涨跌幅": 0.0, "成交额": 1.0, "总市值": 1.0,
             "换手率": 0.0, "更新时间": pd.Timestamp("2026-06-25 12:00:00")},
        ]),
    )
    assert etf_collector.get_etf_spot("999999") is None


def test_get_spot_network_error_returns_none(monkeypatch):
    """akshare 抛错时返回 None(不拖垮调用方)。"""
    etf_collector._ETF_SPOT_CACHE.clear()
    fake = _mock_ak(monkeypatch)
    fake.fund_etf_spot_em.side_effect = RuntimeError("network")
    assert etf_collector.get_etf_spot("510300") is None


def test_get_holdings_maps_top_holdings(monkeypatch):
    """成分股映射为代码/名称/占比,按占比降序。"""
    etf_collector._HOLDINGS_CACHE.clear()
    df = pd.DataFrame([
        {"股票代码": "600519", "股票名称": "贵州茅台", "占净值比例": 4.74, "季度": "2025Q1"},
        {"股票代码": "300750", "股票名称": "宁德时代", "占净值比例": 3.23, "季度": "2025Q1"},
    ])
    _mock_ak(monkeypatch, fund_portfolio_hold_em=df)

    holdings = etf_collector.get_etf_holdings("510300")
    assert len(holdings) == 2
    assert holdings[0]["symbol"] == "600519"
    assert holdings[0]["name"] == "贵州茅台"
    assert holdings[0]["weight_pct"] == 4.74
    # 按占比降序
    assert holdings[0]["weight_pct"] >= holdings[1]["weight_pct"]


def test_get_holdings_empty_returns_list(monkeypatch):
    """无成分股数据返回空列表而非抛错。"""
    etf_collector._HOLDINGS_CACHE.clear()
    _mock_ak(monkeypatch, fund_portfolio_hold_em=pd.DataFrame())
    assert etf_collector.get_etf_holdings("510300") == []


def test_get_nav_history_maps_and_sorts(monkeypatch):
    """净值历史按日期升序返回。"""
    etf_collector._NAV_CACHE.clear()
    df = pd.DataFrame([
        {"净值日期": pd.Timestamp("2025-01-03"), "单位净值": 3.8599, "累计净值": 1.68, "日增长率": -1.18},
        {"净值日期": pd.Timestamp("2025-01-02"), "单位净值": 3.9061, "累计净值": 1.6971, "日增长率": -2.91},
    ])
    _mock_ak(monkeypatch, fund_etf_fund_info_em=df)

    nav = etf_collector.get_etf_nav_history("510300", days=30)
    assert len(nav) == 2
    assert nav[0]["date"] < nav[1]["date"]  # 升序
    assert nav[0]["unit_nav"] == 3.9061
    assert nav[0]["cum_nav"] == 1.6971
