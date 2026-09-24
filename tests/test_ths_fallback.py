"""同花顺降级通道单测(离线:monkeypatch _ths_get,不发网络请求)。"""

from __future__ import annotations

import types

import pandas as pd

import src.modules.market.sector_data_service as ssvc

HYZJL_HTML = """
<html><table>
<tr><th>序号</th><th>行业</th><th>行业指数</th><th>涨跌幅</th><th>流入资金(亿)</th>
<th>流出资金(亿)</th><th>净额(亿)</th><th>公司家数</th><th>领涨股</th></tr>
<tr><td>1</td><td>元件</td><td>29926.60</td><td>1.50%</td><td>434.75</td>
<td>423.37</td><td>11.38</td><td>63</td><td>澳弘电子</td></tr>
<tr><td>2</td><td>厨卫电器</td><td>6106.92</td><td>1.53%</td><td>4.23</td>
<td>3.64</td><td>0.59</td><td>9</td><td>亿田智能</td></tr>
<tr><td>3</td><td>无码行业</td><td>1.00</td><td>0.50%</td><td>1.00</td>
<td>0.90</td><td>0.10</td><td>5</td><td>X</td></tr>
</table></html>
"""

DETAIL_HTML = """
<html><table>
<tr><th>序号</th><th>代码</th><th>名称</th><th>现价</th><th>涨跌幅(%)</th><th>成交额</th></tr>
<tr><td>1</td><td>688512</td><td>慧智微</td><td>17.32</td><td>20.03</td><td>9.51亿</td></tr>
<tr><td>2</td><td>301678</td><td>新恒汇</td><td>59.22</td><td>14.32</td><td>9.41亿</td></tr>
</table></html>
"""


def _patch_ths_get(monkeypatch, html: str):
    monkeypatch.setattr(ssvc, "_ths_get", lambda url, referer: html)


def test_ths_sector_flow_parses_and_converts(monkeypatch):
    _patch_ths_get(monkeypatch, HYZJL_HTML)
    out = ssvc.fetch_ths_sector_flow()
    assert out["元件"] == {"main": 11.38e8}
    assert out["厨卫电器"]["main"] == 0.59e8


def test_ths_board_list_drops_unmapped_names(monkeypatch):
    _patch_ths_get(monkeypatch, HYZJL_HTML)
    monkeypatch.setattr(ssvc, "_ths_name_codes", lambda: {"元件": "881121"})
    rows = ssvc._ths_board_list(10)
    assert [r["name"] for r in rows] == ["元件"]
    assert rows[0]["code"] == "881121" and rows[0]["_source"] == "ths.hyzjl"
    assert rows[0]["change_pct"] == 1.5


def test_ths_constituents_parses_and_rejects_bad_code(monkeypatch):
    _patch_ths_get(monkeypatch, DETAIL_HTML)
    rows = ssvc._ths_board_constituents("881121", 10)
    assert [r["symbol"] for r in rows] == ["688512", "301678"]
    assert rows[0]["turnover"] == 9.51
    # 非 6 位数字代码直接拒绝(防 URL 拼接),不发起请求
    assert ssvc._ths_board_constituents("BK0475", 10) == []
    assert ssvc._ths_board_constituents("", 10) == []


def test_board_list_falls_back_to_ths_when_discovery_down(monkeypatch):
    import src.platform.marketdata.marketdata_client as mdc

    def _boom(*a, **k):
        raise RuntimeError("em_blocked")

    monkeypatch.setattr(mdc, "get_market_data", _boom)
    monkeypatch.setattr(ssvc, "_ths_board_list", lambda limit: [
        {"code": "881121", "name": "元件", "change_pct": 1.5, "turnover": None,
         "_source": "ths.hyzjl"},
    ])
    rows = ssvc.fetch_board_list(5)
    assert rows and rows[0]["_source"] == "ths.hyzjl"


def test_board_closes_falls_back_to_ths_index(monkeypatch):
    df = pd.DataFrame({"收盘价": [10.0, 10.5, 11.0]})

    class _FakeAK(types.SimpleNamespace):
        pass

    def _hist_em(**k):
        raise RuntimeError("em_blocked")

    def _index_ths(**k):
        return df

    fake = types.SimpleNamespace(
        stock_board_industry_hist_em=_hist_em,
        stock_board_industry_index_ths=_index_ths,
    )
    monkeypatch.setattr(ssvc, "_ak", lambda: fake)
    closes = ssvc.fetch_board_hist_closes("元件", days=10)
    assert closes == [10.0, 10.5, 11.0]
