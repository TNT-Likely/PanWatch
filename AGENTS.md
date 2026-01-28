# Repository Guidelines

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
- Backend (dev): `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt && python server.py`
- Frontend (dev): `cd frontend && pnpm install && pnpm dev` (served on `http://localhost:5173`).
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

## Commit & Pull Request Guidelines
- Commit format: `<type>: <subject>` where type ∈ `{feat, fix, docs, refactor, style, test}`.
  Example: `feat: add intraday monitor agent`.
- Pull Requests: include a clear description, linked issues, and screenshots/GIFs for UI changes. Update docs/prompts when applicable.
- CI hygiene: ensure backend runs (`python server.py`) and frontend builds (`pnpm build`). No secrets in commits; use `.env` or UI settings.

## Security & Configuration Tips
- Secrets: do not commit API keys; configure via UI or env vars (`.env`, `AUTH_USERNAME`, `AUTH_PASSWORD`, `JWT_SECRET`, `DATA_DIR`).
- Network/SSL: optional corporate CA via `data/ca-bundle.pem` is auto-managed; respect `HTTP(S)_PROXY`/app proxy settings.
- Playwright: in Docker, browsers install under `DATA_DIR/playwright` automatically; local dev uses system install.
