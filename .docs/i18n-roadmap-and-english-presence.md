# PanWatch 国际化与英文项目介绍路线图

## 目标

PanWatch 的国际化采用渐进式交付：先保证英文用户能够理解项目、完成部署和基础配置，再覆盖核心盯盘与分析路径，最后处理通知、报告和后端错误等界面之外的内容。

本路线图不把“界面语言”与“市场、币种、时区、AI 输出语言”绑定：

- 切换英文界面不会自动切换到美股、美元或美国时区。
- 股票名称、新闻原文、模型名称等外部或用户数据不强制翻译。
- AI 报告语言作为独立偏好设计，不跟随界面语言隐式变化。

## 当前基线

- 已建立 `zh-CN` / `en-US` 的 i18next 运行时、类型安全资源和本地语言偏好。
- 已覆盖登录、首次设置、导航、账户菜单、路由状态和版本更新提示等应用外壳。
- 登录后语言入口位于头像下拉菜单；登录前保留快捷切换。
- 13 个顶层页面中，登录页已完整接入，其余业务页面仍以中文为主。
- 英文界面继续标记为 Experimental，直至核心用户路径达到发布标准。

## 迭代原则

1. 每个 PR 覆盖一条完整用户路径或一组强关联页面，避免全仓一次性替换。
2. 先抽取共享文案，再迁移页面，避免按钮、空状态、错误提示重复定义。
3. 翻译 key 表达语义，不包含视觉位置或具体中文，例如使用 `portfolio.empty.title`，不使用 `leftCardText`。
4. 后端逐步返回稳定错误码，由前端根据错误码翻译；原始错误信息保留用于日志和诊断。
5. 每批同时补充中文与英文渲染测试，不接受只添加 key、没有实际页面验证的迁移。
6. 术语优先保持一致，不逐字翻译产品和金融概念。

## 推荐目录演进

当前单文件资源适合底座阶段。进入批量迁移前，按领域拆分：

```text
frontend/src/i18n/
  locales/
    zh-CN/
      common.ts
      settings.ts
      portfolio.ts
      agents.ts
      assistant.ts
    en-US/
      common.ts
      settings.ts
      portfolio.ts
      agents.ts
      assistant.ts
  index.ts
  format.ts
  i18next.d.ts
```

继续使用 TypeScript 结构约束保证两种语言 key 对齐。只有当首屏资源体积成为实际问题时再引入按 namespace 懒加载，当前阶段不提前增加运行时复杂度。

## 分阶段交付

### P0：国际化工程化

建议单独 PR 完成：

- 按领域拆分翻译资源。
- 统一公共按钮、表单标签、确认弹窗、Toast、加载、空状态和错误状态。
- 增加缺失 key、两种语言结构一致性和关键格式化测试。
- 增加面向 UI 源码的中文硬编码检查，排除日志、测试数据、股票名称和外部内容。
- 建立术语表并在评审中检查一致性。

### P1：首次配置路径

覆盖 `Settings`、`DataSources`、`Agents` 和系统自检，使英文用户可以：

- 配置 AI 服务与模型。
- 配置通知渠道。
- 设置 Agent 调度和权限。
- 导入或导出配置包。
- 识别连接、校验与保存错误。

### P2：日常盯盘路径

覆盖 `Dashboard`、`Stocks`、`Opportunities` 和 `PriceAlerts`：

- 持仓与关注列表。
- 行情、盈亏、市场状态和筛选器。
- 机会发现、建议状态和价格提醒。
- 新建、编辑、删除、空状态和异常状态。

### P3：AI 分析路径

覆盖 `Assistant`、`AnalysisDetail` 和相关业务组件：

- 对话、审批、工具执行状态与 Trace。
- 个股分析、TradingAgents 进度、结论和失败状态。
- 明确区分 UI 语言与 AI 输出语言。

### P4：次要业务模块

覆盖 `PaperTrading`、`Evaluations`、`History` 及剩余弹窗和卡片，清理业务界面的中英混排。

### P5：界面外输出

- 后端错误码和前端错误映射。
- 通知标题与正文模板。
- 分享卡、导出文件和报告。
- AI 报告语言偏好。
- 英文版深度文档和贡献指南。

## 每批验收标准

- 中文和英文下均能完成本批对应的用户路径。
- 除产品名、股票名、模型名和外部内容外，不出现非预期中英混排。
- 日期、数字、百分比和货币使用统一格式化函数。
- 切换语言后无需刷新，刷新后偏好仍保留。
- 英文按钮、表格和移动端布局没有明显截断或溢出。
- 新增文案在两种语言下都有自动化测试或关键页面渲染测试。
- `pnpm exec vitest run`、`pnpm build` 和 `git diff --check` 通过。

## 英文 README 策略

- 保留 `README.md` 作为简体中文主文档，新增 `README.en.md`。
- 两份 README 顶部互相提供语言入口。
- 英文 README 完整覆盖产品定位、核心能力、截图、快速开始、配置、开发、可观测性、发布、贡献和许可证。
- 在核心页面尚未完成前，显著标注 English UI 为 Experimental。
- 功能、安装命令、环境变量或发布流程变化时，两份 README 在同一 PR 中同步更新。
- 暂不要求一次性翻译所有 `docs/` 内容；优先翻译直接影响安装、配置和排障的文档。

## GitHub About 中英双语头脑风暴

本轮只沉淀候选文案，不修改 GitHub About。

### 方案 A：定位优先（推荐）

```text
自托管 AI 盯盘与多 Agent 投研助手｜Self-hosted AI stock monitoring & multi-agent investment research
```

优点：简洁、双语信息对称，能同时说明自托管、AI 盯盘和多 Agent 投研三个核心差异点。

### 方案 B：市场与功能优先

```text
A股/港股/美股 AI 盯盘、持仓分析与多渠道提醒｜AI monitoring, portfolio insights & alerts for CN/HK/US markets
```

优点：用户一眼能看到市场覆盖和功能；缺点是没有突出自托管与 TradingAgents。

### 方案 C：品牌与技术特色优先

```text
盯盘侠 PanWatch — 自托管 AI 股票监控与 TradingAgents 投研平台｜Self-hosted AI stock monitoring & research
```

优点：强化品牌和 TradingAgents；缺点是对不了解 TradingAgents 的用户解释性略弱。

### 建议 Topics

```text
self-hosted, ai, stock-monitoring, portfolio, investment-research,
multi-agent, tradingagents, fastapi, react, china-stocks
```

### 启用时机

建议在 P1 首次配置路径完成、英文用户可以独立完成部署和配置后，再正式修改 GitHub About。届时优先采用方案 A，并根据 README 和社区反馈调整关键词。

## 推荐 PR 顺序

1. 当前 PR：国际化底座、英文 README、路线图和完整配置迁移。
2. P0 PR：资源拆分、公共文案与检查机制。
3. P1 PR：设置、数据源、Agent 和系统自检。
4. P2 PR：首页、持仓、机会和提醒。
5. P3/P4 PR：AI 分析与其余业务页面。
6. P5 PR：通知、报告、后端错误和深度英文文档。
