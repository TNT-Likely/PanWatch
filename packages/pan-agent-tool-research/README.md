# PanAgent Tool Research

`pan-agent-tool-research` 是 `pan-agent-runtime` 的可选插件，用于在工具数量增长
时对工具进行确定性检索和策略过滤。它不属于 runtime 核心，不连接数据库、Redis、
向量服务或模型供应商。

## 能力

- `ToolDescriptor`：工具用途、关键词、别名、能力域、风险和数据新鲜度；
- `ToolCatalog`：进程内、版本化的描述元数据目录；
- `KeywordToolRetriever`：无模型调用的关键词和别名检索；
- `ToolResearchService`：应用启用状态、能力域和宿主 `ToolPolicy` 后返回候选；
- `ToolResearchPlugin`：以 shadow 或 active 模式接入 runtime。

## 接入

```python
from pan_agent import AgentRuntime
from pan_agent_tool_research import ToolResearchPlugin, ToolResearchService

research = ToolResearchService(
    registry,
    descriptors=my_tool_descriptors,
)
runtime = AgentRuntime(
    model,
    registry,
    policy=policy,
    extensions=[ToolResearchPlugin(research, mode="shadow")],
)
```

`shadow` 模式只发出 `extension_event`，不改变模型看到的工具集合；`active` 模式才会
返回候选工具名，由 runtime 与宿主权限策略交集后暴露给模型。插件失败默认回退到原有
工具集合，不会阻断 Agent 任务。

## 事件

插件通过 runtime 的通用扩展事件发出：

- `extension=tool_research, event=started`；
- `candidates_scored`；
- `completed`；
- `fallback`。

宿主可以把 `RuntimeEvent` 直接写入自己的任务事件表，也可以忽略插件事件。插件不会把
用户原文写入事件，started 事件只包含查询哈希。

## 开发

```bash
python -m pip install -e packages/pan-agent-runtime
python -m pip install -e packages/pan-agent-tool-research
python -m pytest packages/pan-agent-tool-research/tests -q
```
