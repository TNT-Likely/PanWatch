# PanAgent Tool Research

`pan-agent-tool-research` 是 `pan-agent-runtime` 的可选插件，用于在工具数量增长
时提供渐进式工具发现能力。它不属于 runtime 核心，M0 不连接数据库、Redis、向量服务
或模型供应商。

## 能力

- `ToolDescriptor`：工具用途、关键词、别名、能力域、风险和数据新鲜度；
- `ToolCatalog`：进程内、版本化的描述元数据目录；
- `KeywordToolRetriever`：无模型调用的关键词和别名检索；
- `ToolResearchService`：应用启用状态、能力域和宿主 `ToolPolicy` 后返回候选；
- `ToolResearchPlugin`：以 shadow 或 active 模式接入 runtime；active 模式提供模型可调用
  的 `tool_search` 虚拟工具。

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
    extensions=[ToolResearchPlugin(research, mode="active")],
)
```

`shadow` 模式只发出 `extension_event`，不改变模型看到的工具集合；`active` 模式会：

1. 保留 Registry 标记为 `direct` 的工具；
2. 暴露一个模型可调用的 `tool_search`；
3. 搜索结果进入下一轮消息，并将选中的 Deferred 工具按完整 schema 暴露；
4. 每次执行仍由 Runtime Policy 和 Registry 再次校验。

Registry 中的工具可以通过 `ToolSpec.exposure` 设置为 `direct`、`deferred` 或 `hidden`：

```python
from pan_agent import ToolExposure, ToolSpec

ToolSpec(
    name="get_special_report",
    title="专项报告",
    description="查询专项报告。",
    exposure=ToolExposure.DEFERRED,
)
```

插件失败默认回退到当前 Direct 工具集合，不会因为目录或检索服务异常而清空模型工具空间。
M0 的 active 检索仍然使用进程内关键词、别名和结构化元数据，不强制调用意图模型。

## 事件

插件通过 runtime 的通用扩展事件发出：

- `extension=tool_research, event=started`；
- `exposure`：当前 Direct 工具和已经加载的工具；
- `candidates_scored`；
- `completed`；
- `searched`：模型实际调用 `tool_search` 后的结果；
- `fallback`。

宿主可以把 `RuntimeEvent` 直接写入自己的任务事件表，也可以忽略插件事件。插件不会把
用户原文写入事件，搜索事件只记录查询哈希、候选数量、工具名和版本信息。

## 开发

```bash
python -m pip install -e packages/pan-agent-runtime
python -m pip install -e packages/pan-agent-tool-research
python -m pytest packages/pan-agent-tool-research/tests -q
```
