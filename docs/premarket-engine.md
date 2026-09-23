# 盘前决策引擎（premarket_pipeline）

盘前决策引擎是交易日 08:15 自动运行的五阶段流水线：**宏观三卡 → 板块预测 → 选股三价 → 财报核验 → 落库推送**。全部阈值集中在 `src/modules/automation/premarket_config.py`（唯一事实源），阶段实现位于 `src/modules/automation/premarket_pipeline/`。

- Agent 名：`premarket_pipeline`（建议池/信号 `strategy_code` 同名）
- 调度：工作日 08:15（`agent_configs.schedule`，可在 Agent 页调整）
- 报告：当日全量数据声明（逐项 source / as_of / 口径 / degrade_level）落 `analysis_history`

## 1. 事件日历导入

事件日历（`event_calendar_items`）是阶段1 的硬输入：**近 7 天 + 未来 5 个交易日窗口内无任何事件 → STOP 硬停**，只产降级报告。

Schema 字段定义、校验规则与空模板见 [event-calendar.schema.md](event-calendar.schema.md)。导入步骤：

```bash
# 1) 按 schema 准备 JSON（数组，元素字段见 schema 文档）
# 2) 导入（UPSERT 键 = event_date + name，可重复执行）
python scripts/import_event_calendar.py --file path/to/calendar.json
```

- 任一条目非法（日期/枚举/缺 name）→ 整体回滚，退出码 2，不写任何行；
- 也可经 HTTP 维护：`/api/event-calendar`（GET 窗口 / POST 单条与批量 / PUT / DELETE）；
- 每周复盘时滚动维护：已发生事件回填 `actual`，新增事件入栈。

## 2. Agent 配置（模型 / 渠道 / 参数）

配置存 `agent_configs` 表，首次启动由 `agent_catalog.AGENT_SEED_SPECS` 播种；页面入口为 Agent 管理页，API 为 `PUT /api/agents/{agent_name}`（字段：`enabled` / `schedule` / `ai_model_id` / `notify_channel_ids` / `config`）。

`premarket_pipeline` 的 `config` 键（`src/modules/automation/agent_catalog.py`）：

| 键 | 缺省 | 含义 |
|---|---|---|
| `pipeline_timeout_minutes` | 20 | 整体硬超时，超时产降级报告 |
| `llm_timeout_seconds` | 120 | 单次 LLM 请求硬超时 |
| `emit_paper_trading_signal` | **False** | 是否把核验通过的候选直写 `strategy_signal_runs` 驱动模拟盘（见 §6） |
| `auto_create_alerts` | True | 每候选 3 条提醒规则（触及买区/止损/止盈，当日 15:30 过期） |
| `max_candidates` | 5 | 单日候选上限 |
| `board_top_n` | 6 | 板块初筛送 LLM 修正的数量 |
| `mv_min_e8` / `mv_max_e8` | 50 / 3000 | 流通市值过滤（亿） |

模型与通知渠道经 `ai_model_id`（`ai_models`）与 `notify_channel_ids`（`notify_channels`）选择；LLM 调用受 `llm_timeout_seconds` 硬超时，AI 通道 failover 由平台统一处理。

## 3. 数据源矩阵与降级语义

全部外部通道 **fail-soft**：失败返回空并标注来源不可用，绝不编造数据；多源可得时交叉校验，偏差超阈标注 disputed。

| 数据 | 主源 | 备源/降级 | 落库 |
|---|---|---|---|
| 事件日历 | `event_calendar_items`（本地导入） | 无（空窗 → STOP） | 阶段1 直读 |
| 行业快照 | 快照库 `sector_snapshots` | 实时拉 `collect_daily_snapshot` | 逐字段 provenance（source/as_of/caliber/degrade_level） |
| 行业资金流 | 东财行业资金流 | 同花顺（偏差 >20% 标 disputed） | `main_net_inflow` 等 + 交叉校验结论 |
| 涨停家数 | 东财涨停池（official，含 ST） | 全市场快照自算（self_counted，滤 ST） | `limit_up_count` + `limit_up_caliber` |
| 行业动量 | 行业 K 线（5/10/20 日涨幅加权） | 缺失 → 该板块降置信度 | 预测 `meta` |
| 宏观指标 | `macro_indicator_values`（库内最新期） | 通道失败 → 弱通道封顶 | 阶段1 三卡 |
| 全球指数/隔夜美股 | marketdata 全球指数通道（实时/T-1） | 失败 → 声明通道不可用 | 报告「全球市场参照」 |
| 财务摘要 | 东财 | 新浪 / 同花顺（任两路交叉，偏差 >5% FAIL） | `fundamentals_cache` |
| 估值（PE 分位） | `valuation_series`（PE-TTM 序列） | 样本不足 → 技术面支撑降级 | 候选 `valuation` |

降级级别（`degrade_level`）语义：

- **0** 多源一致；**1** 单源；**2** 维度缺失（降置信度）；**3** 通道不可用（缺项也声明）。
- 可用通道 < 2 → 三卡降级为 `snapshot_only`；事件/海外通道弱 → 三卡置信度封顶 4。
- 每次运行的阶段明细（含各 source ok/degrade_level）落 `analysis_history.raw_data.stages`，影子周报据此聚合数据源可用率。

## 4. 质量门禁流程

1. **交易日守卫**：非交易日跳过（不产报告）；
2. **双跑互斥**：同 agent 存在 running 记录（TTL 内）→ 本次跳过；
3. **宏观三卡门禁**（阶段1）：日历空窗 → STOP 硬停；可用通道不足 → snapshot_only；废卡（非法 relax/缺字段）带失败原因重写，最多 2 次；
4. **板块预测**：纯 Python 初筛（动量/四阶段/涨停潮/资金背离）→ LLM 修正（仅接受已知 board_code，confidence 归一到 0-1）→ `sector_predictions` 幂等 upsert；
5. **选股三价**：硬过滤（市值/上市天数/ST/连续亏损一票否决）→ 三高评分（缺项降级规则）→ 三价（估值 PE p30/p50，样本不足降技术面支撑）→ V11 硬校验（盈亏比 ≥ 2，不过则弃）→ LLM 终评；
6. **财报核验（fail-closed 六项）**：披露新鲜度 / 双源交叉 / 单季拆解 / 除权检测 / 披露窗预警 / 字段可得性兜底；任一 FAIL 剔除并注明，WARN 降置信度；
7. **落库推送（WriteSet 两段式）**：组装与执行分离，先删当日本 agent 产物再写入（同日重跑幂等）；提醒规则失败捕获进数据声明不阻断。

## 5. 校准：命中率 API 与影子周报

引擎先影子运行（不写模拟盘信号），用以下工具校准：

**板块预测命中率**：`GET /api/sectors/predictions/hit-rate?from=&to=&top_n=3`

- 逐 `snapshot_date` 取方向为看多的预测按 momentum_score 降序取 top3，对照**同日** `sector_snapshots.change_pct` 降序 top3；预测板块进入实际 top3 即命中；
- 输出区间命中率（板块粒度为主口径 + 天粒度）、逐日明细、样本数；
- 三价触及率从 `agent_prediction_outcomes`（`agent_name=premarket_pipeline`，horizon 1/3/5/10）聚合 `outcome_status` 分布，已评估行的方向命中沿用 `agent_prediction_evaluation.EVALUATION_POLICY` 判定；
- 方向词表说明：仓库 `SectorPrediction.direction` 为 bullish/bearish/neutral 三档（无 strong_up 分档），「strong_up/up」映射为 `bullish`；
- 口径限制：盘中是否实际触及入场/止损/止盈价未落库，触及率以「已评估样本的方向命中率」为近似口径，如实标注。

**影子周报**：

```bash
python scripts/pipeline_shadow_report.py                    # 最近 7 天 → stdout
python scripts/pipeline_shadow_report.py --days 14 --out reports/shadow.md
```

内容：① 命中率逐日趋势（含累计命中率列）；② 与 `premarket_outlook` 既有 outcomes 基线按 horizon 对比命中率（差值单位 pp）；③ 数据源可用率（从 `analysis_history.raw_data.stages` 聚合各 source ok 率与最近降级级别）。

## 6. 模拟盘信号开启条件（人工开关）

`emit_paper_trading_signal` **默认 False**：核验通过的候选只写建议池、提醒规则与预测回评，不直写 `strategy_signal_runs`，模拟盘不会自动开仓。

开启前置条件（校准达标后由人工在 Agent 页把该项改为 True，本引擎不自动变更）：

1. 影子周报板块预测命中率：评估天数 ≥ 20 个交易日、板块粒度命中率 ≥ 40%（随机基线约为 top3/板块数）；
2. 三价复盘：1/5 日 horizon 已评估样本 ≥ 20 且命中率不低于 `premarket_outlook` 基线同期水平；
3. 数据源可用率：核心通道（行业快照/资金流/财务摘要）ok 率 ≥ 80%，无长期 degrade_level=3 的通道；
4. 影子运行期间无未关闭的阶段错误（stage_errors）反复复发。

以上数值为初始校准阈值，随样本积累在 `premarket_config.py` 滚动修订；样本不足时周报会显式标注「样本不足」，不得作为开启依据。
