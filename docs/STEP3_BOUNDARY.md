# 第三步：8000 策略引擎 vs 8010 预测引擎 边界分析（2026-08-08）

## 读透 strategy_engine.py (2375行) + panwatch_strategies.yaml (154行) 后的结论

### 8000 策略引擎（src/core/strategy_engine.py）
- 职责：**横截面多因子选股**（从全市场候选股里选好股）
- 流程：
  1. `refresh_entry_candidates`：用 marketdata 扫描市场 → 生成 `entry_candidates` 快照（含 score/action/signal/reason/change_pct/volume_ratio 等预计算特征）
  2. `refresh_strategy_signals`：对候选股跑 YAML 里定义的策略（双低选股/资金热度/放量突破/超卖反弹/动量+质量/低波质量），每个策略算因子分 → 输出 `strategy_signal_runs`（买/卖/持有 + 置信度 + 因子分解）
- 因子源：`alphasift/*` 包（capital_heat@v1.1 / volume_breakout / momentum_quality / low_volatility_quality），**不直接调 marketdata 取资金流**——用的是 EntryCandidate 快照字段
- 数据落库：容器 DB 的 `entry_candidates` + `strategy_signal_runs` 表

### 8010 预测引擎（forecast_server.py）
- 职责：**个股时间序列预测**（给定一只股票，预测未来 N 日价格 + 操作建议）
- 数据：K线 + 资金流(tdx ask) + 龙虎榜(marketdata ftshare) + 情绪(events/news)
- 方法：四模型投票(Kronos/XGBoost/Linear/Lag-Llama) + capital_score + build_recommendation
- 数据落库：宿主机 `~/.panwatch_forecast.db` 的 `forecasts` 表

### 边界判断
| 维度 | 8000 策略引擎 | 8010 预测引擎 |
|------|--------------|--------------|
| 输入 | 全市场候选股(横截面) | 单只股票代码 |
| 输出 | 选股信号(买/卖/持有+因子) | 未来N日价格+操作建议 |
| 因子 | alphasift(动量/质量/资金热度) | 四模型投票+capital_score |
| 用途 | 机会发现(哪些股好) | 个股诊断(这股怎么走) |

**结论：职责不重叠，不存在直接功能重复。**

### 已有/潜在重复点
- 两者都涉及"资金面"：8000 用 capital_heat 因子做**选股排名**，8010 用 capital_score 做**个股预测**——粒度不同，不冲突
- 8010 已接的资金流/龙虎榜是**个股诊断必需维度**，8000 不提供个股级时间序列预测，故非重复

### 建议的协同方式（避免重造，非必须）
- 8010 预测某只股时，可查容器 DB 副本的 `strategy_signal_runs` 表，看 8000 策略引擎是否已对该股出信号（买/卖/持有+因子分解），作为**交叉验证**展示在企微版
- 这样 8010 不重造选股逻辑，复用 8000 已算好的信号
- 前提：容器 DB 副本同步（当前在 /home/ubuntu/.panwatch_data/panwatch.db）
- 注意：strategy_signal_runs 是盘后/定时刷新，和 8010 实时预测可能有时差，展示需标注"策略引擎信号(盘后)"

### 不要做的事
- 不要在 8010 重造因子引擎/alphasift（8000 已有，且更复杂）
- 不要 8010 自己扫全市场选股（那是 8000 的职责）
- 不要改 marketdata/tdx/strategy_engine 底层逻辑（复用已有接口）
