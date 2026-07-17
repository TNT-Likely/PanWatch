# marketdata

多市场(A股 / 港股 / 美股)行情数据抓取层,**可插拔数据源 + 主备故障转移**。脱胎于 PanWatch,但**零 `src/` / web / DB 依赖**——通过两个注入端口(`ConfigProvider` / `MetricsSink`)解耦宿主,可独立使用或嵌入任意项目。

宿主(PanWatch)接入是**单一路径**:数据抓取全部经由本包,`src/core/providers`/`akshare_collector` 等旧实现已删除,没有灰度 flag、没有回退分支。

## 设计

一条路径,两层:

```
调用方 → MarketData(对象式 API)
            └─ Engine(每类型一个:按 priority 主备链故障转移 + TTL 缓存 + 指标)
                 └─ Vendor(每家源一个:只做"抓取 + 解析成标准类型",内部无 fallback)
                      └─ market_get(直连 trust_env=False + 按 host 节流 + 退避重试)
```

- **同步内核**(与底层 HTTP 一致、最好测);异步调用方自行 `asyncio.to_thread(...)` 包一层。
- **两个端口(Protocol)**:`ConfigProvider` 供"某类型在某市场按优先级有哪些启用源 + 凭证",`MetricsSink` 收每次取数的成败/延迟。包只依赖这俩接口,不碰宿主的 DB/web。
- **返回带类型 dataclass**(`Quote` / `Bar` / `CapitalFlow` / `EventItem` / `FlashNews` / `Fundamentals` / `DragonTigerItem` / `MarginItem` / `ShareholderItem` / `DividendItem` / `NorthboundItem` / `HotStock` / `HotBoard`),不是裸 dict。
- **`Symbol` 值对象**:一处归一化各市场代码(腾讯前缀 / 东财 secid / yfinance 后缀),消灭散落各处的 `_to_market`。

## 安装

```bash
pip install -e ./packages/marketdata          # 本地 editable(monorepo 内)
# 依赖仅 httpx;yfinance 为可选 extra:
pip install -e "./packages/marketdata[yfinance]"
```

## 快速上手(独立使用)

用内置的静态配置端口即可跑,无需任何宿主:

```python
from marketdata import MarketData, StaticConfigProvider, SourceConfig

md = MarketData(config=StaticConfigProvider({
    "quote": [
        SourceConfig(vendor="tencent", priority=0),   # 主源
        SourceConfig(vendor="sina",    priority=5),   # US/HK 备源(免 key 免代理)
    ],
    "kline": [
        SourceConfig(vendor="tencent",   priority=0),
        SourceConfig(vendor="eastmoney", priority=5),  # CN/HK 长历史兜底
        SourceConfig(vendor="stooq",     priority=15), # US 兜底
    ],
    "capital_flow": [SourceConfig(vendor="eastmoney", priority=0)],
    "events":       [SourceConfig(vendor="eastmoney", priority=0)],
}))

md.quotes(["600519", "00700", "AAPL"])        # 跨市场,自动按市场分组 → list[Quote]
md.klines("600519", market="CN", days=120)    # list[Bar]
md.capital_flow("600519")                      # CapitalFlow | None
md.events(["600519"], since_days=7)            # list[EventItem]
md.hot_stocks(market="CN", mode="turnover")    # list[HotStock](发现,不经 Engine)
md.health()                                     # {vendor: {success_rate, p50_latency_ms, last_error, ...}}
```

## API 速查

### `MarketData(config: ConfigProvider, metrics: MetricsSink | None = None)`

| 方法 | 参数 | 返回 | 说明 |
|---|---|---|---|
| `quotes` | `symbols, *, market=None` | `list[Quote]` | 批量报价。`symbols` 可跨市场;`market` 省略时按代码**自动识别**并分组,每市场走一次 Engine。 |
| `klines` | `symbol, *, market, days=120, min_count=1` | `list[Bar]` | 单只日 K。`min_count`:某源条数 `< min_count` 视为不足→试下一个;全不足→返回最长的那个。 |
| `capital_flow` | `symbol, *, market="CN"` | `CapitalFlow \| None` | 单只资金流向。 |
| `events` | `symbols, *, market="CN", since_days=7` | `list[EventItem]` | 批量结构化事件(东财公告→类型/重要度启发式)。 |
| `flash_news` | `*, market="CN", limit=50, keyword=None` | `list[FlashNews]` | 快讯(7×24)。**市场级**(`symbols` 恒空),仍走 Engine 做主备/缓存/健康度;`keyword` 在拿到结果后本地过滤 title/content。 |
| `fundamentals` | `symbols, *, market=None` | `list[Fundamentals]` | 批量基本面/财务。跨市场自动分组,范式同 `quotes`。 |
| `dragon_tiger` | `*, date=None, market="CN"` | `list[DragonTigerItem]` | 龙虎榜,**市场级**单日快照。`date` 未给出时不猜测"今天",直接返回 `[]`。 |
| `margin` | `symbols, *, market=None` | `list[MarginItem]` | 批量融资融券(每只取最新一条快照)。 |
| `shareholders` | `symbols, *, market=None` | `list[ShareholderItem]` | 批量股东户数(每只取最新一期)。 |
| `dividend` | `symbols, *, market=None` | `list[DividendItem]` | 批量分红(每只返回全部历史)。 |
| `northbound` | `*, market="CN"` | `list[NorthboundItem]` | 北向资金,**市场级**,取当日末值快照。 |
| `index_quotes` | `tencent_symbols` | `list[dict]` | 指数行情。按**原始腾讯符号**(`sh000001`/`hkHSI`/`usDJI`…)取,不经 `Symbol.parse`(指数代码可能与个股撞号)。不经 Engine/registry。 |
| `index_klines` | `code, *, market, days=120` | `list[Bar]` | 指数日K。仅 `INDEX_SECID`(client.py)显式映射的指数(沪深300/上证/深成指/创业板指/恒生)有数据;未映射(如美股指数)→ `[]`(fail-soft)。不经 Engine/registry。 |
| `hot_stocks` / `hot_boards` / `board_stocks` | 见下 | `list[HotStock/HotBoard]` | 东财热门榜。**市场级、不经 Engine**(非 symbol 模型),直连 `DiscoveryVendor`。`hot_stocks(*, market="CN", mode="turnover", limit=20, proxy=None)`、`hot_boards(*, market="CN", mode="gainers", limit=12, proxy=None)`、`board_stocks(*, board_code, mode="gainers", limit=20, proxy=None)`。 |
| `health` | — | `dict[str, dict]` | 每个 vendor 的内存健康度快照(成功率 / p50 延迟 / 最近错误 / 样本数)。 |

> `klines`/`capital_flow`/`events` **不在包内缓存**(`cache_ttl_sec=0`),缓存/节流交给宿主(PanWatch 的 collector 层有市场态感知缓存);`quotes` 有 5s 短 TTL 防抖;`flash_news` 30s、`fundamentals`/`dragon_tiger`/`margin`/`shareholders`/`dividend` 300s、`northbound` 60s(均为包内 Engine 层 TTL,详见 `client.py`)。

### 类型

- `Symbol(market: Market, code)` —— `Symbol.parse("600519")`(自动识别)/ `Symbol.parse("00700", "HK")`;`.to_tencent()` / `.to_eastmoney_secid()` / `.to_yfinance()`。`Market` = `CN` / `HK` / `US`。
- `Quote`:symbol / market / current_price / name / prev_close / open_price / high_price / low_price / change_amount / change_pct / volume / turnover / turnover_rate / volume_ratio / pe_ratio / circulating_market_value / total_market_value / timestamp。
- `Bar`:date / open / close / high / low / volume。
- `CapitalFlow`:symbol / name / main_net_inflow / main_net_inflow_pct / super_/big_/mid_/small_net_inflow / main_net_5d。
- `EventItem`:source / external_id / event_type / title / publish_time / symbols / importance / url。
- `FlashNews`:source / external_id / title / content / publish_time / symbols / importance / url。
- `Fundamentals`:symbol / market / name + 估值类(pe_ttm / pe_static / pb / ps_ttm / total_market_value / circulating_market_value / dividend_yield / total_shares / float_shares)+ 财报类(eps / bps / roe / revenue / net_profit / gross_margin / net_margin / revenue_yoy / net_profit_yoy / report_date)/ timestamp。
- `DragonTigerItem`:trade_date / symbol / name / reason / close / change_pct / net_buy / buy_amt / sell_amt / turnover_pct。
- `MarginItem`:date / symbol / rz_balance / rz_buy / rz_repay / rq_balance / rq_sell_vol / rq_repay_vol / total_balance。
- `ShareholderItem`:report_date / symbol / holder_num / change_num / change_ratio / avg_shares。
- `DividendItem`:ex_date / symbol / dividend_per_share / transfer_ratio / bonus_ratio / progress。
- `NorthboundItem`:date / hgt_net / sgt_net / total_net / time。
- `HotStock` / `HotBoard`:榜单条目。

### 端口(解耦宿主的关键)

```python
class ConfigProvider(Protocol):
    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]: ...

class MetricsSink(Protocol):
    def record(self, *, vendor, datatype, market, ok, count, latency_ms, error="") -> None: ...

@dataclass
class SourceConfig:
    vendor: str            # "tencent" / "sina" / "eastmoney" / ...
    priority: int = 100    # 越小越优先
    enabled: bool = True
    config: dict = {}      # 凭证/参数:token / cookies / proxy ...(透传给 vendor.fetch)
    supports_batch: bool = False
```

内置默认实现:`StaticConfigProvider({datatype: [SourceConfig, ...]})`、`InMemoryMetricsSink()`(滚动窗口最近 100 次)。宿主可换成自己的实现(如 PanWatch 用 DB 表驱动的 `DbConfigProvider`,见下)。

## 数据类型覆盖矩阵(11 类)

以 `src/marketdata/registry.py` 的 `VENDOR_CLASSES_BY_TYPE` 为准——这是 type→vendor 的权威清单,新增/调整 vendor 必须同步这里。

| type | 返回 dataclass | 已实现 vendor(provider 名) | 覆盖市场 | 粒度 |
|---|---|---|---|---|
| `quote` | `Quote` | `tencent` / `sina` / `eastmoney` / `yfinance` | tencent: CN+HK+US;sina: US+HK;eastmoney: CN;yfinance: HK+US(可选依赖) | 按 symbol |
| `kline` | `Bar` | `tencent` / `stooq` / `eastmoney` / `yahoo` | tencent: CN+HK+US;eastmoney: CN+HK;stooq: US;yahoo: US+HK | 按 symbol |
| `capital_flow` | `CapitalFlow` | `eastmoney` / `sina` | eastmoney: CN+HK+US;sina: CN | 按 symbol |
| `events` | `EventItem` | `eastmoney` | CN | 按 symbol |
| `flash_news` | `FlashNews` | `cls` / `sina` / `eastmoney` | 均 CN | **市场级**(symbols 恒空) |
| `fundamentals` | `Fundamentals` | `tencent` / `eastmoney` | tencent: CN;eastmoney: CN+HK+US | 按 symbol |
| `dragon_tiger` | `DragonTigerItem` | `eastmoney` | CN | **市场级**(单日快照,按 date 过滤) |
| `margin` | `MarginItem` | `eastmoney` | CN | 按 symbol(取最新一条) |
| `shareholders` | `ShareholderItem` | `eastmoney` | CN | 按 symbol(取最新一期) |
| `dividend` | `DividendItem` | `eastmoney` | CN | 按 symbol(返回全部历史) |
| `northbound` | `NorthboundItem` | `ths`(同花顺 hexin) | CN | **市场级**(symbols 恒空,取当日末值) |

此外还有两类**不进 registry/不进 Engine**的特殊入口(市场级、非 symbol 模型,故不计入上述 11 类):
- **discovery**(`hot_stocks`/`hot_boards`/`board_stocks`):东财热门榜,单源,直连 `DiscoveryVendor`。
- **index**(`index_quotes`/`index_klines`):指数行情/K线,`index_quotes` 走腾讯原始符号,`index_klines` 走 `INDEX_SECID` 显式映射 + 东财K线。

**故障转移**:Engine 按 `ConfigProvider` 返回的 priority 顺序试 vendor,过滤 `enabled` + `supports_markets`;首个"成功且非空(kline 为 ≥`min_count`)"即返回并缓存;全失败返回空。每次取数经 `MetricsSink` 记录,`health()` 可读。

### 权威表:`PACKAGE_VENDORS_BY_TYPE`

`registry.py` 里的 `PACKAGE_VENDORS_BY_TYPE`(由 `VENDOR_CLASSES_BY_TYPE` 派生)是"某 type 合法 vendor 名集合"的**唯一真相源**——不会出现"改了 Engine 忘了改文档/权威表"的漂移。

宿主(PanWatch `DataSource` 表)据此判定某行 `(type, provider)` 是否为孤儿:

```python
legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 内该 type 的 provider 集合
```

`discovery`/`index` 是市场级、非 symbol 模型,不进 Engine/不进 `DataSource` taxonomy,故不出现在此表。

### ⚠️ 字段映射校准现状

B 阶段新增类型(`flash_news` / `fundamentals` / `dragon_tiger` / `margin` / `shareholders` / `dividend` / `northbound`)的字段解析,多数**未经真实网络抓取验证**(开发沙箱代理会拦截东财/同花顺等接口,只能靠接口文档 + 历史 PanWatch collector 实现推断字段映射)。各 dataclass 的 docstring 里已标注"字段待实抓校准"。首次在生产接入这些类型时,建议:

1. 在「数据源」页对该 `(type, provider)` 点「测试」,核对返回字段是否符合预期(尤其是 `NorthboundItem.sgt_net` 这类已知不稳定字段)。
2. 若字段错位/为空,对照 vendor 源码(`src/marketdata/vendors/*.py`)与东财/同花顺接口实际响应调整解析逻辑,而不是照抄文档字段名。

## 新增一个数据源

1. 在 `src/marketdata/vendors/` 写一个 vendor,继承对应标记基类——`QuoteVendor` / `KlineVendor` / `CapitalFlowVendor` / `EventsVendor` / `FlashNewsVendor` / `FundamentalsVendor` / `DragonTigerVendor` / `MarginVendor` / `ShareholdersVendor` / `DividendVendor` / `NorthboundVendor`(均定义在 `vendors/base.py`)——实现 `fetch(symbols: list[Symbol], config: dict) -> list[<类型>]`,用包内 `market_get` 发请求(`verify=`/`proxy=`/`encoding=` 按源需要)。设 `name` 与 `supports_markets`。
2. 在 `registry.py` 的 `VENDOR_CLASSES_BY_TYPE[<datatype>]` 里加一行 `"<vendor_name>": <VendorClass>`(`PACKAGE_VENDORS_BY_TYPE` 与 `MarketData.__init__` 的 Engine `vendors={}` 都由它自动派生,不用另外改 `client.py`)。
3. 通过 `ConfigProvider` 给它一条 `SourceConfig(vendor="<name>", priority=...)`(宿主侧配置/种子)。
4. 加解析单测(monkeypatch 该 vendor 模块的 `market_get`)。

## 嵌入宿主(PanWatch 为例)

宿主实现两个端口即可接入:

```python
class DbConfigProvider:                       # 读 DataSource 表 → SourceConfig
    def sources_for(self, datatype, market):
        rows = query(DataSource, type=datatype, enabled=True, order_by=priority)
        return [SourceConfig(vendor=r.provider, priority=r.priority,
                             config=r.config or {}, supports_batch=bool(r.supports_batch))
                for r in rows]

md = MarketData(config=DbConfigProvider())    # metrics 用默认内存 sink
```

PanWatch 侧是**单一路径**:`src/core/marketdata_client.py` 用进程级单例 `get_market_data()` 持有一个 `MarketData(config=DbConfigProvider())`,各 collector/agent 直接调用它取数;没有 flag、没有兼容层分支、没有回退到旧 `akshare_collector`(旧实现已删除)。`health()` 喂到「数据源」页的健康度面板。

> 历史备注(已移除,仅存档参考):早期 Phase 1 曾用 `USE_MARKETDATA` 环境变量做灰度切换,新旧两套并存;该 flag 与旧路径已在后续阶段整体下线,现在只有一条路径。

## 测试

```bash
cd packages/marketdata && python -m pytest -q     # 全部 mock HTTP(不发真实网络)
```

## License / 参考

抓取端点参考并适配自 `simonlin1212/global-stock-data`、`simonlin1212/a-stock-data`(Apache-2.0)。
