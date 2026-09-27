# Contributing to PanWatch

[简体中文](CONTRIBUTING.md)

Thanks for your interest in PanWatch. This guide covers the repository layout, local development, extension points, and contribution expectations.

## Project layout

- `src/modules/automation/` — scheduled and on-demand analysis agents, including the TradingAgents integration.
- `src/platform/` — shared AI, persistence, market-data, notification, and observability infrastructure.
- `src/modules/` — application features and their APIs.
- `frontend/src/` — React application, pages, shared components, and translations.
- `frontend/packages/` — reusable UI and API packages used by the application.
- `prompts/` — prompt templates used by analysis agents.
- `docs/` — user-facing documentation and diagrams.

## Development setup

The project uses Python for the backend and Node.js with pnpm for the frontend. For the recommended local setup, see the development section in [README.en.md](README.en.md).

Typical commands:

```bash
# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py

# Frontend, in another terminal
cd frontend
pnpm install
pnpm dev
```

Run backend tests with `python -m pytest`. In `frontend/`, use `pnpm test`, `pnpm check:i18n`, and `pnpm build` as appropriate for your change.

## Adding an automation agent

Agents typically extend `BaseAgent` and implement `collect()` and `build_prompt()`. Keep data collection, prompt construction, and result interpretation separate so each stage can be tested independently. Register new agents through the existing automation registry and provide a prompt template under `prompts/` when appropriate.

Before adding a new agent, check whether an existing agent or shared service already owns the data source or workflow. Avoid duplicating scheduling, notification deduplication, persistence, or AI-client behavior that is provided by the platform.

## Adding a data source

Prefer the existing market-data interfaces and collector patterns. Normalize provider responses into the shared domain models, use asynchronous HTTP clients for network calls, set sensible timeouts, and handle missing or malformed fields explicitly. Document the provider, API limits, configuration fields, and any market or symbol restrictions.

Never commit API keys, credentials, personal portfolio data, or generated local databases.

## Internationalization

User-visible frontend copy belongs in the locale resources under `frontend/src/i18n/locales/`. Keep Simplified Chinese and English resources in sync, use the existing formatting helpers for dates and numbers, and avoid translating user data, provider content, stock names, or model-generated text after the fact. Run `pnpm check:i18n` after migrating a component.

## Tests and pull requests

- Add or update focused tests for behavior changes; do not rely on a successful build alone.
- Run the relevant backend/frontend tests and `git diff --check` before opening a pull request.
- Use Conventional Commits, for example `fix(marketdata): handle missing quote volume`.
- A pull request should explain the background and user impact, the change, verification performed, known risks or limitations, and any genuine follow-up work.
- Keep changes focused and include documentation when a feature or configuration changes user-facing behavior.

For questions or bugs, open an issue with reproduction steps and sanitized logs. Do not include secrets or private account data.
