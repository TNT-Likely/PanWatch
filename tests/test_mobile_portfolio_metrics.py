from src.modules.mobile.portfolio_metrics import build_portfolio_metrics


def test_mobile_metrics_keep_web_aggregation_and_label_missing_amounts():
    cn_account = {
        "id": 1, "name": "Main", "total_assets": 320, "total_pnl": 20, "total_daily_pnl": 0,
        "total_market_value": 100, "available_funds": 220,
        "positions": [{"market": "CN", "market_value": 100, "daily_pnl": 0}],
    }
    mixed_account = {
        "id": 2, "name": "Mixed", "total_assets": 508, "total_pnl": -98, "total_daily_pnl": 4,
        "total_market_value": 140, "available_funds": 368,
        "positions": [
            {"market": "US", "market_value": 20, "daily_pnl": 4},
            {"market": "HK", "market_value": None, "daily_pnl": None},
        ],
    }
    result = build_portfolio_metrics({
        "total": {"total_assets": 828, "total_pnl": -78, "total_daily_pnl": 4,
                  "total_market_value": 240, "available_funds": 588},
        "accounts": [cn_account, mixed_account],
        "legacy_report": "Synthetic unexpected input",
    }).model_dump()
    assert result["currency"] == "CNY"
    assert result["basis"] == "current_holdings"
    assert result["available_assets"] == 828
    assert result["valuation"] == "partial"
    assert result["computed_at"].utcoffset().total_seconds() == 0
    assert result["market_value_cny"] is None
    assert result["available_funds_cny"] == 588
    assert result["today_pnl"] == {"value": None, "coverage": "partial"}
    assert result["unrealized_pnl"] == {"value": None, "coverage": "partial"}
    assert result["accounts"][0]["today_pnl"] == {"value": 0, "coverage": "complete"}
    assert result["accounts"][0]["unrealized_pnl"] == {"value": 20, "coverage": "complete"}
    assert result["accounts"][0]["market_value_cny"] == 100
    assert result["accounts"][0]["available_funds_cny"] == 220
    assert result["accounts"][1]["unrealized_pnl"] == {"value": None, "coverage": "partial"}
    assert result["accounts"][1]["market_value_cny"] is None
    assert result["accounts"][1]["available_funds_cny"] == 368
    assert "legacy_report" not in result


def test_mobile_metrics_distinguish_empty_portfolio_and_unpriced_positions():
    result = build_portfolio_metrics({
        "total": {"total_assets": 0, "total_pnl": 0},
        "accounts": [],
    })
    assert result.valuation == "complete" and result.today_pnl.value == 0
    pending = {
        "id": 1, "name": "Unpriced", "total_assets": 200, "total_pnl": -50, "total_daily_pnl": 0,
        "positions": [{"market_value": None, "daily_pnl": None}],
    }
    missing = build_portfolio_metrics({"total": pending, "accounts": [pending]})
    assert missing.valuation == "unavailable"
    assert missing.today_pnl.value is None and missing.today_pnl.coverage == "unavailable"
    assert missing.unrealized_pnl.value is None and missing.accounts[0].available_assets == 200


def test_current_holdings_lookback_uses_fixed_rates_and_preserves_missing_dates(monkeypatch):
    from types import SimpleNamespace
    from marketdata.types import Bar
    from src.modules.mobile import portfolio_history

    summary = {
        "total": {"available_funds": 50},
        "accounts": [{"positions": [
            {"symbol": "SAME", "market": "CN", "quantity": 2, "exchange_rate": None},
            {"symbol": "SAME", "market": "US", "quantity": 1, "exchange_rate": 7},
        ]}],
    }
    expected = {
        "CN": [Bar("2026-09-01", 100, 100, 100, 100), Bar("2026-09-02", 101, 101, 101, 101)],
        "US": [Bar("2026-09-01", 10, 10, 10, 10)],
    }

    def fetch(symbol, *, market, days):
        assert symbol == "SAME" and days == 35
        return expected[market], market

    monkeypatch.setattr(portfolio_history, "get_market_data", lambda: SimpleNamespace(klines_with_vendor=fetch))
    data = portfolio_history.build_portfolio_lookback(summary).model_dump(mode="json")
    assert data["basis"] == "current_holdings_fixed_fx" and data["currency"] == "CNY"
    assert data["points"] == [
        {"date": "2026-09-01", "assets": 320, "covered_positions": 2},
        {"date": "2026-09-02", "assets": None, "covered_positions": 1},
    ]
    assert data["sources"] == ["CN", "US"]
    summary["accounts"][0]["positions"][1]["exchange_rate"] = None
    no_fx = portfolio_history.build_portfolio_lookback(summary)
    assert no_fx.position_count == 2 and all(point.assets is None for point in no_fx.points)


def test_cost_basis_uses_recorded_ledger_total_independently_of_quote_coverage():
    row = {"id": 1, "name": "Example", "total_assets": 100, "total_cost": 70.63, "total_pnl": 5.01,
           "positions": [{"market": "US", "cost_price": 2.01, "quantity": 5, "exchange_rate": 7.0289,
                          "market_value": None}]}
    summary = {"total": row, "accounts": [row]}
    actual = build_portfolio_metrics(summary)
    assert actual.valuation == "unavailable"
    assert actual.cost_basis_cny == 70.63 and actual.accounts[0].cost_basis_cny == 70.63
    row["positions"][0]["exchange_rate"] = None
    assert build_portfolio_metrics(summary).cost_basis_cny is None
    row["positions"][0].update(market="CN", cost_price=None)
    assert build_portfolio_metrics(summary).accounts[0].cost_basis_cny is None
    row["positions"][0]["cost_price"] = 0
    row["total_cost"] = 0
    assert build_portfolio_metrics(summary).cost_basis_cny == 0
