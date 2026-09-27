# Contributing to PanWatch

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

Thank you for helping improve PanWatch. This guide describes the current repository layout, development workflow, extension points, and pull-request expectations.

## Before you start

- Search existing issues and pull requests before starting duplicate work.
- For large features, architecture changes, new dependencies, or breaking behavior, open an issue first and describe the user problem and proposed boundary.
- Never commit API keys, tokens, cookies, personal portfolio data, local databases, generated reports, or logs containing private data.
- Keep a pull request focused. Separate unrelated cleanup from the behavior being changed.

## Repository map

| Path | Responsibility |
|---|---|
| `src/modules/` | Product modules and their API routes, services, and workflows |
| `src/modules/automation/` | Scheduled and on-demand agents, catalog, scheduler, and TradingAgents integration |
| `src/platform/` | Shared AI, persistence, market-data, notification, observability, and runtime infrastructure |
| `packages/marketdata/` | Standalone typed market-data package with vendor failover |
| `packages/pan-agent-*` | Reusable agent runtime, metering, and tool-research packages |
| `frontend/src/` | React application, pages, components, hooks, and locale resources |
| `frontend/packages/` | Shared frontend API, business UI, and base UI packages |
| `prompts/` | Prompt templates used by analysis workflows |
| `tests/` | Backend, integration, architecture, and evaluation tests |
| `frontend/tests/` | Vitest and Testing Library tests |
| `docs/` | Public documentation, diagrams, screenshots, and donation assets |

Respect module ownership: product code may depend on platform services, while standalone packages must not import the PanWatch application or database. Architecture tests enforce important boundaries.

## Development setup

### Requirements

- Python 3.10 or newer; the Docker runtime uses Python 3.11.
- Node.js 24.14.0.
- pnpm 9.15.9.

### Recommended commands

```bash
# Terminal 1: create .venv, install backend dependencies, and start :8000
make dev-api

# Terminal 2: install frontend dependencies and start :5183
make dev-web
```

The frontend development server proxies `/api` to `127.0.0.1:8000`.

Manual setup is also supported:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py

cd frontend
pnpm install
pnpm dev
```

Copy `.env.example` to `.env` when local configuration is needed. Use disposable test credentials and keep `.env` untracked.

To install the repository's pre-push test hook:

```bash
make install-hooks
```

## Development workflow

1. Create a branch from the latest default branch.
2. Add or update a focused test before or alongside the implementation.
3. Keep network calls mocked in unit tests; tests must not require paid APIs or send real notifications.
4. Update both language resources and public documentation when user-visible behavior changes.
5. Run checks proportional to the affected area, followed by `git diff --check`.
6. Open a pull request using the format below.

## Validation

Run the smallest focused test while iterating, then the relevant suite before opening a pull request.

```bash
# Backend suite
.venv/bin/python -m pytest -q

# Frontend suite, translation guard, type/build verification
pnpm --dir frontend exec vitest run
pnpm --dir frontend run check:i18n
pnpm --dir frontend run build

# Whitespace and conflict-marker check
git diff --check
```

Package-specific changes should also run their own tests, for example:

```bash
.venv/bin/python -m pytest packages/marketdata/tests -q
.venv/bin/python -m pytest packages/pan-agent-runtime/tests -q
```

Do not describe a check as passing unless you actually ran it. If an environment prevents a required check, explain the limitation in the pull request.

## Adding or changing an agent

Before creating an agent, decide whether it is:

- a **workflow agent**: user-visible and eligible for scheduling; or
- a **capability agent**: invoked by another workflow or UI action and not independently scheduled.

Use these integration points:

1. Implement the agent under `src/modules/automation/`, normally by extending `BaseAgent` and separating `collect()` from `build_prompt()`.
2. Reuse `AgentContext` for AI, portfolio, watchlist, notification, and report-language state. Do not create parallel AI clients or notification pipelines.
3. Put substantial prompts in `prompts/`. Preserve required machine-readable output structures when adding language instructions.
4. Add the user-facing seed definition to `AGENT_SEED_SPECS` in `src/modules/automation/agent_catalog.py`, including kind, visibility, schedule, execution mode, and safe defaults.
5. Add the implementation class to `AGENT_REGISTRY` in `server.py` when it can be executed directly.
6. Add English and Chinese catalog labels under `agentsPage.catalog` in the frontend locale resources when the agent is visible in the UI.
7. Add focused tests for collection, parsing, notification policy, scheduling, idempotency, failure states, and English report output as applicable.

Prefer existing scheduler, persistence, tracing, cost metering, deduplication, and failover behavior. New workflows must end in an observable success or failure state and must not silently swallow exceptions.

## Adding or changing a market-data source

Choose the correct layer first:

- Typed quote, K-line, fundamentals, flow, event, or discovery vendors belong in `packages/marketdata/`.
- PanWatch-specific orchestration, caching, screenshots, or adapters belong in `src/platform/marketdata/collectors/`.

For a vendor in the standalone package:

1. Implement the appropriate vendor interface under `packages/marketdata/src/marketdata/vendors/`.
2. Normalize responses into the shared dataclasses; do not expose provider-specific dictionaries to callers.
3. Use the package HTTP helper so timeouts, proxy behavior, throttling, retries, and metrics remain consistent.
4. Register the vendor in `VENDOR_CLASSES_BY_TYPE` in `packages/marketdata/src/marketdata/registry.py`.
5. If PanWatch should expose it in the data-source admin UI, add a conservative entry to `DATA_SOURCE_SEEDS` in `server.py`. Lower priority numbers run first; providers requiring credentials or a proxy should normally start disabled.
6. Add mocked parser and routing tests, including malformed/empty responses, supported markets, and failover behavior.

Document attribution, authentication requirements, rate limits, market coverage, symbol format, and known data-quality limitations. Avoid destructive seed reconciliation and never overwrite user credentials or priorities during startup.

## Frontend and internationalization

- Put user-visible text in `frontend/src/i18n/locales/zh-CN/` and `frontend/src/i18n/locales/en-US/` using semantic keys.
- Keep both locale shapes in sync and run `pnpm --dir frontend run check:i18n`.
- A locale changes interface and generated-report language; it must not implicitly change market, currency, timezone, stock symbols, or source excerpts.
- Use the existing format helpers for dates, numbers, percentages, currencies, and market labels.
- Prefer shared components from `frontend/packages/` over page-local duplicates.
- Render API failures from stable `error_code` values. Treat server error messages as diagnostics, not translation keys or control-flow contracts.
- Add Testing Library coverage for user-visible state changes and locale-sensitive behavior.

## Persistence and API changes

- Keep migrations and startup reconciliation backward compatible with existing self-hosted data.
- Prefer additive schema changes and explicit defaults. Do not silently delete or replace user configuration.
- Keep API error codes stable and document new request or response fields.
- Test upgrade paths, missing legacy fields, authorization boundaries, and repeated/idempotent requests where relevant.

## Documentation

- `README.md` is the default English project introduction.
- `README.zh-CN.md` is the complete Simplified Chinese version.
- Update both when installation, configuration, screenshots, or user-visible features change.
- Keep historical compatibility files such as `README.en.md` and `CONTRIBUTING.en.md` working when paths move.
- Do not present prototypes, local-only files, or unverified behavior as released functionality.

## Commit messages

Commit messages must be written in English and use Conventional Commits:

```text
<type>(<scope>): <subject>
```

Common types are `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `build`, and `ci`. Use the affected module as the scope, such as `assistant`, `marketdata`, `frontend`, or `i18n`. Keep the subject concise, imperative, and without a trailing period.

Examples:

```text
feat(automation): add a pre-market risk digest
fix(marketdata): handle empty quote volume
docs(readme): clarify Docker startup behavior
```

## Pull requests

Pull-request titles and descriptions must be written in English. Titles should also use Conventional Commits. The body must include:

1. **Background** — the problem and user impact.
2. **Changes** — implementation and behavior changes grouped by module.
3. **Validation** — exact commands that were run and their results.
4. **Boundaries and risks** — compatibility, untested paths, and known limitations.
5. **Follow-up** — only concrete work intentionally left for later.

Use a `codex/`-prefixed branch when changes are made through Codex unless a maintainer requests otherwise. The repository normally uses squash merge.

## Reporting bugs and security issues

For ordinary bugs, open an issue with reproduction steps, expected and actual behavior, version information, and sanitized logs. Remove tokens, cookies, account identifiers, positions, and other private financial data.

For a security-sensitive issue, do not publish exploit details or credentials in a public issue. Follow the private reporting instructions in [SECURITY.md](SECURITY.md).
