# PanAgent Runtime

<code>pan-agent-runtime</code> 是一个轻量、与业务无关的 Python Agent 执行内核，负责把
“模型输出工具调用”安全地变成“可观测、可暂停、可恢复的任务”。它不绑定 FastAPI、
数据库、具体模型供应商或任何业务领域，适合被 PanWatch、BeeCount-Cloud 以及其他项目
作为底层依赖复用。

> 当前版本：<code>0.1.0</code><br>
> Python：<code>>=3.10</code><br>
> 运行时依赖：<code>pydantic>=2.0</code>

## 为什么单独抽成 runtime

业务项目通常会同时遇到几类 Agent 需求：

- 模型需要调用宿主项目提供的查询、写入或外部服务工具；
- 写入类工具必须先经过用户确认，确认后还能从中断处继续；
- 前端需要实时看到 token、工具开始/结束和审批请求；
- 任务必须有步骤数、工具调用数、单次超时和总超时上限；
- 模型偶尔会重复提交相同工具调用，需要被及时熔断。

这些能力与“股票、记账、CRM”等业务无关，因此放在 runtime 中统一实现。业务项目
只负责提供模型适配器、工具实现、权限策略、事件传输和持久化。

## 设计边界

### runtime 负责

- provider-neutral 的消息、工具、权限、审批、checkpoint 和事件契约；
- 有界的串行 Agent loop；
- 工具可见性和每次调用的权限决策；
- Human-in-the-loop 暂停、部分审批和恢复；
- 工具超时、重试、总超时、最大步骤数和重复调用检测；
- 将执行过程转换为稳定的结构化事件。

### 宿主项目负责

- 调用 OpenAI-compatible、Anthropic 或本地模型的 <code>ModelPort</code> 适配器；
- 具体业务工具和工具结果；
- 用户、租户、角色和工具权限的持久化；
- 审批卡片展示、审批决定保存和 checkpoint 持久化；
- SSE、WebSocket、消息队列或其他 UI 传输；
- 数据库事务、缓存、限流和业务审计。

runtime 不会直接连接数据库，也不会替宿主决定“谁可以执行什么”。默认策略只允许
无须确认的读工具，写入和外部副作用必须由宿主显式注入策略。

## 安装

### 在 monorepo 中本地安装

从仓库根目录执行：

~~~bash
python -m pip install -e packages/pan-agent-runtime
~~~

PowerShell 也可以使用：

~~~powershell
python -m pip install -e .\packages\pan-agent-runtime
~~~

### 作为独立包安装

发布到包索引后，其他项目只需要：

~~~bash
python -m pip install pan-agent-runtime
~~~

包的 import 名称是 <code>pan_agent</code>，不是带连字符的发行包名：

~~~python
from pan_agent import AgentRuntime, ToolRegistry
~~~

## 5 分钟示例：运行一个只读 Agent

下面的示例使用一个假的模型适配器，展示 runtime 的最小接入面。真实项目只需要把
<code>DemoModel</code> 换成自己的模型 SDK 适配器。

~~~python
import asyncio
from datetime import UTC, datetime

from pan_agent import (
    AgentRuntime,
    ModelMessage,
    ModelTurn,
    ReadOnlyToolPolicy,
    RunRequest,
    RuntimeEvent,
    ToolCall,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolRisk,
)


class DemoModel:
    """真实项目中，这里负责把 ModelPort 调用转换成模型供应商协议。"""

    async def run_turn(self, messages, tools, emit_token):
        # 第一次返回工具调用；下一次直接返回最终答案。
        if not any(message.role == "tool" for message in messages):
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        name="lookup_balance",
                        arguments={"account_id": "demo"},
                    )
                ]
            )
        await emit_token("账户余额查询完成。")
        return ModelTurn(content="账户余额查询完成。")


class ConsoleSink:
    async def publish(self, event: RuntimeEvent):
        print(event.type.value, event.data)


async def lookup_balance(_request, arguments):
    return ToolResult.success(
        summary=f"账户 {arguments['account_id']} 余额为 100.00。",
        data={"account_id": arguments["account_id"], "balance": 100.0},
        sources=[{"name": "demo ledger"}],
        observed_at=datetime.now(UTC),
    )


async def main():
    tools = ToolRegistry()
    tools.register(
        ToolSpec(
            name="lookup_balance",
            title="查询余额",
            description="查询一个账户的当前余额。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["account_id"],
                "properties": {"account_id": {"type": "string"}},
            },
        ),
        lookup_balance,
    )

    request = RunRequest(
        run_id="demo-run-1",
        messages=[ModelMessage(role="user", content="查询我的余额")],
    )
    runtime = AgentRuntime(DemoModel(), tools, policy=ReadOnlyToolPolicy())
    result = await runtime.run(request, ConsoleSink())
    print(result.status, result.answer)


asyncio.run(main())
~~~

宿主提供的工具执行器必须是异步 callable，签名为：

~~~python
async def executor(request: RunRequest, arguments: dict) -> ToolResult:
    ...
~~~

<code>ToolResult.summary</code> 是给模型和 UI 的短摘要，<code>data</code> 才是模型后续
推理所需的结构化数据，<code>sources</code> 用于来源展示。建议摘要简洁、数据可序列化，
并避免把整张数据库表或完整响应原样塞进上下文。

## 运行生命周期

一次 <code>AgentRuntime.run()</code> 的流程如下：

~~~text
RunRequest
   │
   ├─ RUN_CREATED
   ├─ 模型回合（只看到 policy 允许暴露的工具）
   ├─ 工具权限决策
   │    ├─ ALLOW → TOOL_STARTED → TOOL_COMPLETED
   │    ├─ DENY  → 返回 permission_denied 工具结果，模型可以继续
   │    └─ ASK   → APPROVAL_REQUIRED + AgentCheckpoint，暂停
   ├─ ANSWER_TOKEN（模型适配器持续 emit）
   └─ RUN_COMPLETED / RUN_FAILED
~~~

模型返回多个工具调用时，runtime 会按原顺序处理。只要有一个调用需要审批，当前
任务就返回 <code>WAITING_FOR_APPROVAL</code>，尚未批准的调用不会执行。

## Human-in-the-loop 审批

### 默认只读策略

<code>ReadOnlyToolPolicy</code> 只暴露 <code>risk=read</code> 且
<code>confirmation_required=False</code> 的工具。它适合公开查询、离线分析或尚未接入
宿主权限系统的安全默认场景。

### 宿主自定义策略

需要写入或外部副作用时，宿主实现 <code>ToolPolicy</code>。策略至少包含两个方法：

~~~python
from pan_agent import ToolPermissionDecision, ToolRisk


class MyPolicy:
    def is_tool_visible(self, request, tool):
        # 决定模型本轮能发现哪些工具
        return True

    async def decide(self, request, tool, call):
        # 决定本次调用是 allow、ask 还是 deny
        if tool.risk is ToolRisk.READ and not tool.confirmation_required:
            return ToolPermissionDecision.allow()
        return ToolPermissionDecision.ask("该操作会修改数据，需要用户确认")
~~~

生产环境的 <code>decide</code> 通常会读取当前用户、租户和工具权限设置，并对写入、
删除、外部发送等操作返回 <code>ask</code>。不要让模型通过参数覆盖策略结果。

### 暂停与恢复

审批请求返回后，宿主应持久化 <code>RunResult.checkpoint</code>，并把其中的
<code>pending_approvals</code> 转成 UI 卡片。用户决定后调用 <code>resume</code>：

~~~python
from pan_agent import ApprovalDecision, RunStatus

paused = await runtime.run(request, sink)
if paused.status is RunStatus.WAITING_FOR_APPROVAL:
    checkpoint = paused.checkpoint
    # 可以只决定一张卡，剩余卡片会保留在新的 checkpoint 中。
    decisions = {
        checkpoint.pending_approvals[0].call_id: ApprovalDecision.APPROVED,
    }
    resumed = await runtime.resume(request, checkpoint, decisions, sink)
~~~

<code>resume</code> 不要求使用同一个 runtime 实例，因此宿主可以在进程重启后恢复。
建议将 checkpoint 以 JSON 形式持久化，并使用任务 ID、用户 ID 和版本号做并发校验，
避免同一张审批卡被重复消费。

## 核心公开 API

### Context Engineering 扩展

长会话的上下文控制已经作为 `pan_agent.context` 的通用能力提供，不依赖
PanWatch 的数据库、FastAPI 或模型厂商。它包括：

- `ContextBudget`：最大 token、soft/hard 阈值和最近消息窗口；
- `ContextUsage`：系统指令、历史消息、最近消息、页面上下文和摘要的分段用量；
- `ContextSummary`：目标、约束、决定、事实、当前状态、未完成事项和工具发现；
- `ContextEngine`：自动压缩和 `force_compress=True` 主动压缩；
- `ContextSummarizer`：宿主接入任意摘要模型的协议；
- `ExtractiveContextSummarizer`：模型不可用时的确定性 fallback。

示例：

~~~python
from pan_agent import ContextBudget, ContextEngine

result = await ContextEngine(my_summarizer).prepare(
    messages,
    budget=ContextBudget(
        max_tokens=12_000,
        soft_limit_tokens=8_400,
        hard_limit_tokens=10_200,
        keep_recent_messages=8,
        summary_max_tokens=800,
    ),
)
next_request_messages = result.messages
~~~

runtime 只负责这组 provider-neutral contracts。摘要模型选择、snapshot
持久化、HTTP/SSE 和 UI 都由宿主项目注入。更完整的边界说明见
[`docs/architecture.md`](docs/architecture.md) 和
[`docs/context.md`](docs/context.md)。

| 类型 | 用途 |
| --- | --- |
| <code>AgentRuntime</code> | 启动、暂停和恢复有界 Agent loop |
| <code>ToolRegistry</code> | 注册工具、按策略暴露工具、执行工具 |
| <code>ToolSpec</code> | 工具名称、描述、风险等级和 JSON Schema |
| <code>ToolResult</code> | 工具成功/失败、摘要、结构化数据和来源 |
| <code>ToolPolicy</code> | 宿主定义工具可见性和每次调用权限 |
| <code>ReadOnlyToolPolicy</code> | 安全默认策略，只允许无确认读工具 |
| <code>ModelPort</code> | 宿主接入模型供应商的异步协议 |
| <code>EventSink</code> | 宿主接收结构化运行事件的异步协议 |
| <code>RunRequest</code> | 一次运行的消息、上下文和限制 |
| <code>RunLimits</code> | 步骤数、工具调用数、超时和重试上限 |
| <code>AgentCheckpoint</code> | 审批暂停后可持久化的恢复状态 |
| <code>RunResult</code> | 运行状态、答案、错误码和 checkpoint |
| <code>RuntimeEvent</code> | SSE/WebSocket 等传输使用的统一事件 |

### 风险与权限

<code>ToolRisk</code> 当前包含：

- <code>read</code>：读取数据，不产生业务副作用；
- <code>write</code>：创建或修改数据，通常应询问用户；
- <code>external</code>：发送消息、调用外部系统等副作用；
- <code>destructive</code>：删除或不可逆操作，建议在宿主策略中默认拒绝或强制二次确认。

<code>PermissionMode</code> 的三个结果是 <code>allow</code>、<code>ask</code>、<code>deny</code>。
风险等级只是工具声明，最终决定权始终在宿主的 <code>ToolPolicy</code>。

### 运行限制

<code>RunLimits</code> 默认值如下：

| 限制 | 默认值 | 可配置范围 |
| --- | ---: | ---: |
| <code>max_steps</code> | 6 | 1–32 |
| <code>max_tool_calls</code> | 8 | 1–64 |
| <code>tool_timeout_seconds</code> | 20 | 1–120 |
| <code>run_timeout_seconds</code> | 90 | 1–600 |
| <code>step_retry_count</code> | 1 | 0–3 |

runtime 还会检测连续重复的相同工具调用。达到阈值后返回
<code>repeated_tool_call</code>，防止模型在错误参数上无限循环。

## 事件与流式输出

<code>RuntimeEvent.type</code> 可能是：

| 事件 | 典型用途 |
| --- | --- |
| <code>run_created</code> | 创建前端任务状态 |
| <code>plan_created</code> | 预留给宿主展示计划 |
| <code>step_updated</code> | 显示当前 Agent 步骤 |
| <code>tool_started</code> | 显示工具开始执行 |
| <code>tool_completed</code> | 显示工具结果摘要和错误码 |
| <code>answer_token</code> | 增量渲染模型答案 |
| <code>approval_required</code> | 创建一张或多张审批卡 |
| <code>run_completed</code> | 任务成功结束 |
| <code>run_failed</code> | 任务以错误或部分结果结束 |

runtime 本身不实现 SSE。FastAPI 宿主可以在 <code>EventSink.publish()</code> 中把事件
写入 <code>asyncio.Queue</code>，接口层再将队列转换成 <code>text/event-stream</code>。
这样浏览器传输格式和 runtime 的执行逻辑保持解耦。

## BeeCount-Cloud 接入建议

推荐把接入分成五层：

1. **模型适配层**：在 <code>ModelPort.run_turn()</code> 中把 BeeCount-Cloud 当前模型
   客户端的流式 token、tool call 和 finish reason 转成 <code>ModelTurn</code>。
2. **业务工具层**：在 Cloud 自己的模块中注册记账、账户、报表等工具；工具实现只
   依赖 Cloud 的 service/repository，不进入 <code>pan-agent-runtime</code>。
3. **权限策略层**：实现 <code>ToolPolicy</code>，根据用户、租户、角色和工具设置返回
   <code>allow/ask/deny</code>。写入、删除和外部通知建议默认 <code>ask</code>。
4. **持久化层**：把 <code>RunRequest</code>、<code>RuntimeEvent</code>、<code>RunResult</code>
   和 <code>AgentCheckpoint</code> 映射到 Cloud 自己的任务/消息/审批表。
5. **传输层**：将 <code>EventSink</code> 接到现有 SSE 或 WebSocket 通道；前端只消费
   统一事件，不需要知道底层模型供应商。

示意目录：

~~~text
beecount-cloud/
├─ src/modules/assistant/
│  ├─ model_adapter.py       # ModelPort
│  ├─ policy.py              # ToolPolicy + 用户权限
│  ├─ tools.py               # Cloud 业务工具注册
│  ├─ event_sink.py          # SSE/WebSocket 事件桥接
│  └─ repository.py          # checkpoint / approval 持久化
└─ pyproject.toml            # 依赖 pan-agent-runtime
~~~

不要把 FastAPI <code>Request</code>、SQLAlchemy <code>Session</code>、Cloud 的模型类或
业务异常传进 runtime 包的公共接口。这样未来换数据库、换模型供应商或把 runtime 发布
到其他项目时，不会形成反向耦合。

## 工具变多后的上下文控制

runtime 会在每一轮调用 <code>ToolRegistry.model_tools(request, policy)</code>，因此
默认行为是“把策略允许的工具定义交给模型”。工具数量少时最直观；工具增长后，工具
定义和工具结果都会成为上下文成本。

建议按以下顺序优化：

### 1. 按能力域动态暴露工具

在 <code>ToolPolicy.is_tool_visible()</code> 中根据当前请求上下文只暴露相关工具，例如：

- 用户问余额，只暴露账户和持仓查询；
- 用户问账单，只暴露交易和分类工具；
- 用户要求修改数据，再临时暴露对应写工具。

更大规模的系统可以只暴露一个“工具目录/搜索工具”，模型先检索能力，再由宿主把
命中的工具加入后续回合。

### 2. 工具结果摘要化

<code>ToolResult.summary</code> 用于快速理解，<code>data</code> 只保留后续推理需要的字段。
列表查询返回 ID、名称和关键状态，详情通过下一次工具调用按 ID 查询。不要每次把完整
行情、完整日志或整张账单表复制到对话历史。

### 3. 历史消息分层

- 保留最近几轮原始消息和工具结果；
- 将更早的对话压缩成稳定摘要；
- 将可复用事实放入宿主的结构化 context 或外部存储，按需检索；
- 对单次任务设置最大输入长度。

### 4. 缓存和重复调用保护

对相同用户、标的、时间窗口和参数的只读查询做短 TTL 缓存；在工具层或宿主层去重
并发请求。runtime 自带连续相同 tool call 熔断，宿主还可以在 <code>ToolPolicy</code>
或工具适配层增加更细的幂等键。

### 5. 预算和可观测性

根据任务类型设置 <code>RunLimits</code>，记录每步 token、工具耗时、重试次数和错误码。
发现工具调用异常增长时，优先检查模型提示、工具描述和权限策略，而不是简单无限增大
<code>max_steps</code>。

## 错误处理与状态

工具异常会在 runtime 边界被转换为稳定的 <code>ToolResult</code>/<code>RunResult</code>，
不会把数据库堆栈直接发送给模型或浏览器。常见终态包括：

| 状态 | 含义 |
| --- | --- |
| <code>completed</code> | 模型返回最终答案 |
| <code>waiting_for_approval</code> | 有待处理的审批卡 |
| <code>partial</code> | 达到步骤/工具/超时上限，或工具失败 |
| <code>failed</code> | runtime 边界发生未预期错误 |
| <code>cancelled</code> | 宿主取消了任务 |
| <code>pending</code> / <code>running</code> | 宿主持久化或展示中的中间状态 |

对外接口应优先使用 <code>RunResult.error_code</code> 做机器判断，再用事件中的
<code>summary</code> 做人类可读提示。不要依赖异常文本作为前端协议。

## 测试与本地开发

在仓库根目录运行 runtime 测试：

~~~bash
python -m pytest packages/pan-agent-runtime/tests -q
~~~

建议宿主项目至少覆盖：

- 只读工具能被暴露并执行；
- 写工具会生成审批而不是直接执行；
- 拒绝审批不会产生业务写入；
- 部分审批只执行已决定的调用；
- checkpoint 序列化后可以在新进程恢复；
- 工具超时、重试、重复调用和未知工具会得到预期错误码；
- 事件顺序与前端流式协议一致。

## 独立发布检查清单

发布新版本前建议确认：

1. 更新 <code>pyproject.toml</code> 中的 <code>version</code>；
2. 检查 <code>README.md</code> 示例与公共 API 一致；
3. 运行 <code>python -m pytest packages/pan-agent-runtime/tests -q</code>；
4. 构建 wheel 和 source distribution：

   ~~~bash
   python -m pip install build
   python -m build packages/pan-agent-runtime
   ~~~

5. 在干净虚拟环境安装生成的 wheel 并运行最小示例；
6. 通过 PyPI Trusted Publishing 或项目约定的发布流水线上传；
7. 为破坏性 API 变更升级主版本或明确记录迁移说明。

## 版本兼容原则

<code>0.x</code> 阶段允许在小版本中调整尚未稳定的细节，但应尽量保持以下边界稳定：

- <code>ToolSpec</code>、<code>ToolResult</code>、<code>RunRequest</code>、<code>RunResult</code>
  的字段语义；
- <code>ModelPort</code>、<code>ToolPolicy</code>、<code>EventSink</code> 的异步调用约定；
- <code>RuntimeEvent</code> 的事件类型和核心字段；
- checkpoint 能否被同版本宿主恢复。

业务项目不应依赖 <code>pan_agent.runtime</code> 内部私有函数或未导出的实现细节，只从
<code>pan_agent</code> 顶层导入公共 API。
