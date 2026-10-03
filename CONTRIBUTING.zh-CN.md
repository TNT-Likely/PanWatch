# 为 PanWatch 做贡献

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

感谢你帮助改进 PanWatch。本文说明当前仓库结构、开发流程、扩展点和 Pull Request 要求。

## 开始之前

- 开始开发前先搜索已有 Issue 和 Pull Request，避免重复工作。
- 大型功能、架构调整、新依赖或破坏性行为请先开 Issue，说明用户问题和计划边界。
- 不要提交 API Key、Token、Cookie、个人持仓、本地数据库、生成报告或包含隐私数据的日志。
- 保持 PR 聚焦；与功能无关的清理请单独提交。

## 仓库结构

| 路径 | 职责 |
|---|---|
| `src/modules/` | 产品模块及其 API、服务和业务流程 |
| `src/modules/automation/` | 定时/按需 Agent、目录、调度器和 TradingAgents 集成 |
| `src/platform/` | AI、持久化、行情、通知、可观测和运行时基础设施 |
| `packages/marketdata/` | 支持数据源故障转移的独立强类型行情包 |
| `packages/pan-agent-*` | 可复用的 Agent 运行时、计量和工具检索包 |
| `frontend/src/` | React 应用、页面、组件、Hook 和多语言资源 |
| `frontend/packages/` | 前端共享 API、业务 UI 和基础 UI 包 |
| `prompts/` | 分析流程使用的 Prompt 模板 |
| `tests/` | 后端、集成、架构和评测测试 |
| `frontend/tests/` | Vitest 与 Testing Library 测试 |
| `docs/` | 对外文档、图表、截图和捐赠资源 |

请遵循模块归属：产品模块可以依赖平台服务，独立包不得反向导入 PanWatch 应用或数据库。仓库中的架构测试会检查重要边界。

## 开发环境

### 环境要求

- Python 3.10 或更高版本；Docker 运行时使用 Python 3.11。
- Node.js 24.14.0。
- pnpm 9.15.9。

### 推荐命令

```bash
# 终端 1：创建 .venv、安装后端依赖并启动 :8000
make dev-api

# 终端 2：安装前端依赖并启动 :5183
make dev-web
```

前端开发服务器会将 `/api` 代理到 `127.0.0.1:8000`。

也可以手动启动：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py

cd frontend
pnpm install
pnpm dev
```

需要本地配置时，将 `.env.example` 复制为 `.env`。只使用可丢弃的测试凭据，并确保 `.env` 不被提交。

安装仓库提供的 pre-push 测试钩子：

```bash
make install-hooks
```

## 开发流程

1. 从最新默认分支创建开发分支。
2. 在实现前或实现过程中补充聚焦测试。
3. 单元测试必须模拟网络调用，不得依赖付费 API 或真实发送通知。
4. 用户可见行为变化时，同步更新中英文资源和公开文档。
5. 运行与改动范围匹配的检查，最后执行 `git diff --check`。
6. 按下文格式创建 Pull Request。

## 验证命令

迭代时先运行最小聚焦测试，创建 PR 前再运行相关测试集。

```bash
# 后端完整测试
.venv/bin/python -m pytest -q

# 前端测试、多语言门禁、类型检查与生产构建
pnpm --dir frontend exec vitest run
pnpm --dir frontend run check:i18n
pnpm --dir frontend run check:ui
pnpm --dir frontend run build

# 空白符和冲突标记检查
git diff --check
```

修改独立包时，还应运行对应测试，例如：

```bash
.venv/bin/python -m pytest packages/marketdata/tests -q
.venv/bin/python -m pytest packages/pan-agent-runtime/tests -q
```

没有实际执行的检查，不要在 PR 中写成“已通过”。如果环境限制导致必要检查无法运行，请在 PR 中明确说明。

## 新增或修改 Agent

新增前先判断 Agent 类型：

- **workflow agent**：用户可见并可参与调度；
- **capability agent**：由其他流程或 UI 动作调用，不独立调度。

使用以下扩展点：

1. 在 `src/modules/automation/` 实现 Agent，通常继承 `BaseAgent`，并将 `collect()` 与 `build_prompt()` 分离。
2. 通过 `AgentContext` 使用 AI、持仓、自选、通知和报告语言状态，不要另建平行的 AI 客户端或通知链路。
3. 较长的 Prompt 放入 `prompts/`。增加语言指令时必须保留既有机器可读输出结构。
4. 在 `src/modules/automation/agent_catalog.py` 的 `AGENT_SEED_SPECS` 增加用户可见定义，明确 kind、可见性、调度、执行模式和安全默认值。
5. 可直接执行的 Agent 还需将实现类加入 `server.py` 的 `AGENT_REGISTRY`。
6. 在 UI 中展示时，为前端中英文资源的 `agentsPage.catalog` 增加目录文案。
7. 根据实际行为补充采集、解析、通知策略、调度、幂等、失败状态和英文报告测试。

优先复用现有调度、持久化、Trace、成本计量、通知去重和故障转移能力。新流程必须落入可观测的成功或失败终态，不得静默吞掉异常。

## 新增或修改行情数据源

先选择正确层级：

- 强类型报价、K 线、基本面、资金流、事件或发现类 vendor 放在 `packages/marketdata/`。
- PanWatch 特有的编排、缓存、截图或适配逻辑放在 `src/platform/marketdata/collectors/`。

在独立行情包中新增 vendor 时：

1. 在 `packages/marketdata/src/marketdata/vendors/` 实现对应 vendor 接口。
2. 将响应归一化为共享 dataclass，不要向调用方暴露服务商私有字典结构。
3. 使用包内 HTTP helper，统一超时、代理、节流、重试和指标行为。
4. 在 `packages/marketdata/src/marketdata/registry.py` 的 `VENDOR_CLASSES_BY_TYPE` 注册 vendor。
5. 如果需要在 PanWatch 数据源管理页展示，在 `server.py` 的 `DATA_SOURCE_SEEDS` 增加保守的默认项。优先级数字越小越先执行；需要凭据或代理的服务商通常应默认关闭。
6. 增加模拟网络的解析与路由测试，覆盖异常/空响应、市场范围和故障转移。

文档需要说明来源归属、认证方式、限流、市场覆盖、代码格式和已知数据质量限制。种子对账不得破坏用户数据，启动时不能覆盖用户凭据或优先级。

## 前端与国际化

- 用户可见文案放在 `frontend/src/i18n/locales/zh-CN/` 与 `frontend/src/i18n/locales/en-US/`，使用语义化 key。
- 保持两种语言资源结构一致，并运行 `pnpm --dir frontend run check:i18n`。
- 语言只影响界面和生成报告，不应隐式改变市场、币种、时区、股票代码或原始来源摘录。
- 日期、数字、百分比、货币和市场名称使用现有格式化函数。
- 优先复用 `frontend/packages/` 中的共享组件，避免页面内重复实现。
- 通过稳定的 `error_code` 展示 API 错误；服务端错误消息只是诊断信息，不能作为翻译 key 或控制流契约。
- 用户可见状态变化和语言相关行为需要 Testing Library 测试。

## 持久化与 API 变更

- 数据迁移和启动对账必须兼容已有自托管数据。
- 优先使用增量字段和显式默认值，不得静默删除或替换用户配置。
- 保持 API 错误码稳定，并记录新增请求/响应字段。
- 根据改动覆盖升级路径、旧字段缺失、权限边界及重复/幂等请求。

## 文档

- `README.md` 是默认英文项目介绍。
- `README.zh-CN.md` 是完整简体中文版本。
- 安装、配置、截图或用户功能变化时同步更新两份文档。
- 路径迁移时保持 `README.en.md`、`CONTRIBUTING.en.md` 等历史兼容入口可用。
- 不要把原型、本地文件或未经验证的行为描述成已发布能力。

## 提交信息

提交信息统一使用英文，并采用 Conventional Commits：

```text
<type>(<scope>): <subject>
```

常用 type 包括 `feat`、`fix`、`refactor`、`perf`、`test`、`docs`、`chore`、`build` 和 `ci`。scope 使用受影响模块，例如 `assistant`、`marketdata`、`frontend` 或 `i18n`。subject 使用简洁的英文祈使句，不以句号结尾。

示例：

```text
feat(automation): add a pre-market risk digest
fix(marketdata): handle empty quote volume
docs(readme): clarify Docker startup behavior
```

## Pull Request

PR 标题和正文统一使用英文，标题同样采用 Conventional Commits。正文至少包含：

1. **Background**：问题和用户影响。
2. **Changes**：按模块说明实现与行为变化。
3. **Validation**：实际执行的命令及结果。
4. **Boundaries and risks**：兼容性、未测试路径和已知限制。
5. **Follow-up**：只记录明确留到后续处理的工作。

通过 Codex 开发时，除非维护者另有要求，使用 `codex/` 前缀分支。仓库默认采用 squash merge。

## 反馈问题与安全问题

普通 Bug 请提交 Issue，包含复现步骤、预期/实际行为、版本信息和脱敏日志。务必移除 Token、Cookie、账户标识、持仓等金融隐私数据。

安全敏感问题不要在公开 Issue 中发布利用细节或凭据；请遵循 [SECURITY.md](SECURITY.md) 中的私密报告方式。

## 统一 UI 约定

修改控件或滚动面板前阅读 [UI 规范](frontend/UI_GUIDELINES.zh-CN.md)（[English](frontend/UI_GUIDELINES.md)）。运行 `pnpm --dir frontend check:ui` 拦截原生选择框、浏览器弹窗和漏用样式的滚动区域，并验证桌面 / 手机、亮色 / 深色效果。

## 交易日历覆盖

公布的年度休市日和半日市保存在 `src/platform/scheduling/exchange_calendar_data.py`（目前为 2026 年）。运行时只预热过去 30 天和未来 90 天，不请求历史日历。未公布年份的工作日标记未知，不能授权自动执行。跨年前应根据交易所公告补充下一年度数据，并验证市场当地日期、夏令时和半日市。已配置的 Agent Cron / 间隔保持不变，执行与预览共用交易日门禁。
