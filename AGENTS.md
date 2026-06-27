# Repository Guidelines

## 铁律：禁止默认读取依赖目录

**默认不要**读取、搜索或遍历依赖与构建产物目录。这些目录体积巨大，对理解项目代码没有帮助。

禁止默认访问：`.venv/`、`venv/`、`node_modules/`、`dist/`、`build/`、`static/`、`__pycache__/`、`data/` 等。

需要依赖信息时，只读 `requirements.txt`、`pyproject.toml`、`package.json` 等声明文件，或查官方文档；**不要**钻进 `node_modules` 或 `.venv`。仅当用户明确要求排查某个依赖包内部问题时，才可定向读取单个文件。

## Project Structure & Module Organization
- `src/agents/` — Agent implementations (business logic). Add new agents here.
- `src/collectors/` — Data collectors (quotes, kline, news, etc.).
- `src/core/` — Core utilities (AI client, notifier, scheduler helpers).
- `src/web/` — FastAPI app (models, API routes, DB setup).
- `frontend/` — React + TypeScript (Vite + Tailwind). UI lives in `frontend/src/`.
- `prompts/` — Prompt templates used by agents.
- `config/`, `data/` — Config files and runtime data (persisted at `DATA_DIR`).
- `server.py` — Backend entrypoint; also registers agents and data sources.
- `tests/` — Placeholder for backend tests.
- `build.sh`, `Dockerfile` — Build frontend and container images.

## Build, Test, and Development Commands
- Backend (dev): `make dev-api`（自动 venv+依赖+uvicorn reload，监听 `:8000`）；或手动 `python server.py`。
- Frontend (dev): `make dev-web`（自动 pnpm install+dev，served on `http://localhost:5183`）。
- Frontend (build): `cd frontend && pnpm install --frozen-lockfile && pnpm build`.
- Docker image: `./build.sh <version>` (copies `frontend/dist` to `./static` and builds image).
- Run via Docker: `docker run -d -p 8000:8000 -v panwatch_data:/app/data sunxiao0721/panwatch:latest`.
- Tests (backend): add pytest tests under `tests/` then run `pytest`.

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indent, type hints required for new code. Files `snake_case.py`, classes `PascalCase`, functions/vars `snake_case`.
- Agents: implement in `src/agents/*.py`, register in `server.py` (`AGENT_REGISTRY`) and seed config in `seed_agents()`.
- Collectors: place in `src/collectors/`, keep stateless; return typed dataclasses.
- TypeScript: components `PascalCase.tsx` in `frontend/src/`, hooks `use-` prefix, utilities `camelCase.ts`.
- Prompts: one prompt file per agent in `prompts/` (e.g., `daily_report.txt`).

## Testing Guidelines
- Backend: structure tests as `tests/test_<module>.py`; prefer fast, isolated unit tests around agents, collectors, and core.
- Coverage: target meaningful coverage for new modules (no strict threshold yet, but include happy-path and error cases).
- Fixtures: use factory helpers for DB models; avoid network calls (mock collectors and AI clients).

## Trading Discipline
- Long-term position architecture lives in `docs/long-term-position-architecture.md`; follow it when changing trading logic, prompts, portfolio context, or related UI.
- A long-term holding is not a slower short-term stop-loss flow. Respect the user's thesis, target weight, max weight, staged add plan, and recent trade history.
- Core and satellite positions must be treated separately: protect core positions for the long-term thesis; use satellite positions for swing adjustments.
- Never suggest unlimited averaging down. Any add suggestion must check max allocation, available cash, triggered levels, and today's trades.
- Today's trade records take priority: do not repeat add suggestions after a same-day buy, and do not repeat reduce/sell suggestions after a same-day sell.

## Commit & Pull Request Guidelines
- Commit format: `<type>: <subject>` where type ∈ `{feat, fix, docs, refactor, style, test}`.
  Example: `feat: add intraday monitor agent`.
- Pull Requests: include a clear description, linked issues, and screenshots/GIFs for UI changes. Update docs/prompts when applicable.
- CI hygiene: ensure backend runs (`python server.py`) and frontend builds (`pnpm build`). No secrets in commits; use `.env` or UI settings.

## Security & Configuration Tips
- Secrets: do not commit API keys; configure via UI or env vars (`.env`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `JWT_SECRET`, `DATA_DIR`).
- Network/SSL: optional corporate CA via `data/ca-bundle.pem` is auto-managed; respect `HTTP(S)_PROXY`/app proxy settings.
- Playwright: in Docker, browsers install under `DATA_DIR/playwright` automatically; local dev uses system install.
