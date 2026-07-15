def test_import_package():
    import marketdata
    assert marketdata.__version__ == "0.1.0"


def test_public_api_exports():
    from marketdata import (
        MarketData, Symbol, Market, Quote,
        SourceConfig, StaticConfigProvider, InMemoryMetricsSink,
        ConfigProvider, MetricsSink,
    )
    assert MarketData is not None and Symbol is not None and Quote is not None
    assert SourceConfig is not None and StaticConfigProvider is not None
