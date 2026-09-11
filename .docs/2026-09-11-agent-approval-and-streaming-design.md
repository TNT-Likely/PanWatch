# PanAgent 授权中断与真实事件流设计

> 状态：设计已确认，等待文档评审后实施  
> 日期：2026-09-11  
> 范围：移除助手伪进度文案、修复对话流自动滚动，并为 `pan-agent-runtime` 增加可暂停、可恢复、与业务无关的 Human-in-the-loop 授权能力。

## 1. 背景与问题

当前 `/assistant` 的 SSE 端点将运行时内部事件映射为三条硬编码的中文文案：`正在理解你的问题`、`正在准备回答`、`正在生成回答`。这些文案不是模型推理、工具执行或任务状态的事实；尤其 `STEP_UPDATED` 会在每轮工具调用后重复映射为“正在生成回答”，造成协议噪声和误导性的 UI。

对话组件还将每一批 token 的末尾元素用 `smooth` 方式滚动。浏览器的程序化平滑滚动会触发与用户滚动相同的监听器；内容持续增长时，监听器会误判用户已经离开底部，进而永久关闭自动滚动。组件没有“回到底部”控制，用户在长回答中只能手动拖回末尾。

运行时虽已有 `ToolRisk` 与 `confirmation_required` 元数据，但注册表以“只读运行时”为目标，直接拒绝所有 write 或确认工具。它没有授权事件、暂停状态、持久化检查点或 resume 入口。因此，不能只在前端弹一个确认框：浏览器刷新、SSE 断开或服务重启后，模型回合和待执行调用都会丢失。

## 2. 目标与非目标

### 目标

1. 对用户只发送事实型 SSE 事件，删除伪“思考/准备/生成”状态文案。
2. 流式回答在用户停留底部时可靠跟随；用户主动上滚时不抢夺阅读位置，并提供明确的回到底部入口。
3. 让 `pan-agent-runtime` 能在工具执行前作出 `allow / ask / deny` 决策，并在 `ask` 时返回可序列化检查点。
4. 在 PanWatch 中持久化待审批调用、权限策略和检查点；用户刷新页面后仍能批准、拒绝或查看该请求。
5. 将包设计成不依赖 FastAPI、SQLAlchemy、OpenAI、前端框架、PanWatch 领域模型或用户模型，以便 BeeCount Cloud 等宿主复用。

### 非目标

- 本期不新增下单、修改持仓或删除数据等写工具；先完成对未来工具的安全执行框架。
- 不把模型内部推理文本或 provider 原始事件直接暴露给用户。
- 不在 `pan-agent-runtime` 内实现数据库、账号、租户、WebSocket/SSE 或浏览器确认框。
- 不以长连接常驻等待人工决定；待审批任务必须可中断、可恢复。

## 3. 事件流：事实优先，展示由 UI 派生

### 3.1 对外 SSE 契约

新助手端点只推送下列用户可理解、可追溯的事件。`run_started` 是真实的任务创建事实，主要用于重连和恢复；它不是进度文案。

| SSE event | 核心字段 | 含义 |
|---|---|---|
| `run_started` | `task_id` | 已创建持久化任务。 |
| `tool_call_start` | `call_id`, `name`, `arguments` | 已决定执行某项工具。 |
| `tool_result` | `call_id`, `name`, `ok`, `preview` | 工具已完成或返回受控失败。 |
| `token` | `text` | 模型实际生成的增量文本。 |
| `approval_required` | `approval_id`, `calls`, `expires_at` | 至少一个工具需要用户决定，任务已暂停。 |
| `paused` | `task_id`, `reason` | 当前流的正常终态；不是 `error`。 |
| `done` | `message_id`, `content`, `created_at` | 最终回答已落库。 |
| `error` | `code`, `message` | 不可恢复的用户可见失败。 |

`RUN_CREATED`、`STEP_UPDATED` 等仍可保留为运行时内部观测事件，供日志、评测和任务快照使用；HTTP 适配器不再把它们翻译成自然语言 `status`。旧 `/api/chat` 兼容流可临时继续解析 `status`，但新的 `/assistant` 路径不产生它。

### 3.2 前端显示规则

- 用户发送后由本地状态显示无文字的 loading 指示，直到第一个工具事件、token、暂停或终态到达。
- 只有收到 `tool_call_start` 才显示“正在查询持仓”等工具名称映射；收到 `tool_result` 后将其折叠为过程记录。
- 收到首个 `token` 后显示正文流；不通过后端虚构“模型正在思考”。
- `approval_required` 渲染为可操作的审批卡片；`paused` 结束旧流，前端进入等待用户决定的状态。

这与 OpenAI Agents SDK 将原始模型增量、工具调用和高层 run item 分层的做法一致；LangGraph 同样把消息、工具和自定义业务进度区分为独立通道。业务进度只有显式选择 `custom` 才应发送，不应伪装为模型状态。

### 3.3 自动滚动规则

`ChatWidget` 提取一个仅负责滚动的 hook，维护 `isNearBottom` 与 `showScrollToBottom`：

1. 发送新消息、打开会话或点击回到底部时，设置 `isNearBottom = true` 并立即滚到底部。
2. 流式 token、工具卡或审批卡改变内容高度时，仅在 `isNearBottom` 为真时执行 `scrollTop = scrollHeight`，使用即时滚动而非逐 token 的平滑动画。
3. 用户滚动时，以 80px 阈值更新 `isNearBottom`；离开阈值即停止自动跟随并显示一个浮动的向下按钮。
4. 点击向下按钮执行一次平滑滚动；滚动结束后重新进入自动跟随状态。
5. 程序化滚动不会反向关闭自动跟随。实现中以“跟随状态”作为来源，而不根据每次平滑滚动中间帧重新判断意图。

## 4. 通用运行时设计

### 4.1 边界

`packages/pan-agent-runtime` 定义运行状态机、契约和端口；它不读取设置、不写数据库、不知道当前用户，也不生成业务文案。PanWatch 与未来 BeeCount Cloud 通过端口注入授权策略、持久化和展示信息。

```text
pan_agent (通用包)
  ToolSpec / ToolRisk / ToolCall
  ToolPolicy → allow | ask | deny
  AgentCheckpoint / PendingApproval
  AgentRuntime.run / AgentRuntime.resume
  RuntimeEvent

宿主应用
  权限设置与身份/租户
  Policy adapter
  审批、检查点与任务持久化
  模型、工具执行器、SSE/HTTP/UI
```

### 4.2 工具风险与权限策略

`ToolSpec` 声明工具的固有风险，不能由前端伪造。风险等级扩展为：

| `ToolRisk` | 含义 | 默认权限模式 |
|---|---|---|
| `READ` | 无副作用读取 | `allow` |
| `WRITE` | 修改本地应用状态 | `ask` |
| `EXTERNAL` | 对外部系统产生动作 | `ask` |
| `DESTRUCTIVE` | 删除、重置或不可逆动作 | `deny` |

权限模式为 `ALLOW`、`ASK`、`DENY`。宿主实现 `ToolPolicy`：

```python
class ToolPolicy(Protocol):
    async def decide(
        self, request: RunRequest, tool: ToolSpec, call: ToolCall
    ) -> ToolPermissionDecision: ...
```

`ToolPermissionDecision` 包含模式、可展示的原因和可选审批摘要。策略分辨率从高到低为：单工具覆盖 → 风险组默认 → 安全默认值。`confirmation_required=True` 是硬下限：无论全局策略如何，它至少为 `ASK`；`DESTRUCTIVE` 不允许被普通“总是允许”设置自动降级。

`ToolRegistry` 不再把 write 工具一律当作注册错误，而是负责能力注册和重复名称校验。为保持现有应用安全，默认注入的 `ReadOnlyToolPolicy` 仍只允许无确认的 `READ` 工具；只有宿主显式注入具备审批能力的策略，write 工具才会对模型可见。

### 4.3 暂停、检查点和恢复

运行时以可序列化的 `AgentCheckpoint` 表示一个可恢复的状态点：

```text
AgentCheckpoint
  messages                 # 包含产生 tool_calls 的 assistant 消息
  answer                   # 已发送的正文增量
  step_index               # 已完成模型回合数
  tool_calls_used          # 已消耗的调用预算
  pending_calls            # 尚待审批的调用（可为同一回合的一批）
  limits / context digest  # 恢复时验证所需的不变量
```

状态机如下：

```text
模型提出调用
  ├─ allow → 执行工具 → 追加 tool result → 下一模型回合
  ├─ deny  → 追加受控的 tool result（permission_denied）→ 下一模型回合
  └─ ask   → 追加完整 assistant.tool_calls
            → 发布 approval_required
            → 返回 WAITING_FOR_APPROVAL + AgentCheckpoint

用户决定
  ├─ approve → 仅执行被批准的 pending call → 下一模型回合
  └─ reject  → 追加 approval_rejected 工具结果 → 下一模型回合
```

同一模型回合的多个调用作为一个审批批次呈现。已经 `allow` 的调用可以先完成并写入检查点；待审批调用不会执行。恢复时必须保留原 assistant `tool_calls` 与每条 tool result 的 `tool_call_id`，以满足 OpenAI 兼容模型的工具协议。批准或拒绝均按 `call_id` 精确生效，且一次决定只能消费一次。

等待授权不是失败：`RunStatus` 新增 `WAITING_FOR_APPROVAL`，`EventType` 新增 `APPROVAL_REQUIRED`。`run()` 在该状态返回；`resume(checkpoint, decisions, sink)` 在新的进程或请求中继续运行。普通拒绝会作为可读工具结果回交模型，让模型解释限制或选择安全替代方案，而不是错误中断整次对话。

### 4.4 通用包 API 与兼容性

- 新增 `ToolPolicy`、`ToolPermissionDecision`、`ApprovalDecision`、`PendingApproval`、`AgentCheckpoint` 等 Pydantic/Protocol 合约，并从 `pan_agent.__init__` 导出。
- `AgentRuntime` 构造参数增加可选 policy，默认 `ReadOnlyToolPolicy`，因此现有只读宿主无需改动即可保持原行为。
- `RunResult` 增加可选 `checkpoint` 与 `pending_approvals`；原有 completed/partial/failed 字段语义不变。
- 运行时事件只承载稳定字段（run/call/tool/risk/arguments/decision），不包含 PanWatch 的数据库 ID、用户 ID、HTML 或本地化文案。
- 包保持仅依赖标准库与 Pydantic，不能导入 `src.*`、FastAPI、SQLAlchemy、OpenAI、LangChain 或 LangGraph。

## 5. PanWatch 宿主实现

### 5.1 持久化模型

现有 `AssistantTaskRun`、`AssistantToolInvocation` 已经是合适的任务边界，扩展而非另建即时任务系统。

| 表/字段 | 责任 |
|---|---|
| `assistant_task_runs.checkpoint`（JSON） | 保存 `AgentCheckpoint`；任务状态可为 `awaiting_approval`。 |
| `assistant_tool_invocations` 扩展 | 保存 `arguments`、`risk` 与 `waiting_approval / approved / rejected / completed` 等状态。 |
| `assistant_tool_approvals` | 每个待审批 call 一条记录：稳定 approval ID、task/call ID、工具、风险、参数、展示摘要、状态、过期时间、决定时间和决定者。 `(task_run_id, call_id)` 唯一。 |
| `assistant_tool_permissions` | 宿主策略设置：`principal_scope`、选择器类型（risk/tool）、选择器值、模式与更新时间。 |

PanWatch 本地版暂以 `principal_scope = local` 存储。BeeCount Cloud 只需要在同一 host adapter 中将该字段映射到 tenant/user，不需要修改 `pan-agent-runtime`。

数据库迁移只做前向添加；已有会话、任务和工具调用记录不迁移或重写。过期审批（默认 10 分钟）在读取、决定和恢复入口均会被拒绝并把任务标为受控失败/过期，防止旧授权被重新使用。

### 5.2 HTTP 与 SSE

```text
POST /assistant/conversations/{id}/messages/stream
  → run_started → 事实事件 → done | error | approval_required → paused

GET /assistant/tasks/{task_id}
  → 任务快照、待审批项与已完成工具摘要

POST /assistant/approvals/{approval_id}/decision/stream
  body: { decision: "approved" | "rejected" }
  → 原子消费决定、恢复 checkpoint，并返回新的 SSE 流

GET /assistant/tool-permissions
PUT /assistant/tool-permissions
  → 当前宿主的风险默认值与工具级覆盖
```

批准决定通过“状态仍为 pending”的条件更新原子消费；重复点击、刷新重放或并发决定只能有一次成功，其他请求返回当前审批/任务快照。浏览器在 `paused` 后关闭旧流；点击决定按钮会建立新的 resume SSE 流。刷新页面时，客户端通过 task snapshot 恢复审批卡，而不是依赖已经消失的连接。

工具展示摘要由 PanWatch 的 approval presenter 生成，至少包含操作对象、影响和人类可读参数；不能仅把原始 JSON 当作安全说明。敏感参数在记录、SSE 和 UI 中按工具规则脱敏。

### 5.3 前端体验与设置

助手消息流新增 `ApprovalCard`：展示工具标题、风险、操作对象、参数摘要、过期时间，支持“本次允许”和“拒绝”。决定期间按钮禁用；决定后卡片保留不可篡改的结果标记。长期权限只在设置页的“智能体工具权限”中修改，不在匆忙的审批卡中提供“总是允许”。

设置页以风险组显示默认策略，并在展开项中显示已注册工具的覆盖规则。建议初始值：读取直接允许、修改本地状态每次询问、外部动作每次询问、破坏性操作禁止。尚未注册的写工具不会在助手中出现，因此本期不会凭空产生审批弹窗。

## 6. 实施顺序

1. **事件与滚动收口**：移除新助手 `status` 事件和前端依赖；修复自动跟随与回到底部按钮。此步无数据库迁移、不会改变工具权限。
2. **运行时授权内核**：先以失败测试实现权限策略、等待状态、批量 pending call、检查点和 `resume()`；默认 read-only 行为不变。
3. **PanWatch 持久化与 API**：添加迁移、repository/service、审批 decision SSE 和快照恢复；用新进程/新 runtime 模拟恢复。
4. **前端审批与设置**：实现审批卡、恢复请求、设置页权限规则和真实工具过程展示。
5. **接入第一项受控写工具**：只在上述全链路验证后选择一个可逆、低影响操作（例如创建提醒）作为验收，不把下单或删除作为首个试点。

## 7. 验收与测试矩阵

### 通用包

- 默认 policy 只执行无确认的 read 工具；write 工具不会因注册失败而破坏宿主启动。
- `ASK` 不执行工具，产生 `WAITING_FOR_APPROVAL`、完整 checkpoint 与批量 `APPROVAL_REQUIRED` 事件。
- 新 runtime 实例从 checkpoint 恢复；批准调用只执行一次，拒绝调用绝不执行。
- 批量调用维持原调用顺序和 `tool_call_id` 关联；模型收到完整 OpenAI 兼容工具历史。
- 调用预算、超时、未知工具和重复 decision 在暂停/恢复边界仍受限。
- 静态检查确认包没有宿主、数据库或框架导入。

### PanWatch

- 新 `/assistant` 流不再出现 `status`，仅出现定义的事实事件。
- 直接回答为 `run_started → token* → done`；查询为工具事件后 token；授权为 `approval_required → paused`。
- 审批刷新、过期、重复点击、拒绝、恢复后 SSE 断开均有一致的持久化状态。
- 权限解析遵循“工具覆盖 > 风险默认 > 安全默认”；`confirmation_required` 与 destructive 工具不能被广域 allow 绕过。
- 自动滚动在持续 token 下保持底部；用户上滚后不被抢回；回到底部按钮恢复跟随。
- 后端全量测试、运行时包测试、前端交互测试和生产构建均通过。

## 8. 安全约束

1. 模型永远不能自行批准工具；允许、拒绝和权限设置只由宿主可信代码产生。
2. 每个审批绑定 task ID、call ID、工具名和参数摘要；恢复时必须验证四者，不接受客户端提交的替换参数。
3. `approval_id` 不可猜测，决定端点验证当前主体/租户；本地版仍校验 pending 状态与过期时间。
4. 破坏性操作默认不可见或拒绝，不能被普通风险组“允许”覆盖。
5. 审批、工具结果、错误和 SSE 记录不得泄露密钥、token、账户全号或未脱敏的敏感参数。

## 9. 与已有方案的关系

本设计补充并更新 `.docs/2026-09-09-assistant-product-design.md` 中“统一流式事件”和“未来写操作确认机制”的约束。该文原先的首版只读范围保持不变；本设计先建立通用授权基础设施，写工具的具体接入仍需逐项评审和测试。
