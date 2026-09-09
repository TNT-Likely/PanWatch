# PanWatch 后端架构

## 目标与形态

PanWatch 是一个**模块化单体**：一个 FastAPI 应用、一个共享数据库，但以稳定的
业务边界组织代码。目标不是为每个领域拆微服务，而是让每段业务代码都有明确
的所有者、依赖方向和测试边界，避免重新形成无边界的 `core` 目录。

```text
src/
├── bootstrap/       # 应用启动与依赖装配
├── platform/        # 与业务无关的技术能力
├── modules/         # 产品业务能力
└── web/             # FastAPI HTTP 边界与应用组装
```

`collectors/`、`models/` 和 `compat/` 是仍待收口的历史目录：采集器将迁至
`platform/marketdata/collectors/`，市场领域模型将迁至
`modules/market/models.py`，`compat/` 没有调用者后必须删除。新代码不得放入
这些目录。

## 依赖方向

```text
web ───────────────► modules ───────────────► platform
 │                    │                         │
 │                    └─ public service / DTO ──┘
 └────────────────────────► platform
```

- `platform` 不得导入 `modules`，也不做产品或投资决策。
- `modules` 可使用 `platform`，但不能直接导入另一个模块的 ORM models 或 repository。
- 跨模块协作必须经过拥有模块公开的 service、DTO 或 event。
- `web` 只做 HTTP 输入输出映射与服务装配，不承载复杂 SQL、Agent 工具循环或策略判断。
- `bootstrap` 只负责启动期装配，不实现业务流程。

`tests/test_architecture_boundaries.py` 负责守卫这些规则。修改模块边界时，应同步
更新架构测试与本文档。

## `platform/`：技术平台

平台代码描述“怎样连接或执行”，不描述“用户应做什么”。

| 子目录 | 责任 | 不应包含 |
| --- | --- | --- |
| `persistence/` | engine、Session、ORM Base、全部表定义、版本迁移 | 持仓、策略等业务判断 |
| `ai/` | AI provider client、failover、模型传输适配 | 提示词、工具授权 |
| `marketdata/` | 外部行情客户端、供应商路由、数据归一化 | 告警阈值、选股规则 |
| `events/` | SSE 等事件传输 | 事件的业务含义 |
| `scheduling/` | cron 解析、交易日历、注册表 | Agent 调度流程 |
| `notifications/` | 通道发送、基础去重和策略 | 哪种业务事件应通知 |
| `observability/` | 日志上下文、trace、指标导出 | 领域指标推导 |

## `modules/`：业务能力

一个一级目录拥有一个产品能力。推荐但非强制的内部结构：

```text
<module>/
├── api.py          # 可选：模块专属 router
├── service.py      # 用例编排，也是优先的公开边界
├── repository.py   # 可选：持久化查询与写入
├── models.py       # 可选：模块使用的领域/ORM 模型引用
├── schemas.py      # 可选：DTO、命令与响应模型
└── ...             # 领域专属实现
```

不要为了形式创建空层。其他模块应调用 service，而不是绕过它导入 repository。

| 模块 | 拥有的能力 | 典型公开边界 |
| --- | --- | --- |
| `assistant` | 对话、任务快照、PanAgent host adapter、已批准工具 | `AssistantService` |
| `automation` | 定时分析 Agent、运行记录、TradingAgents、AgentScheduler | agent service / scheduler |
| `market` | 标的、采集编排、新闻、K 线上下文、价格告警 | market/alert service |
| `portfolio` | 账户、仓位、诊断、业绩基准 | `PortfolioService` |
| `research` | 分析历史、上下文、证据、结果评估、signals | research/context service |
| `strategy` | 因子、信号、候选标的、校准、backtest | strategy service |
| `paper_trading` | 模拟盘执行、账本、分配、通知 | paper-trading service |
| `reporting` | 报告与 PDF 等产物渲染 | render/export function |
| `administration` | 健康检查、设置维护、PAT、升级检查 | administration service |

## `web/`：HTTP 边界

`web/app.py` 创建 FastAPI 应用并注册 router。每个 router 仅负责：

1. 校验 HTTP 输入并创建 command/DTO；
2. 获取模块 service；
3. 将 service 结果映射为 HTTP response、SSE 或错误码。

数据库、ORM models 与 migrations 均在 `platform/persistence/`。禁止恢复
`src/web/database.py`、`models.py` 或 `migrations.py`。

## 关键流程

### 导航级助手

```text
/assistant 页面
  → /api/assistant router
  → AssistantService
  → AgentRuntime (packages/pan-agent-runtime)
  → ModelPort + 已批准 ToolRegistry
  → runtime events → task persistence + SSE → UI
```

`pan-agent-runtime` 的导入名为 `pan_agent`。它只定义受限执行循环、资源限制和
可移植事件，不能导入 FastAPI、SQLAlchemy、`src.*` 或 LangChain。PanWatch 的
适配器、工具、持久化都属于 `modules/assistant`。

### 跨模块调用

若 `strategy` 需要持仓摘要，不应导入 `portfolio.repository` 或
`portfolio.models`；应由 `portfolio` 暴露专门 service/DTO。异步、可延迟或涉及
多个所有者的工作应发布领域 event，由订阅模块自行处理。

## 持久化与迁移

全部 SQLAlchemy 表注册在 `platform/persistence/models.py`；engine、`Base`、
Session 与 `get_db` 在 `database.py`；版本迁移在 `migrations.py`。

新增 schema 时：先在所属模块确定行为和测试；再注册表、添加可重复执行的新
版本迁移；不要修改已发布迁移，也不要在 router 中执行 schema 变更。

## 新代码放置指南

| 需求 | 放置位置 |
| --- | --- |
| 新 AI、行情或通知供应商 | 对应 `platform/*` adapter |
| 新投资、分析或用户工作流 | 对应 `modules/<domain>` service |
| 新 API | 优先模块自己的 `api.py`；旧 `web/api` 仅作过渡 |
| 新后台任务 | 业务执行在模块，cron/日历使用 `platform/scheduling` |
| 新 ORM 表或迁移 | `platform/persistence`，由所属模块 service 使用 |
| 带业务含义的 helper | 其所属 module；不得创建新的 `core` |

## 禁止项

- 不恢复 `src/core`、`src/agents` 或旧 `src/web` 持久化文件；
- 不让 `platform` 导入 `modules`；
- 不跨模块导入 `models.py`、`repository.py`；
- 不把业务规则、SQL 或工具循环塞进 HTTP router；
- 不让 `pan_agent` 依赖 PanWatch、数据库或具体 AI SDK；
- 不以“通用 helper”为名创建没有所有者的根目录模块。

这些规则不是为了增加层数，而是让每段代码的归属、依赖和演进方式都清晰可见。
