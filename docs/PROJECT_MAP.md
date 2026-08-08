# PanWatch 项目结构地图（2026-08-08 实测）

## 部署拓扑
- **8000** = FastAPI 主后端，跑在 **Docker 容器**内（镜像 `sunxiao0721/panwatch:latest`）
  - 容器挂载：`/home/ubuntu/.hermes:/hermes`(ro)、`panwatch_data:/app/data`
  - **没有挂载 `/tmp/PanWatch:/app`** → 改 8000 代码需 `docker cp` 进容器 + 重启，或重建镜像。直接改 `/tmp/PanWatch` 副本**不生效**
  - 容器内装了 `marketdata` 包（数据源抽象层），宿主机没有
- **8010** = 独立预测引擎，跑在**宿主机**（`/home/ubuntu/forecast_server.py` + `/home/ubuntu/forecast_lib/`），systemd 自启（hermes venv）
  - 宿主机**没有 marketdata 包** → 调不到 PanWatch 数据源，只能走 HTTP 调 8000
- **前端** = React 18 + TS，源码 `/tmp/PanWatch/frontend`，构建产物 `dist/`（容器内只有 build 产物）
- 代理层：8000 `src/web/api/forecast.py` 用 `FORECAST_ENGINE_URL` 转发到 8010

## 核心目录
```
PanWatch/
├── server.py              # 8000 入口 (FastAPI)
├── forecast_server.py     # 8010 入口 (预测引擎, 我主要改的)
├── packages/marketdata/   # ★ 数据源抽象层 (vendor: tdx/eastmoney/sina/tencent/ftshare/zhitu/northbound...)
├── src/
│   ├── collectors/        # CapitalFlowCollector / KlineCollector / market_sentiment_collector 等
│   ├── core/              # strategy_engine.py (策略引擎), marketdata_client.py (接线)
│   ├── web/api/           # 8000 的 HTTP 端点 (forecast/discovery/chat/market/recommendations...)
│   ├── models/            # MarketCode / 数据库模型
│   └── agents/            # TradingAgents 多 Agent
├── strategies/
│   └── panwatch_strategies.yaml  # 策略目录(含"资金热度(短线)"等)
├── forecast_lib/          # 8010 预测库 (forecast_traces/forecast_reports/forecast_utils/panwatch_bridge)
└── frontend/
```

## 数据源层（packages/marketdata）— 你说的关键入口
`MarketData` 类暴露的方法（全部走 vendor 抽象，可换源）：
- `klines` 历史K线
- `quotes` / `index_quotes` 实时行情
- `capital_flow` **资金流（东财口径，含 main_net/super/big/mid/small + main_net_5d）**
- `events` 事件、`news`/`flash_news` 新闻
- `fundamentals` 基本面
- `dragon_tiger` **龙虎榜**
- `margin` 融资融券、`shareholders` 股东、`dividend` 分红
- `northbound` **北向资金**
- `hot_stocks` / `hot_boards` / `board_stocks` **热点/板块**
- `index_klines` 指数

vendors 里 `capital_flow.py` 调东财 `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`（历史资金流日K线）。
⚠️ 海外直连该东财接口可能 502，但 marketdata vendor 经可达通道能拿东财口径（实测容器内可达）。

## 8000 已有规范化端点（8010 应优先复用，而非野路子）
- `src/web/api/discovery.py` — 热点板块 `get_hot_boards`、板块成分股 `get_board_stocks`（走 marketdata）
- `src/web/api/chat.py` — `get_capital_flow` 工具 → `CapitalFlowCollector.get_capital_flow_summary`
- `src/web/api/datasources.py` — 数据源健康检查（含 capital_flow/dragon_tiger/northbound 类型）
- `src/web/api/market.py` — 指数行情
- `src/web/api/recommendations.py` — 策略引擎代理（`refresh_strategy_signals` → `src/core/strategy_engine.py`）
- `src/web/api/klines.py` / `quotes.py` / `news.py` — 行情/新闻直出

## 我的改动在架构里的位置
- `forecast_lib/panwatch_bridge.py` — 8010 经 8000 `/api/tdx/ask`（问小达）拿资金流
  → **野路子**：应优先改成调 8000 规范化端点（discovery 的热点板块 / chat 的 get_capital_flow）
- `forecast_server.py` predict 流程 — 调 panwatch_bridge 拿东财口径资金流（已撤掉错的 zhitu）
- `forecast_lib/forecast_reports.py` `generate_wecom_report` — 企微版加资金面段（东财口径）
- `forecast_lib/forecast_utils.py` `calc_capital_score` / `build_recommendation` — 资金面参与权重+策略

## 待办/可扩展（你提到的"tdx 入口能拿很多数据"）
1. panwatch_bridge 改用 8000 规范化端点（discovery/chat）而非 tdx ask 自然语言
2. 资金面之外：把 marketdata 的 `dragon_tiger`(龙虎榜/游资)、`northbound`(北向)、`hot_boards`(板块) 接进 8010 预测特征
3. 考虑 8010 预测 vs 8000 策略引擎（strategy_engine + YAML）的关系：是否打通/避免重复
4. 铁律：改 8000 容器代码 = docker cp + 重启；不改 marketdata/tdx 底层逻辑，只复用已有接口
