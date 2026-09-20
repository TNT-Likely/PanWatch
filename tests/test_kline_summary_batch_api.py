from src.modules.market.api import klines


def test_summary_batch_preserves_input_order_across_markets(monkeypatch):
    calls: list[tuple[str, str]] = []

    def fake_summary(self, symbol: str):
        calls.append((self.market.value, symbol))
        return {"trend": f"trend-{symbol}"}

    monkeypatch.setattr(klines.KlineCollector, "get_kline_summary", fake_summary)

    payload = klines.KlineSummaryBatchRequest(
        items=[
            klines.KlineSummaryItem(symbol="600519", market="CN"),
            klines.KlineSummaryItem(symbol="00700", market="HK"),
            klines.KlineSummaryItem(symbol="AAPL", market="US"),
        ]
    )

    result = klines.get_kline_summary_batch(payload)

    assert [item["symbol"] for item in result] == ["600519", "00700", "AAPL"]
    assert [item["market"] for item in result] == ["CN", "HK", "US"]
    assert [item["summary"]["trend"] for item in result] == [
        "trend-600519",
        "trend-00700",
        "trend-AAPL",
    ]
    assert sorted(calls) == sorted([("CN", "600519"), ("HK", "00700"), ("US", "AAPL")])


def test_summary_batch_keeps_other_items_when_one_summary_fails(monkeypatch):
    def fake_summary(self, symbol: str):
        if symbol == "BAD":
            raise RuntimeError("provider unavailable")
        return {"trend": f"trend-{symbol}"}

    monkeypatch.setattr(klines.KlineCollector, "get_kline_summary", fake_summary)

    payload = klines.KlineSummaryBatchRequest(
        items=[
            klines.KlineSummaryItem(symbol="GOOD", market="CN"),
            klines.KlineSummaryItem(symbol="BAD", market="CN"),
        ]
    )

    result = klines.get_kline_summary_batch(payload)

    assert result[0]["summary"] == {"trend": "trend-GOOD"}
    assert result[1]["summary"]["error"] == "provider unavailable"
