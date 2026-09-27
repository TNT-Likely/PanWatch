# PanWatch

[简体中文](README.md) | [English](README.en.md)

**A self-hosted, AI-powered stock monitoring assistant with [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent investment research** — real-time monitoring, portfolio management, intelligent analysis, and multi-channel notifications for China A-shares, Hong Kong, and U.S. markets.

> 🌐 The English interface is being rolled out incrementally and is currently experimental. Some business pages may still contain Chinese text.

[![GitHub stars](https://img.shields.io/github/stars/TNT-Likely/PanWatch?style=flat&logo=github&color=yellow)](https://github.com/TNT-Likely/PanWatch/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/sunxiao0721/panwatch?logo=docker&label=docker%20pulls&color=2496ED)](https://hub.docker.com/r/sunxiao0721/panwatch)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/TNT-Likely/PanWatch)](https://github.com/TNT-Likely/PanWatch/commits/main)
[![PWA](https://img.shields.io/badge/PWA-installable-5A0FC8?logo=pwa&logoColor=white)](https://github.com/TNT-Likely/PanWatch)

![PanWatch TradingAgents deep-analysis demo](docs/screenshots/tradingagents-demo.gif)

> 🧠 **Start from a portfolio holding → let a nine-agent TradingAgents research team analyze it → follow the bull/bear debate and risk review → receive a PM decision memo and the complete reasoning trail in your messaging app within 3–5 minutes.**

## 📸 Feature Overview

| Portfolio · Multi-account overview | Opportunities · AI-scored ideas |
|:---:|:---:|
| ![Portfolio management](./docs/screenshots/portfolio.png) | ![AI-scored opportunities](./docs/screenshots/opportunities.png) |
| **Paper trading · Equity curve and performance** | **Deep stock analysis** |
| ![Paper trading](./docs/screenshots/papertrading.png) | ![Stock details](./docs/screenshots/stock-detail.png) |
| **Technical confluence · MACD/RSI/KDJ at a glance** | **Price alerts · Combined conditions** |
| ![Technical indicators](./docs/screenshots/technicals.png) | ![Price alerts](./docs/screenshots/alerts.png) |

<details>
<summary>Mobile screenshots</summary>

<img src="./docs/screenshots/mobile.png" width="300" /> <img src="./docs/screenshots/mobile-detail.png" width="300" />

> 📱 PanWatch is an installable PWA and can be added to your mobile home screen like a native app.

</details>

> 💡 If PanWatch is useful to you, please consider giving the project a ⭐ **Star**. It is the best way to support the project and help more people discover it.

## 🧠 Deep Analysis with TradingAgents

PanWatch integrates [TradingAgents](https://github.com/TauricResearch/TradingAgents), the multi-agent investment decision framework with more than 76k stars. Select the 🧠 icon next to a portfolio holding to start an analysis:

- **Four analyst roles** — technical, sentiment, news, and fundamentals — followed by a **bull/bear debate**, **risk review**, and **portfolio-manager decision**.
- A complete reasoning trail is generated in 3–5 minutes and can be delivered to Telegram, WeCom, or DingTalk.
- The default model is `deepseek-chat`; a typical run costs about USD 0.05, keeping monthly spending predictable.
- [View the TradingAgents deep-analysis flowchart](docs/tradingagents-flow.md) (Chinese)

## Why PanWatch?

- **Private by design** — self-host it so your portfolio data stays under your control.
- **AI-native** — PanWatch goes beyond displaying indicators: it gives AI the context needed to understand your holdings, style, and objectives.
- **Ready to use** — deploy with Docker and finish the initial setup in about five minutes.

## Core Features

<details>
<summary><b>Intelligent agent system</b></summary>

| Agent | Trigger | Purpose |
|-------|---------|---------|
| **Pre-market outlook** | Before each market session | Combines overnight U.S. market moves, news, and technical structure into an action plan for the day. |
| **Intraday monitor** | During trading hours | Watches unusual moves and sends alerts when indicators such as RSI, KDJ, and MACD align. |
| **Daily report** | After market close | Reviews the session, analyzes capital flows, and prepares a plan for the next trading day. |

</details>

<details>
<summary><b>Professional technical analysis</b></summary>

- **Trend indicators:** moving-average alignment, MACD crosses, and Bollinger Band breakouts.
- **Momentum indicators:** RSI overbought/oversold conditions and KDJ saturation or divergence.
- **Price and volume:** volume-ratio anomalies, low-volume pullbacks, and high-volume breakouts.
- **Pattern recognition:** hammer, engulfing, doji, and other candlestick patterns.
- **Support and resistance:** automatic calculation of multiple support and resistance levels.

</details>

<details>
<summary><b>Multiple markets and accounts</b></summary>

- **Markets:** real-time quotes for China A-shares, Hong Kong stocks, and U.S. stocks.
- **Account management:** manage brokerage accounts independently while viewing consolidated total assets.
- **Trading styles:** set short-term, swing, or long-term preferences for more relevant AI suggestions.

</details>

<details>
<summary><b>Multi-channel notifications</b></summary>

Telegram / WeCom / DingTalk / Feishu / Bark / custom webhooks

</details>

<details>
<summary><b>Price alerts</b></summary>

- Combine price, percentage change, turnover, volume ratio, and other conditions with AND/OR logic.
- Limit rules to market hours or keep them active all day; configure cooldowns, daily trigger limits, and repeat behavior.
- Set an expiration date and `HH:mm` time in the rule dialog, or leave it empty so the rule never expires.
- Choose notification channels per rule, or use the system default when none is selected.

</details>

## Quick Start

```bash
docker run -d \
  --name panwatch \
  -p 8000:8000 \
  -v panwatch_data:/app/data \
  sunxiao0721/panwatch:latest
```

Open `http://localhost:8000` and create your username and password on first launch.

The image includes the system dependencies required by Playwright. Chromium is downloaded and installed into the mounted volume (by default `/app/data/playwright`) on the first container startup. This can take a few minutes and requires network access.

If you do not need browser-based features such as screenshots, set `PLAYWRIGHT_SKIP_BROWSER_INSTALL=1` when starting the container to skip the initial Chromium installation.

<details>
<summary>Docker Compose</summary>

```yaml
version: '3.8'
services:
  panwatch:
    image: sunxiao0721/panwatch:latest
    container_name: panwatch
    ports:
      - "8000:8000"
    volumes:
      - panwatch_data:/app/data
    restart: unless-stopped

volumes:
  panwatch_data:
```

```bash
docker-compose up -d
```

</details>

<details>
<summary>Environment variables</summary>

| Variable | Description | Default |
|----------|-------------|---------|
| `AUTH_USERNAME` | Preconfigured login username | Set on first visit |
| `AUTH_PASSWORD` | Preconfigured login password | Set on first visit |
| `JWT_SECRET` | Secret used to sign JWTs | Generated automatically |
| `DATA_DIR` | Data storage directory | `./data` |
| `TZ` | Application timezone used for agent schedules and displayed times | `Asia/Shanghai` |
| `PLAYWRIGHT_SKIP_BROWSER_INSTALL` | Skip the initial Chromium installation when browser features are not required | Not set |
| `LOG_LEVEL` | Console log level. `INFO` prints business events and errors; use `DEBUG` for scheduler heartbeats, collection steps, and other diagnostics. The UI log panel always retains the complete log. | `INFO` |
| `HTTP_PROXY` / `HTTPS_PROXY` / `http_proxy` | Outbound HTTP proxy. Configure it through an external environment variable, `http_proxy=http://host:port` in `.env`, or **Settings → Global HTTP Proxy**. Priority: external environment variables > UI > `.env`. `NO_PROXY` includes `localhost,127.0.0.1` by default. | Not set |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry OTLP endpoint, such as `http://jaeger:4318`. Export remains completely disabled when this is empty. The optional dependencies in `requirements-otel.txt` are also required. | Not set (disabled) |

</details>

<details>
<summary>Initial setup</summary>

1. Open the web interface and create your login credentials.
2. Go to **Settings → AI Services** and configure an OpenAI-compatible API, such as OpenAI, Zhipu AI, DeepSeek, or Ollama.
3. Go to **Settings → Notification Channels** and add Telegram or another delivery channel.
4. Go to **Portfolio → Add Stock**, add a symbol to your watchlist, and enable the relevant agents.

</details>

<details>
<summary>Local development</summary>

**Requirements:** Python 3.10+ / Node.js 18+ / pnpm

```bash
# One-command development setup (recommended)
make dev-api          # Backend with automatic venv/dependencies on :8000
make dev-web          # Frontend with automatic pnpm install on :5183

# Or start each service manually
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python server.py                              # Backend on :8000

cd frontend && pnpm install && pnpm dev       # Frontend on :5183
```

The frontend development server runs at `http://localhost:5183` and proxies `/api` to `127.0.0.1:8000`.

Port `5183` is used instead of Vite's default `5173` to avoid conflicts with other locally running projects such as BeeCount-Cloud.

</details>

<details>
<summary><b>Technology stack</b></summary>

**Backend:** FastAPI / SQLAlchemy / APScheduler / OpenAI SDK

**Frontend:** React 18 / TypeScript / Tailwind CSS / shadcn/ui

</details>

<details>
<summary><b>OpenTelemetry export (optional and disabled by default)</b></summary>

PanWatch includes a built-in observability stack with structured logs, end-to-end `trace_id` correlation, the `agent_runs` table, and node-level TradingAgents progress and cost reporting. It works without any external service.

You can optionally add standard [OpenTelemetry](https://opentelemetry.io/) export and send traces to Jaeger, Tempo, Langfuse, or another compatible APM. PanWatch maps three types of spans:

- **One agent run** → a root span correlated with the `trace_id` from `agent_runs`.
- **One LLM call** → a `gen_ai` child span following the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/), including `gen_ai.system`, `gen_ai.request.model`, input/output token usage, and `gen_ai.operation.name`.
- **One TradingAgents node** → a child span that reuses the node-level progress callback.

Export is a complete no-op when the optional dependencies are not installed and no endpoint is configured.

Enable it in three steps:

```bash
# 1. Install the optional dependencies
pip install -r requirements-otel.txt

# 2. Configure your collector or APM endpoint
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_SERVICE_NAME=panwatch   # Optional; defaults to panwatch

# 3. Start PanWatch normally. The startup log will confirm that OTel export is enabled.
python server.py
```

Run a local Jaeger instance to verify the integration:

```bash
docker run -d --name jaeger -p 16686:16686 -p 4318:4318 \
  jaegertracing/all-in-one:latest
# Trigger an agent, open http://localhost:16686, and select service=panwatch.
```

Langfuse and Tempo work the same way: point `OTEL_EXPORTER_OTLP_ENDPOINT` to their OTLP endpoint.

</details>

<details>
<summary><b>Publishing Docker images</b></summary>

The repository includes a GitHub Actions release workflow:

- Pushing a tag such as `0.2.3` automatically builds and publishes:
  - `sunxiao0721/panwatch:0.2.3`
  - `sunxiao0721/panwatch:latest`
- The workflow can also be started manually with `workflow_dispatch` and an explicit version.

Configure these repository secrets before publishing:

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

</details>

## Support the Project

If PanWatch helps you, you can buy the author a coffee:

| WeChat Pay | Alipay |
|:---:|:---:|
| <img src="./docs/donate/wechat.png" width="240" /> | <img src="./docs/donate/alipay.png" width="240" /> |

## Contributing

Issues and pull requests are welcome. See the [contribution guide](CONTRIBUTING.md) for custom agent and data-source development. The current contribution guide is in Chinese.

Community chat (Telegram): [t.me/panwatch](https://t.me/panwatch)

## License

[MIT](LICENSE)
