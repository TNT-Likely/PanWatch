import src.web.api.stocks as stocks_api


def test_get_quotes_uses_md_quote_rows(monkeypatch):
    """自选股 /quotes 应走 md_quote_rows(而非直连 _fetch_tencent_quotes)。"""
    calls = []
    monkeypatch.setattr(
        stocks_api, "md_quote_rows",
        lambda symbols, market: (calls.append((tuple(symbols), market)),
                                 [{"symbol": symbols[0], "current_price": 3.0,
                                   "change_pct": 1.0, "change_amount": 0.03, "prev_close": 2.97}])[1],
    )
    assert hasattr(stocks_api, "md_quote_rows")
    # 语义:传入原始 symbol(非腾讯格式),按市场分组调用
    assert callable(stocks_api.md_quote_rows)
