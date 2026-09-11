# PanAgent 授权中断与真实事件流 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让助手仅输出事实型事件、可靠跟随流式回答，并通过 `pan-agent-runtime` 提供可暂停、可恢复的通用工具授权流程。

**Architecture:** `pan-agent-runtime` 新增风险、策略、待审批调用、检查点和 resume 状态机，但不依赖任何宿主框架。PanWatch 将本地权限规则、审批记录、SQLite 迁移、SSE 与 React 审批卡接在这些端口外；流式 UI 通过本地滚动状态保持阅读位置，不再消费伪进度文案。

**Tech Stack:** Python 3.13、Pydantic v2、asyncio、FastAPI、SQLAlchemy、SQLite、React 18、TypeScript、Vite、Vitest、SSE。

**Spec:** `.docs/2026-09-11-agent-approval-and-streaming-design.md`

## Global Constraints

- `packages/pan-agent-runtime` 只依赖 Python 标准库和 Pydantic，不能导入 `src.*`、FastAPI、SQLAlchemy、OpenAI、LangChain 或 LangGraph。
- 权限决策必须由宿主的可信 `ToolPolicy` 提供；模型和浏览器永远不能自行批准调用。
- 默认 `ReadOnlyToolPolicy` 只允许非确认型 `READ` 工具，保持现有部署安全行为。
- `WRITE` 与 `EXTERNAL` 默认 `ASK`，`DESTRUCTIVE` 默认 `DENY`；`confirmation_required=True` 不能被广域 allow 绕过。
- 所有等待授权任务必须持久化并可在新 runtime 实例中恢复；SSE 连接不是状态来源。
- `/assistant` 只发送 `run_started`、工具、token、审批、暂停和终态等事实事件，不能发送“理解/准备/生成”文案。
- 参数、工具结果、错误和审批摘要不得包含密钥、访问令牌或未脱敏敏感值。
- 所有新增行为先写失败测试并观察失败；每个任务完成后运行范围测试、静态检查和独立中文提交。
- 所有提交均留在 `codex/panagent-backend-modularization`，更新现有 PR，最终按 squash merge。

---

## 文件结构

| 路径 | 职责 |
|---|---|
| `packages/pan-agent-runtime/src/pan_agent/contracts.py` | 风险、权限决定、待审批调用、检查点、事件和运行结果契约。 |
| `packages/pan-agent-runtime/src/pan_agent/policy.py` | `ToolPolicy`、默认只读策略和策略工具可见性。 |
| `packages/pan-agent-runtime/src/pan_agent/registry.py` | 工具注册及按策略筛选模型可见工具。 |
| `packages/pan-agent-runtime/src/pan_agent/runtime.py` | allow/ask/deny、批量暂停和 `resume()` 状态机。 |
| `packages/pan-agent-runtime/src/pan_agent/ports.py` | 不依赖宿主的策略端口。 |
| `packages/pan-agent-runtime/tests/test_{contracts,registry,runtime,policy}.py` | 通用包的策略和恢复回归测试。 |
| `src/platform/persistence/{models,migrations}.py` | PanWatch 审批、权限和检查点的持久化模型、迁移。 |
| `src/modules/assistant/{repository,service,api}.py` | 宿主权限解析、审批持久化、SSE 映射和 resume HTTP 边界。 |
| `tests/test_assistant_{stream_api,approval_repository,approval_api}.py` | 新助手事实流、持久化审批和恢复 API 测试。 |
| `frontend/packages/api/src/chat.ts` | SSE 事件与审批决定请求的类型化客户端。 |
| `frontend/src/components/{ChatWidget,assistant/ApprovalCard}.tsx` | 真实工具过程、审批卡、自动滚动和回到底部入口。 |
| `frontend/src/hooks/useChatAutoScroll.ts` | 可单测的聊天滚动意图和 DOM 滚动封装。 |
| `frontend/src/pages/Settings.tsx` | “智能体工具权限”设置区。 |

---

### Task 1: 收口助手事实事件与流式滚动

**Files:**
- Create: `frontend/src/hooks/useChatAutoScroll.ts`
- Create: `frontend/src/hooks/useChatAutoScroll.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/pnpm-lock.yaml`
- Create: `frontend/vitest.config.ts`
- Modify: `frontend/src/components/ChatWidget.tsx`
- Modify: `frontend/packages/api/src/chat.ts`
- Modify: `src/modules/assistant/api.py:78-213`
- Modify: `tests/test_assistant_stream_api.py`

**Interfaces:**
- Produces `isNearBottom({ scrollHeight, scrollTop, clientHeight }, threshold = 80): boolean` and `useChatAutoScroll()` with `scrollBoxRef`, `followNewContent`, `handleScroll`, `scrollToBottom`, `showScrollToBottom`.
- Produces assistant SSE `run_started: { task_id: number }`; `/assistant` no longer emits `status`.
- Preserves legacy `ChatStreamCallbacks.onStatus` parsing for `/api/chat`, but `ChatWidget` no longer maps it to a visible message.

- [ ] **Step 1: Write the failing backend event-stream tests**

In `tests/test_assistant_stream_api.py`, make `_CompletedRuntime.run()` publish `RuntimeEvent(type=EventType.RUN_CREATED, run_id="12")` through the received sink. Replace the progress assertion with a literal event contract:

```python
def test_assistant_stream_announces_a_durable_run_without_fake_status():
    service = _FakeService(_CompletedRuntime())
    response = asyncio.run(assistant_api.stream_assistant_message(
        1, assistant_api.SendAssistantMessageCommand(content="测试问题"), service,
    ))
    events = asyncio.run(_read_events(response))

    assert events[0] == ("run_started", {"task_id": 12})
    assert all(event != "status" for event, _data in events)
    assert events[-1][0] == "done"
```

Add a disconnect test that consumes the first `run_started` event instead of a `status` event.

- [ ] **Step 2: Run backend tests to verify RED**

Run: `python -m pytest tests/test_assistant_stream_api.py -q`

Expected: the first event is still `status`, so `test_assistant_stream_announces_a_durable_run_without_fake_status` fails.

- [ ] **Step 3: Write the failing scroll-intent unit tests**

Install `vitest`, `jsdom`, `@testing-library/react` and `@testing-library/user-event` as frontend dev dependencies. Add `"test": "vitest"` to `frontend/package.json` and create `frontend/vitest.config.ts` with `test: { environment: 'jsdom', globals: true }`. In `frontend/src/hooks/useChatAutoScroll.test.ts`, test pure geometry rather than browser internals:

```ts
import { describe, expect, it } from 'vitest'
import { isNearBottom } from './useChatAutoScroll'

describe('isNearBottom', () => {
  it('keeps following when the viewport is within the 80px bottom tolerance', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 420, clientHeight: 500 })).toBe(true)
  })

  it('stops following after the user reads more than the tolerance above the bottom', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 500 })).toBe(false)
  })
})
```

- [ ] **Step 4: Run the frontend test to verify RED**

Run: `pnpm --dir frontend test --run src/hooks/useChatAutoScroll.test.ts`

Expected: FAIL because the `test` script and `useChatAutoScroll` export do not exist.

- [ ] **Step 5: Implement the smallest event and scroll changes**

In `_SSEEventSink.publish`, map `EventType.RUN_CREATED` to `("run_started", {"task_id": self._task_id})`; do not map `STEP_UPDATED`. Remove the eager `status` yield from `events()`. Keep token, tool and terminal mappings unchanged.

Implement this hook API:

```ts
export function isNearBottom(
  metrics: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 80,
): boolean {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

export function useChatAutoScroll() {
  const scrollBoxRef = useRef<HTMLDivElement>(null)
  const followingRef = useRef(true)
  const [showScrollToBottom, setShowScrollToBottom] = useState(false)

  const followNewContent = useCallback(() => {
    const box = scrollBoxRef.current
    if (box && followingRef.current) box.scrollTop = box.scrollHeight
  }, [])
  // handleScroll updates followingRef and showScrollToBottom using isNearBottom.
  // scrollToBottom sets followingRef true, hides the button, and calls
  // box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' }) once.
  return { scrollBoxRef, handleScroll, followNewContent, scrollToBottom, showScrollToBottom, resetFollowing }
}
```

Replace the `endRef.scrollIntoView({ behavior: 'smooth' })` effect in `ChatWidget` with `followNewContent()` after message/token/tool changes. Call `resetFollowing()` when sending or opening a conversation. Render an `ArrowDown` icon button only when `showScrollToBottom` is true, positioned above the composer. Replace the idle `思考中...` text with an accessible spinner (`aria-label="正在请求助手回复"`) without visible progress copy. Remove the `onStatus` UI callback from `ChatWidget`; retain tool labels only for `onToolCallStart`.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
python -m pytest tests/test_assistant_stream_api.py -q
pnpm --dir frontend test --run src/hooks/useChatAutoScroll.test.ts
pnpm --dir frontend build
```

Expected: no assistant `status` event; geometry tests pass; TypeScript and production build pass.

- [ ] **Step 7: Commit**

```bash
git add src/modules/assistant/api.py tests/test_assistant_stream_api.py frontend/package.json frontend/pnpm-lock.yaml frontend/packages/api/src/chat.ts frontend/src/components/ChatWidget.tsx frontend/src/hooks
git commit -m "fix: 收口助手事实事件与流式滚动"
```

### Task 2: 在通用包定义权限、待审批调用和检查点契约

**Files:**
- Create: `packages/pan-agent-runtime/src/pan_agent/policy.py`
- Create: `packages/pan-agent-runtime/tests/test_policy.py`
- Modify: `packages/pan-agent-runtime/src/pan_agent/contracts.py`
- Modify: `packages/pan-agent-runtime/src/pan_agent/ports.py`
- Modify: `packages/pan-agent-runtime/src/pan_agent/__init__.py`
- Modify: `packages/pan-agent-runtime/src/pan_agent/registry.py`
- Modify: `packages/pan-agent-runtime/tests/test_contracts.py`
- Modify: `packages/pan-agent-runtime/tests/test_registry.py`

**Interfaces:**
- Produces `ToolRisk.READ | WRITE | EXTERNAL | DESTRUCTIVE`, `PermissionMode.ALLOW | ASK | DENY`, `ApprovalDecision.APPROVED | REJECTED`, `PendingApproval`, `ToolPermissionDecision`, `AgentCheckpoint`.
- Produces `ToolPolicy.is_tool_visible(request, tool) -> bool` and `async ToolPolicy.decide(request, tool, call) -> ToolPermissionDecision`.
- Produces `ReadOnlyToolPolicy`, preserving existing safe host behavior.

- [ ] **Step 1: Write failing policy and registry tests**

In `test_policy.py`, use literal specifications and assert observed decisions:

```python
def test_read_only_policy_hides_write_tools_and_requests_no_confirmation_for_read():
    policy = ReadOnlyToolPolicy()
    read = ToolSpec(name='lookup', title='Lookup', description='Read', risk=ToolRisk.READ)
    write = ToolSpec(name='create_alert', title='Alert', description='Write', risk=ToolRisk.WRITE)

    assert policy.is_tool_visible(request(), read) is True
    assert policy.is_tool_visible(request(), write) is False
    assert asyncio.run(policy.decide(request(), read, ToolCall(id='c1', name='lookup'))) == ToolPermissionDecision.allow()
```

In `test_registry.py`, replace the old assertion that write tools cannot register:

```python
def test_registry_registers_write_tools_but_default_policy_hides_them_from_the_model():
    tools = ToolRegistry()
    tools.register(write_spec('create_alert'), fake_executor)
    assert tools.model_tools(request(), ReadOnlyToolPolicy()) == []
```

In `test_contracts.py`, assert that a `PendingApproval` round-trips through `AgentCheckpoint.model_dump(mode="json")` with literal call IDs and arguments.

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest packages/pan-agent-runtime/tests/test_contracts.py packages/pan-agent-runtime/tests/test_policy.py packages/pan-agent-runtime/tests/test_registry.py -q`

Expected: FAIL because policy classes, enum members and checkpoint contracts are absent; existing registry test still rejects write tools.

- [ ] **Step 3: Implement framework-free contracts and policy**

Add the following types to `contracts.py` with Pydantic validation:

```python
class PermissionMode(StrEnum):
    ALLOW = 'allow'
    ASK = 'ask'
    DENY = 'deny'

class ApprovalDecision(StrEnum):
    APPROVED = 'approved'
    REJECTED = 'rejected'

class ToolPermissionDecision(BaseModel):
    mode: PermissionMode
    reason: str = ''
    @classmethod
    def allow(cls) -> 'ToolPermissionDecision':
        return cls(mode=PermissionMode.ALLOW)
    @classmethod
    def ask(cls, reason: str = '') -> 'ToolPermissionDecision':
        return cls(mode=PermissionMode.ASK, reason=reason)
    @classmethod
    def deny(cls, reason: str) -> 'ToolPermissionDecision':
        return cls(mode=PermissionMode.DENY, reason=reason)

class PendingApproval(BaseModel):
    call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    risk: ToolRisk
    arguments: dict[str, Any] = Field(default_factory=dict)

class AgentCheckpoint(BaseModel):
    messages: list[ModelMessage]
    answer: str = ''
    step_index: int = Field(ge=0)
    tool_calls_used: int = Field(ge=0)
    pending_approvals: list[PendingApproval] = Field(default_factory=list)
```

`ToolPermissionDecision` exposes class constructors `allow()`, `ask(reason: str = '')` and `deny(reason: str)`. `ReadOnlyToolPolicy` returns allow only for visible read tools without `confirmation_required`; all other tools are hidden and deny if passed directly. Extend `ToolRegistry.model_tools` to accept `(request, policy)` and return only tools visible to that policy; registration itself accepts all valid risks.

Export all new public types from `pan_agent.__init__` and declare `ToolPolicy` in `ports.py` so hosts can implement it without importing a default policy class.

- [ ] **Step 4: Verify GREEN and package isolation**

Run:

```bash
python -m pytest packages/pan-agent-runtime/tests/test_contracts.py packages/pan-agent-runtime/tests/test_policy.py packages/pan-agent-runtime/tests/test_registry.py -q
rg -n 'from (src|fastapi|sqlalchemy|openai|langchain|langgraph)|import (src|fastapi|sqlalchemy|openai|langchain|langgraph)' packages/pan-agent-runtime/src
```

Expected: all tests pass; `rg` returns no imports.

- [ ] **Step 5: Commit**

```bash
git add packages/pan-agent-runtime/src/pan_agent packages/pan-agent-runtime/tests/test_contracts.py packages/pan-agent-runtime/tests/test_policy.py packages/pan-agent-runtime/tests/test_registry.py
git commit -m "feat: 定义通用智能体授权契约"
```

### Task 3: 实现可暂停、批量审批和恢复的通用运行时

**Files:**
- Modify: `packages/pan-agent-runtime/src/pan_agent/runtime.py`
- Modify: `packages/pan-agent-runtime/src/pan_agent/contracts.py`
- Modify: `packages/pan-agent-runtime/tests/test_runtime.py`

**Interfaces:**
- Consumes `ToolPolicy`, `AgentCheckpoint`, `PendingApproval`, `ApprovalDecision` and registry policy filtering from Task 2.
- Produces `RunStatus.WAITING_FOR_APPROVAL`, `EventType.APPROVAL_REQUIRED`, `RunResult.checkpoint`, and `AgentRuntime.resume(request, checkpoint, decisions, sink)`.
- `decisions` is `dict[str, ApprovalDecision]`, keyed by `call_id`; it must contain exactly the pending calls in the checkpoint.

- [ ] **Step 1: Write failing runtime tests for pause and resume**

Add a deterministic `AskPolicy` and a recording executor in `test_runtime.py`:

```python
def test_ask_policy_pauses_without_executing_and_returns_a_checkpoint():
    executor_calls = 0
    tools = ToolRegistry()
    tools.register(ToolSpec(name='write_note', title='Write note', description='Write', risk=ToolRisk.WRITE), recording_executor)
    model = FixedModel([ModelTurn(tool_calls=[ToolCall(id='call-1', name='write_note', arguments={'text': 'x'})])])
    runtime = AgentRuntime(model, tools, policy=AskPolicy())
    result = asyncio.run(runtime.run(request(), CollectingSink()))

    assert result.status is RunStatus.WAITING_FOR_APPROVAL
    assert executor_calls == 0
    assert result.checkpoint.pending_approvals == [
        PendingApproval(call_id='call-1', tool_name='write_note', risk=ToolRisk.WRITE, arguments={'text': 'x'})
    ]
```

Add a second test that creates a fresh `AgentRuntime`, resumes a checkpoint with `{'call-1': ApprovalDecision.APPROVED}`, and verifies the executor is called once and the second model turn completes. Add a rejection test asserting no executor call and a tool-result message with `error_code: approval_rejected` reaches the second model turn. Add a two-call test asserting one `APPROVAL_REQUIRED` event contains both calls in original order.

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest packages/pan-agent-runtime/tests/test_runtime.py -q`

Expected: FAIL because `WAITING_FOR_APPROVAL`, `APPROVAL_REQUIRED`, checkpoints and `resume()` do not exist.

- [ ] **Step 3: Implement the pause/resume state machine**

Refactor `AgentRuntime.run()` into an internal execution loop initialized from either a new request or checkpoint. Before executing each model turn, call `self._tools.model_tools(request, self._policy)`. When a turn contains calls:

1. Append exactly one assistant `ModelMessage` containing all `tool_calls` before any tool result.
2. Evaluate each call through `ToolPolicy.decide`.
3. Execute `ALLOW` calls under the existing timeout/budget rules and append their results.
4. Convert `DENY` calls into a `ToolResult.failure(summary='工具权限不足', error_code='permission_denied')`, append it, and continue.
5. Collect `ASK` calls without executing them, publish one `APPROVAL_REQUIRED` event with a `calls` list, and return `RunResult(status=WAITING_FOR_APPROVAL, checkpoint=...)`.

Implement `resume()` by validating the exact pending call ID set. For approved calls run the existing executor exactly once. For rejected calls append `ToolResult.failure(summary='用户拒绝了此操作', error_code='approval_rejected')`. Clear pending calls, then continue the normal model loop from `checkpoint.step_index + 1`; retain the original deadline limits and tool-call budget.

Ensure unknown tool, timeout, tool exception, token streaming and final tool-call protocol keep their existing behavior. `RunResult` must include `checkpoint=None` for terminal runs.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest packages/pan-agent-runtime/tests/test_runtime.py packages/pan-agent-runtime/tests -q`

Expected: all package tests pass; new tests prove pause, fresh-runtime resume, rejection, exact-once execution and batch ordering.

- [ ] **Step 5: Commit**

```bash
git add packages/pan-agent-runtime/src/pan_agent/runtime.py packages/pan-agent-runtime/src/pan_agent/contracts.py packages/pan-agent-runtime/tests/test_runtime.py
git commit -m "feat: 支持智能体工具审批与恢复"
```

### Task 4: 持久化 PanWatch 权限、审批和检查点

**Files:**
- Modify: `src/platform/persistence/models.py:1116-1177`
- Modify: `src/platform/persistence/migrations.py`
- Modify: `src/modules/assistant/repository.py`
- Modify: `src/modules/assistant/service.py`
- Create: `tests/test_assistant_approval_migrations.py`
- Create: `tests/test_assistant_approval_repository.py`

**Interfaces:**
- Produces `AssistantToolApproval` and `AssistantToolPermission` SQLAlchemy models.
- Produces repository methods `save_checkpoint`, `create_approvals`, `decide_approval`, `get_pending_approval`, `resolve_permission`, `get_task_checkpoint`.
- Produces `PanWatchToolPolicy` from `AssistantService.build_runtime`, using a policy snapshot for the local principal.

- [ ] **Step 1: Write failing migration tests**

In `tests/test_assistant_approval_migrations.py`, construct a legacy SQLite database containing the migration-122 assistant tables, run `_m123_assistant_approval_workflow`, then assert literal schema columns and indexes:

```python
assert {'checkpoint'} <= columns('assistant_task_runs')
assert {'arguments', 'risk'} <= columns('assistant_tool_invocations')
assert tables() >= {'assistant_tool_approvals', 'assistant_tool_permissions'}
assert index_names('assistant_tool_approvals') >= {'ux_assistant_approval_run_call'}
```

In `tests/test_assistant_approval_repository.py`, use an in-memory SQLAlchemy session to create a task, persist an `AgentCheckpoint`, create one pending approval, and prove that calling `decide_approval` twice yields exactly one accepted decision.

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest tests/test_assistant_approval_migrations.py tests/test_assistant_approval_repository.py -q`

Expected: FAIL because migration 123, models and repository APIs are absent.

- [ ] **Step 3: Add models, forward migration and atomic repository operations**

Add JSON `checkpoint` to `AssistantTaskRun`; JSON `arguments` and string `risk` to `AssistantToolInvocation`. Add:

```text
assistant_tool_approvals:
  id (opaque UUID text primary key), task_run_id, call_id, tool_name, risk,
  arguments JSON, presentation JSON, status, expires_at, decided_at, decided_by

assistant_tool_permissions:
  id, principal_scope, selector_kind, selector_value, mode, updated_at
```

Migration 123 must use `ALTER TABLE ... ADD COLUMN` only after checking column absence, `CREATE TABLE IF NOT EXISTS`, and create unique `(task_run_id, call_id)` plus lookup indexes. Register it after migration 122.

`decide_approval` must issue a conditional update matching `id`, `status='pending'` and `expires_at > now`; it returns the updated record on success and the current record on a repeat. `save_checkpoint` sets task status `awaiting_approval`, while `finish_task` clears the checkpoint. `resolve_permission` applies exact tool match before risk match before defaults and enforces confirmation/destructive floors.

Construct `PanWatchToolPolicy` in the assistant service from repository data. It exposes all `ALLOW` and `ASK` tools to the model, hides `DENY` tools, and returns presenter-safe approval context without importing it into the generic package.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python -m pytest tests/test_assistant_approval_migrations.py tests/test_assistant_approval_repository.py -q
python -m pytest tests/test_evaluation_migrations.py -q
```

Expected: migration is idempotent, decision is exact-once, permission precedence is deterministic, and existing migration tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/platform/persistence/models.py src/platform/persistence/migrations.py src/modules/assistant/repository.py src/modules/assistant/service.py tests/test_assistant_approval_migrations.py tests/test_assistant_approval_repository.py
git commit -m "feat: 持久化助手工具权限与审批"
```

### Task 5: 实现 PanWatch 暂停、决定和恢复的 HTTP/SSE 边界

**Files:**
- Modify: `src/modules/assistant/api.py`
- Modify: `src/modules/assistant/schemas.py`
- Modify: `src/modules/assistant/service.py`
- Modify: `tests/test_assistant_stream_api.py`
- Create: `tests/test_assistant_approval_api.py`

**Interfaces:**
- Produces `GET /assistant/tasks/{task_id}` snapshots with `pending_approvals`.
- Produces `POST /assistant/approvals/{approval_id}/decision/stream` accepting `{"decision": "approved" | "rejected"}` and returning a fresh SSE stream.
- Maps runtime approval events to `approval_required` followed by `paused`, never `done` or `error` for a normal wait.

- [ ] **Step 1: Write failing HTTP boundary tests**

Create a fake runtime returning `RunResult(status=RunStatus.WAITING_FOR_APPROVAL, checkpoint=checkpoint, pending_approvals=[pending])`. Assert the first stream emits `run_started`, followed by:

```python
assert events[-2] == ('approval_required', {
    'approval_id': 'approval-1',
    'calls': [{'call_id': 'call-1', 'name': 'create_alert', 'risk': 'write', 'arguments': {'symbol': '600519'}}],
    'expires_at': '2026-09-11T00:10:00+00:00',
})
assert events[-1] == ('paused', {'task_id': 12, 'reason': 'approval_required'})
assert service.recorded_assistant_messages == []
```

Add an approved-decision test whose fake service records `resume(checkpoint, {'call-1': ApprovalDecision.APPROVED})` and emits a final `done`. Add a rejected-decision test, an expired approval test returning `409`, and a repeated-decision test proving the second request cannot start another resume worker.

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest tests/test_assistant_stream_api.py tests/test_assistant_approval_api.py -q`

Expected: FAIL because waiting results are currently converted to terminal `error`, and no decision route exists.

- [ ] **Step 3: Implement a shared stream worker and approval resume route**

Extract the common “run runtime, enqueue runtime events, persist terminal result” code in `api.py` into a helper used by initial and resume streams. For a waiting result:

1. persist the checkpoint and create approval rows before sending browser events;
2. enqueue `approval_required` with host-generated opaque approval IDs and redacted presentation;
3. enqueue `paused` and close the stream without saving an assistant message;
4. do not call `fail_task`.

For the decision route, atomically call `service.decide_approval`, load the task checkpoint and all still-pending calls, construct a new runtime, and invoke `runtime.resume(...)` in the response worker. Reject stale, expired, non-pending or mismatched decisions before invoking a model. Reuse the existing timeout/cancellation safeguards. Add snapshots of pending approvals to `get_task_snapshot`.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_assistant_stream_api.py tests/test_assistant_approval_api.py tests/test_assistant_approval_repository.py -q`

Expected: factual stream ordering, pause persistence, approve/reject resume, expiry and duplicate decision cases pass.

- [ ] **Step 5: Commit**

```bash
git add src/modules/assistant/api.py src/modules/assistant/schemas.py src/modules/assistant/service.py tests/test_assistant_stream_api.py tests/test_assistant_approval_api.py
git commit -m "feat: 提供助手审批暂停与恢复接口"
```

### Task 6: 连接审批卡、权限设置和恢复后的聊天体验

**Files:**
- Create: `frontend/src/components/assistant/ApprovalCard.tsx`
- Create: `frontend/src/components/assistant/ApprovalCard.test.tsx`
- Modify: `frontend/packages/api/src/chat.ts`
- Modify: `frontend/src/components/ChatWidget.tsx`
- Modify: `frontend/src/pages/Settings.tsx`
- Create: `frontend/src/pages/Settings.agent-permissions.test.tsx`

**Interfaces:**
- Produces `AssistantApproval` and `ToolPermission` TypeScript types and `chatApi.decideAssistantApprovalStream()`.
- Produces `<ApprovalCard approval onDecision={...} />`, where `onDecision` receives `'approved' | 'rejected'` exactly once.
- Produces a Settings “智能体工具权限” panel reading and writing `GET/PUT /assistant/tool-permissions`.

- [ ] **Step 1: Write failing component and API-client tests**

Use Testing Library and MSW/fetch mocks already installed in Task 1. In `ApprovalCard.test.tsx`:

```tsx
it('allows exactly one explicit decision and shows the operation summary', async () => {
  const onDecision = vi.fn().mockResolvedValue(undefined)
  render(<ApprovalCard approval={{
    id: 'approval-1', tool_title: '创建提醒', risk: 'write',
    summary: '为贵州茅台创建价格提醒', expires_at: '2026-09-11T00:10:00Z', status: 'pending',
  }} onDecision={onDecision} />)

  await userEvent.click(screen.getByRole('button', { name: '本次允许' }))
  expect(onDecision).toHaveBeenCalledWith('approved')
  expect(screen.getByRole('button', { name: '拒绝' })).toBeDisabled()
})
```

In the settings test, assert defaults render as “读取：直接允许、修改：每次询问、外部操作：每次询问、破坏性操作：禁止”, changing `create_alert` to `allow` sends a tool-level override, and a destructive tool has no allow option.

- [ ] **Step 2: Run to verify RED**

Run:

```bash
pnpm --dir frontend test --run src/components/assistant/ApprovalCard.test.tsx src/pages/Settings.agent-permissions.test.tsx
```

Expected: FAIL because approval types, card, permissions API and settings section are absent.

- [ ] **Step 3: Implement the API client and assistant UI**

Extend `ChatStreamCallbacks` with `onRunStarted`, `onApprovalRequired` and `onPaused`; parse corresponding SSE events. Add:

```ts
decideAssistantApprovalStream(
  approvalId: string,
  decision: 'approved' | 'rejected',
  callbacks: ChatStreamCallbacks,
): Promise<void>
```

to call `/assistant/approvals/${approvalId}/decision/stream` through the existing SSE reader.

In `ChatWidget`, retain `taskId` and `pendingApprovals` for the active run. Render `ApprovalCard` inline where the transient spinner previously appeared. Its handler disables duplicate actions, starts the decision SSE stream, and reuses the exact token/tool/done callbacks used by the initial run. On `paused`, set `sending=false` but keep the approval card visible. On page recovery, fetch the active task snapshot before declaring a paused conversation complete.

Add the settings section using the existing settings card, select and toast patterns. Show risk defaults first, then registered-tool overrides. Never expose an “always allow” control for destructive tools or confirmation-required tools.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
pnpm --dir frontend test --run
pnpm --dir frontend build
```

Expected: approval decisions are single-fire, permission controls follow risk constraints, and production build passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/api/src/chat.ts frontend/src/components/ChatWidget.tsx frontend/src/components/assistant frontend/src/pages/Settings.tsx frontend/src/pages/Settings.agent-permissions.test.tsx
git commit -m "feat: 新增助手工具审批与权限设置"
```

### Task 7: 端到端恢复验证与交付收口

**Files:**
- Create: `tests/test_assistant_approval_resume_integration.py`
- Create: `.docs/2026-09-11-agent-approval-and-streaming-verification.md`
- Modify: `README.md` only if public user-facing assistant documentation already references tool behavior

**Interfaces:**
- Consumes the persisted task/approval records, a fresh `AgentRuntime` and the decision SSE endpoint from Tasks 3–6.
- Produces a verified recovery scenario without a live model or external network.

- [ ] **Step 1: Write the failing fresh-runtime integration test**

Use a file-backed temporary SQLite database. Start a task whose model requests a write tool under `ASK`, close the first service/runtime, build a fresh service/runtime against the same database, approve the stored approval, then assert:

```python
assert snapshot_after_pause['status'] == 'awaiting_approval'
assert approved_executor_calls == 1
assert final_snapshot['status'] == 'completed'
assert final_messages[-1].content == '提醒已创建'
assert final_snapshot['tools'][0]['status'] == 'completed'
```

Add a sibling test for rejection with `approved_executor_calls == 0` and an assistant explanation generated after an `approval_rejected` tool result.

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest tests/test_assistant_approval_resume_integration.py -q`

Expected: FAIL until persistence and resume wiring from Tasks 3–5 is complete.

- [ ] **Step 3: Verify the fresh-runtime scenario is GREEN**

Run: `python -m pytest tests/test_assistant_approval_resume_integration.py -q`

Expected: PASS. The test must prove that the existing runtime `resume()` path, repository checkpoint and decision API wiring are sufficient; it must not introduce a second execution path.

- [ ] **Step 4: Run the verification matrix**

Run:

```bash
python -m pytest packages/pan-agent-runtime/tests -q
python -m pytest tests -q
python -m compileall -q src packages/pan-agent-runtime/src server.py
pnpm --dir frontend test --run
pnpm --dir frontend build
git diff --check
```

Expected: all backend/package/frontend tests and builds pass; no whitespace error; manual browser check confirms continuous token following, user-controlled stop, visible return-to-bottom button, approval pause, refresh recovery, approve and reject flows.

- [ ] **Step 5: Write verification evidence and commit**

Record exact command results, browser scenarios and any existing non-blocking warnings in `.docs/2026-09-11-agent-approval-and-streaming-verification.md`.

```bash
git add tests/test_assistant_approval_resume_integration.py .docs/2026-09-11-agent-approval-and-streaming-verification.md README.md
git commit -m "test: 验证助手审批恢复流程"
```

## Plan Self-Review

| 设计要求 | 实现任务 |
|---|---|
| 删除伪状态、只发事实事件 | Task 1、Task 5 |
| 自动滚动与回到底部入口 | Task 1 |
| 通用风险、策略和安全默认值 | Task 2 |
| 暂停、批量审批与新实例恢复 | Task 3 |
| 不泄露业务/数据库进通用包 | Task 2、Task 3 的隔离检查 |
| 权限、审批、检查点持久化 | Task 4 |
| SSE 暂停、决定和恢复 | Task 5 |
| 审批卡和权限设置 | Task 6 |
| 刷新恢复、拒绝、重复决定与全量验证 | Task 7 |

The plan contains no implementation placeholder: each task names files, exact exported interfaces, red test, expected failure, minimal implementation boundary, verification command and a Chinese commit message.
