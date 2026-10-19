# Pan Investment Research

[English](README.md)

使用 LangGraph 的独立多 Agent 投研包。参考 TradingAgents，提供技术、资金、事件
三类专家，多方与空方研究员，以及研究经理、策略、风控挑战和最终决策角色。
角色可以共用同一个底层模型。

## 边界与接入

包只依赖 Pydantic 与 LangGraph，不导入 PanWatch，不读数据库或凭证，不发通知，
不下单。宿主注入模型、只读工具和事件接口，取得类型化建议及带版本的研究产物。

运行图、研究子图和专家工具子图分别负责组合决策、研究协作及工具回路。
专家并行，子图完整结束后才参与父图汇合。关键争议可定向补证或回应，风控可退回
策略修改；这些反馈共用额度，不会无限循环。

```sh
python -m pip install ./packages/pan-investment-research
python -m pytest packages/pan-investment-research/tests -q
```

```python
from pan_investment_research import InvestmentResearch, ResearchInput, ResearchPolicy

result = await InvestmentResearch().run(
    ResearchInput.model_validate(prepared_input),
    model=my_model_adapter,
    tools=my_read_only_tools,
    events=my_event_sink,
    policy=ResearchPolicy(),
)
```

接口见 `ports.py`。模型适配器的每次真实发送，包括 fallback 和 retry，都必须先
等待 `before_attempt()`；关闭 SDK 隐式重试。用量来自该次响应，不通过共享计数
相减。无法确认的用量保留 unknown。

股票键使用 `market:symbol`。输入时间带时区，证据不能越过范围或分析截点。
论点必须引用可见证据。工具按角色授权，只返回范围内证据，不修改已有证据身份。
公告与新闻中的文字属于业务资料，不是执行指令。

## 预算与结论

单研究组完整基础路径为九次逻辑调用，工具、修复和反馈另外计数；多研究组需要
独立核算预算，组合策略、风控和决策统一执行。限制包括实际尝试数、保守 token
预留、工具数、反馈数、并发、输出规模与时间。预估 token 不等于真实账单。

`ready` 表示产物通过包内已经实现的检查，不代表收益保证、完整市场规则验证或
已经核验购买力。缺少币种、结算资料不能授权数量。最终决策不能改变已过风控的
方案。关键争议未解决或预算不足时返回明确的 `review` 和观望结果；基础设施错误
交给宿主处理，取消不会被吞掉。

宿主负责最新账户与市场约束、持久化、幂等交付、通知及持久恢复。默认不启用
checkpoint 恢复。包内测试使用脚本模型与回放资料，不能作为真实模型质量或收益
验证的证据。
