from types import SimpleNamespace


def test_stock_response_includes_agent_display_name_from_config():
    from src.modules.market.api.stocks import _stock_to_response

    stock = SimpleNamespace(
        id=1,
        symbol="600519",
        name="贵州茅台",
        market="CN",
        sort_order=1,
        agents=[
            SimpleNamespace(
                agent_name="daily_report",
                schedule="",
                ai_model_id=None,
                notify_channel_ids=[],
            )
        ],
    )

    result = _stock_to_response(stock, {"daily_report": "收盘复盘"})

    assert result["agents"] == [
        {
            "agent_name": "daily_report",
            "display_name": "收盘复盘",
            "schedule": "",
            "ai_model_id": None,
            "notify_channel_ids": [],
        }
    ]
