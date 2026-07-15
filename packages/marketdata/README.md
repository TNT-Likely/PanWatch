# marketdata

PanWatch 的多市场(A股/港股/美股)行情数据抓取层,可插拔数据源。

- 对象式入口:`MarketData(config=..., metrics=...).quotes([...], market="CN")`
- 内部一层:`Vendor`(抓取解析)+ `Engine`(故障转移/缓存/指标)
- 通过 `ConfigProvider` / `MetricsSink` 端口解耦宿主,不依赖任何 web/DB

## 独立使用

    from marketdata import MarketData, StaticConfigProvider, SourceConfig
    md = MarketData(config=StaticConfigProvider({"quote": [SourceConfig(vendor="tencent", priority=1)]}))
    print(md.quotes(["600519", "00700", "AAPL"]))
