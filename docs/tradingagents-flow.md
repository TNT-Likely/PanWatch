# TradingAgents 深度分析流程

PanWatch 从持仓页、Agent 定时任务或已启用的盘中异动规则触发单标的分析。图中包括 PanWatch 的数据采集与结果处理，以及 TradingAgents 内部的多 Agent 决策流程。

```mermaid
flowchart TD
    A["触发：持仓页手动 / Agent 定时任务 / 盘中异动（可选）"] --> B["并行采集行情、K 线、资金流和事件"]
    B --> C["按市场补充技术指标和财务数据<br/>组装标的与持仓上下文"]
    C --> D{"命中同日缓存？"}
    D -- 是 --> CACHE["复用已有分析结果"]
    D -- 否 --> E{"月度预算允许继续？"}
    E -- 否 --> STOP["结束运行并返回预算超限"]
    E -- 是 --> F["配置模型、分析师和数据适配器"]

    F --> G["按配置依次运行分析师<br/>市场 / 情绪 / 新闻 / 基本面<br/>按需调用数据工具"]
    G --> H["看多与看空研究员交替辩论"]
    H --> I{"研究辩论轮数已满？"}
    I -- 否 --> H
    I -- 是 --> J["研究经理整合投资计划"]
    J --> K["交易员形成交易方案"]
    K --> L["激进、保守、中性风控分析师轮流讨论"]
    L --> M{"风控讨论轮数已满？"}
    M -- 否 --> L
    M -- 是 --> N["投资组合经理综合投资计划、交易方案、风控观点与持仓"]

    N --> O["输出决策书与五级评级<br/>Buy / Overweight / Hold / Underweight / Sell"]
    O --> P["TradingAgents 保存最终决策"]
    P --> Q["PanWatch 映射评级并保存分析历史与建议池"]
    Q --> R{"已启用模拟盘联动？"}
    R -- 是 --> PAPER["写入模拟盘信号"]
    R -- 否 --> NOTIFY["按通知策略处理结果"]
    PAPER --> NOTIFY
    CACHE --> NOTIFY
    NOTIFY --> DONE["界面查看分析详情<br/>并按配置推送通知"]

    P -. "后续同标的运行：持有期到期后结算并生成反思" .-> G

    B -. "采集进度" .-> OBS["记录运行进度与模型成本"]
    G -. "节点与模型回调" .-> OBS
```

研究辩论和风控讨论轮数均可配置。图中的模拟盘联动默认关闭；开启后，符合条件的买入或卖出建议才会写入模拟盘信号。TradingAgents 的评级会映射为 PanWatch 的买入、持有或卖出建议；无法解析的评级会标记为待人工复核。

TradingAgents 会单独保存决策日志；持有期结束且行情数据可用后，会将相对基准的表现和反思带入后续同标的分析。PanWatch 的分析历史和建议池用于展示结果，与这份上游决策日志分开保存。

## 相关实现

- [TradingAgentsAgent：采集、缓存、预算检查与上游图调用](../src/modules/automation/tradingagents/agent.py)
- [PanWatch 数据上下文与持仓转换](../src/modules/automation/tradingagents/data_context.py)
- [PanWatch 行情工具适配](../src/modules/automation/tradingagents/toolkit_adapter.py)
- [决策评级映射与模拟盘信号桥接](../src/modules/automation/tradingagents/decision.py)
- [TradingAgents 上游多 Agent 流程（PanWatch 当前依赖 v0.5.0）](https://github.com/TauricResearch/TradingAgents/blob/v0.5.0/tradingagents/graph/setup.py)
