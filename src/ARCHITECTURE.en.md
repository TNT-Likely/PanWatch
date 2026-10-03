# PanWatch Backend Architecture

[简体中文](ARCHITECTURE.md)

## Purpose and shape

PanWatch is a **modular monolith**: one FastAPI application and one shared
database, organized around stable business boundaries. The goal is not to turn
every domain into a microservice. It is to make ownership, dependency direction,
and test boundaries explicit so the codebase does not collapse into an
unbounded `core` directory.

```text
src/
├── bootstrap/       # application startup and dependency wiring
├── platform/        # technology capabilities with no product decisions
├── modules/         # product business capabilities
└── web/             # reusable HTTP middleware and response adapters
```

`collectors/`, `models/`, and `compat/` at the source root were deliberately
retired. Market-data collectors live in `platform/marketdata/collectors/`, and
shared quote value objects live in `platform/marketdata/models.py`. Do not
reintroduce those root-level directories.

## Dependency direction

```text
web ───────────────► modules ───────────────► platform
 │                    │                         │
 │                    └─ public service / DTO ──┘
 └────────────────────────► platform
```

- `platform` must not import `modules` or make product or investment decisions.
- `modules` may use `platform`, but one module must not import another module's
  ORM models or repositories directly.
- A module must not import another module's `api/` router or `*_api.py` file.
  Cross-module HTTP code must call the owning module's public service, DTO, or
  supported tool boundary instead.
- Cross-module collaboration happens through an owning module's service, DTO,
  or domain event.
- `web` maps HTTP input and output only. It must not contain complex SQL,
  agent tool loops, or strategy decisions.
- `bootstrap` wires startup dependencies only; it does not implement business
  flows.

`tests/test_architecture_boundaries.py` guards these rules. Update both the
test and this document whenever an intentional boundary changes.

## `platform/`: technical capabilities

Platform code describes *how* PanWatch connects, stores, schedules, or runs;
it does not decide *what* a user should do.

| Area | Owns | Does not own |
| --- | --- | --- |
| `persistence/` | engine, sessions, ORM base, tables, migrations | portfolio or strategy decisions |
| `ai/` | provider clients, failover, transport adapters | prompts or tool authorization |
| `marketdata/` | external quote clients, collectors, value objects, code normalization, provider routing, data normalization | alert thresholds or stock-selection rules |
| `events/` | transport such as SSE | business meaning of events |
| `scheduling/` | cron parsing, trading calendar, registries | agent scheduling workflow |
| `notifications/` | channel delivery, baseline de-duplication and policy | which business event should notify |
| `observability/` | log context, traces, metric export | domain-metric interpretation |
| `runtime/` | process configuration, environment, cross-cutting runtime settings | product rules |

## `modules/`: business capabilities

Each first-level directory owns a product capability. A module may use the
following structure when it makes the design clearer; empty layers are not a
goal.

```text
<module>/
├── api.py          # optional module-specific router
├── service.py      # use-case orchestration and preferred public boundary
├── repository.py   # optional persistence queries and writes
├── models.py       # optional domain or ORM-model references
├── schemas.py      # optional DTOs, commands, and response models
└── ...             # domain-specific implementation
```

Other modules should call a service instead of bypassing it through a
repository import.

| Module | Owns | Typical public boundary |
| --- | --- | --- |
| `assistant` | conversations, task snapshots, PanAgent host adapter, approved tools | `AssistantService` |
| `automation` | scheduled analysis agents, run records, TradingAgents, `AgentScheduler` | agent service / scheduler |
| `market` | symbols, collection orchestration, news, candlestick context, price alerts | market and alert services |
| `portfolio` | accounts, positions, diagnosis, benchmark performance | `PortfolioService` |
| `research` | analysis history, context, evidence, evaluation results, signals | research and context services |
| `strategy` | factors, signals, candidate stocks, calibration, backtests | strategy service |
| `paper_trading` | simulated execution, ledger, allocation, notifications | paper-trading service |
| `reporting` | report and PDF rendering | render and export functions |
| `administration` | health checks, settings, PATs, upgrade checks | administration service |

## Application and HTTP boundaries

`bootstrap/application.py` is the only place that creates the FastAPI
application and registers routers. It may depend on the public HTTP entry
points from `web`, `modules`, and `platform`, but it must not contain domain
rules, SQL, or agent loops.

Business routers live with their modules rather than in a new centralized
`web/api` directory. A router should only:

1. validate HTTP input and create a command or DTO;
2. obtain the module service;
3. map a service result to an HTTP response, SSE stream, or stable error code.

Database models and migrations live in `platform/persistence/`. Do not restore
`src/web/api/`, `src/web/app.py`, `src/web/database.py`, `src/web/models.py`, or
`src/web/migrations.py`.

| Module | HTTP router location | API scope |
| --- | --- | --- |
| `administration` | `modules/administration/api/` | authentication, settings, health, logs, data sources, PAT, MCP |
| `assistant` | `modules/assistant/api.py` | navigation assistant, persistent tasks, runtime extensions; shared adapters live in `tool_adapters.py` |
| `automation` | `modules/automation/api/` | agents, recommendation pool, templates |
| `market` | `modules/market/api/` | symbols, quotes, candlesticks, news, discovery, price alerts |
| `portfolio` | `modules/portfolio/api/` | accounts, position history, dashboard |
| `research` | `modules/research/api/` | context, insights, evaluation, feedback, suggestions |
| `strategy` | `modules/strategy/api/` | factor endpoints |
| `paper_trading` | `modules/paper_trading/api/` | simulated-trading endpoints |

### API errors and localization

Use `src.web.errors.api_error(status, code, chinese_message)` for intentional
HTTP failures. The response wrapper exposes the stable `code` as `error_code`
for `/api` responses while preserving the Chinese message for the Chinese UI
and diagnostics. The client translates the code for the English UI instead of
matching user-facing prose. This keeps the error contract stable across UI
languages and prevents upstream exception details from becoming client API
contracts.

## Key flows

### Navigation assistant

```text
/assistant UI
  → /api/assistant router
  → AssistantService
  → AgentRuntime (packages/pan-agent-runtime)
  → ModelPort + approved ToolRegistry
  → runtime events → task persistence + SSE → UI
```

The `pan-agent-runtime` import name is `pan_agent`. It defines the constrained
execution loop, resource limits, and portable events. It must not import
FastAPI, SQLAlchemy, `src.*`, or LangChain. PanWatch adapters, tools, and task
persistence belong to `modules/assistant`.

### Cross-module collaboration

If `strategy` needs a portfolio summary, it should not import
`portfolio.repository` or `portfolio.models`. `portfolio` exposes a dedicated
service or DTO. Work that is asynchronous, deferrable, or owned by multiple
domains should publish a domain event; the subscribing module owns its own
handling.

### TradingAgents deep analysis

The TradingAgents integration prepares PanWatch market and portfolio context,
checks the cache, invokes the upstream multi-agent graph, then
maps its decision to PanWatch recommendations and optional paper-trading
signals. The full user-facing flow is documented in
[the English flowchart](../docs/tradingagents-flow.en.md) and
[the Simplified Chinese flowchart](../docs/tradingagents-flow.md).

## Persistence and migrations

All SQLAlchemy tables are registered in `platform/persistence/models.py`; the
engine, `Base`, session, and `get_db` live in `database.py`; migrations live in
`migrations.py`.

When adding a schema change: define behavior and tests in the owning module,
register the table, add a repeatable new migration, and never modify a released
migration. Routers must not perform schema changes.

## Placement guide

| Need | Location |
| --- | --- |
| New AI, market-data, or notification provider | matching `platform/*` adapter |
| New investment, analysis, or user workflow | owning `modules/<domain>` service |
| New API | owning module's `api/`; use a single `api.py` only when the module genuinely has one router |
| New background task | business execution in its module; cron and calendar primitives from `platform/scheduling` |
| New ORM table or migration | `platform/persistence`, used by its owning module service |
| Helper with business meaning | owning module; do not create a new root-level `core` |

## Prohibited designs

- Do not restore `src/core`, `src/agents`, `src/web/api`, or the retired `web`
  persistence files.
- Do not let `platform` import `modules`.
- Do not cross-import `models.py` or `repository.py` between modules.
- Do not put business rules, SQL, or tool loops in an HTTP router.
- Do not let `pan_agent` depend on PanWatch, the database, or a concrete AI SDK.
- Do not create an ownerless root-level module in the name of a generic helper.

These rules are not about adding layers. They make ownership, dependencies, and
the path for future changes visible before the codebase grows further.
