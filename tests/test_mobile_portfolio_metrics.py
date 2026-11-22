from src.modules.mobile.portfolio_metrics import build_portfolio_metrics


def test_mobile_metrics_keep_web_aggregation_and_label_missing_amounts():
    cn_account = {
        "id": 1, "name": "Main", "total_assets": 320, "total_pnl": 20, "total_daily_pnl": 0,
        "positions": [{"market": "CN", "market_value": 100, "daily_pnl": 0}],
    }
    mixed_account = {
        "id": 2, "name": "Mixed", "total_assets": 508, "total_pnl": -98, "total_daily_pnl": 4,
        "positions": [
            {"market": "US", "market_value": 20, "daily_pnl": 4},
            {"market": "HK", "market_value": None, "daily_pnl": None},
        ],
    }
    result = build_portfolio_metrics({
        "total": {"total_assets": 828, "total_pnl": -78, "total_daily_pnl": 4},
        "accounts": [cn_account, mixed_account],
        "legacy_report": "Synthetic unexpected input",
    }).model_dump()
    assert result["currency"] == "CNY"
    assert result["basis"] == "current_holdings"
    assert result["available_assets"] == 828
    assert result["valuation"] == "partial"
    assert result["today_pnl"] == {"value": None, "coverage": "partial"}
    assert result["unrealized_pnl"] == {"value": None, "coverage": "partial"}
    assert result["accounts"][0]["today_pnl"] == {"value": 0, "coverage": "complete"}
    assert result["accounts"][0]["unrealized_pnl"] == {"value": 20, "coverage": "complete"}
    assert result["accounts"][1]["unrealized_pnl"] == {"value": None, "coverage": "partial"}
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
