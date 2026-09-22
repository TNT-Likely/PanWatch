---
feature: homepage-polish
status: delivered
updated: 2026-09-22
branch: codex/homepage-polish
commits: cb725f0b181e45b17b55d3cd1b4cc086930beff1..1257a1f301a87d0f55cd5589aa713e7cbd1fbe94
---

# 首页金融终端精致化

## Report

**What was built** — 首页（Dashboard）按「金融终端精致化」完成视觉升维：落地 `page-container` / `chip*` / `metric` / `empty-hint` / `link-quiet` / `row-hover` / `btn-quiet` 等共享样式；今日盈亏升为 metric 级 hero；要紧事 feed 带类型优先级色条与统一 11px chip；指数 pills、组合体检、机会精选、简报、DiscoveryPanel 对齐同一字阶与涨跌 token（红涨绿跌 · `stock.up/down`）。信息架构、API、路由未改。

**Verification** — `pnpm exec vitest run` → PASS（20 files / 48 tests）；`pnpm build`（`tsc -b && vite build`）→ PASS；`git diff --check` → clean。独立 review 无 CRITICAL；MAJOR（类型徽章借用涨跌 token、页标题压过 hero、`text-[15px]`/`gap-2.5` 破例）已修复并复验。

**Journey log**
- `page-container` 曾被 Dashboard/Opportunities 引用但全库无定义——补共享类比局部 max-width 更能一次修好壳。
- 类型徽章（提醒/持仓）不可复用 `chip-up/down`：会与行内真实涨跌 chip 抢同一套红绿语义，review 抓出后改为 amber/primary。
- `✓ 未见风险` 的「安全绿」也不能用 `stock-down`（那是「跌」token），改回 emerald 状态色。
- 市场分布 stacked 条的 US 绿是分段区分色，不是涨跌色，按 S3 保留硬编码。
- 375px 视觉验收依赖真实运行实例，本轮以响应式 class + 单测壳层覆盖；真机截图可作后续计划。

## [S1] Problem

PanWatch 容器首页（`/` → `Dashboard`）信息结构正确，但视觉完成度停留在「能用的工作台」：

1. `page-container` 在 `Dashboard.tsx` / `Opportunities.tsx` 被引用，全库无定义，大屏缺少 max-width / 居中节奏。
2. 同页混用 `9/10/11/12/13/14.5/15/20/22px` 等 ad-hoc 字号，数字与标签无稳定层级。
3. 要紧事、体检、机会、简报四卡同权，首页主角「今日该看什么 / 今日盈亏」视觉权重不足。
4. 间距 `gap-2/2.5/3`、chip、空态、分享入口各自为政；涨跌色硬编码 `rose-500/emerald-500`，未走 `stock.up/down` token。
5. 空态、hover、徽章等微细节毛糙，整体缺乏金融终端应有的数字排版精度。

用户要求「精致化、精细化美化」，且明确：**保持信息密度**，走「金融终端精致化」路线，范围 **首页 + 共享基础样式**。

## [S2] Design

### 视觉方向

- **风格锚点**：深色专业行情台（TradingView / 金融终端），高密度、强数字排版、克制层次。
- **色板**：保留现有 HSL token（`--primary: 234 85% 55%` 靛蓝、深浅双主题）；涨跌统一映射到 tailwind `stock.up` / `stock.down`（红涨绿跌，A 股口径），消灭页面内硬编码红绿。
- **字阶**（全页只允许这 4 档 + 必要 mono 变体）：
  | Token 用途 | 尺寸 | 场景 |
  |---|---|---|
  | display / metric | `text-2xl`（24px）· `text-xl`（20px） | 今日盈亏 hero、页标题 |
  | title | `text-sm`（14px）font-semibold | 卡片节标题 |
  | body | `text-xs`（12px）· `text-[13px]` | 列表主文案、指标值 |
  | caption | `text-[11px]` | 标签、辅助说明、按钮次文案 |
- **禁止** `text-[9px]` / `text-[10px]` 作为正文或徽章字（徽章下限 11px）；数字一律 `font-mono` + 右对齐（列表内）或固定列宽。
- **布局**：补真实 `.page-container`（`w-full max-w-[1440px] mx-auto`）；卡内/卡间统一 8pt 节奏（`gap-2` / `p-4` 等，替换 2.5 类中间值）。
- **签名时刻**：
  1. **今日盈亏 hero** — 组合速览条左侧今日盈亏放大为 metric 字号，涨跌色 + 涨跌幅 chip 同行；次级指标降为 caption 标签 + mono 值。
  2. **今日要紧事 feed** — 行左侧优先级色条（按 `FEED_BADGE` **类型语义**，不得借用涨跌 token：alert/risk→amber，holding/opportunity→primary，watch→muted），徽章统一 11px chip，hover 行背景。

### 契约

**共享基础样式**（`frontend/src/index.css`）：

```css
.page-container  /* w-full max-w-[1440px] mx-auto */
.card            /* 保留；阴影微调 */
.card-subtle     /* 保留 */
.chip            /* 统一徽章：rounded-full px-2 py-0.5 text-[11px] font-medium */
.chip-up / .chip-down / .chip-muted / .chip-primary / .chip-amber
.metric          /* font-mono tabular-nums leading-tight */
.empty-hint      /* 统一空态：py-8 text-center text-[12px] text-muted-foreground */
.link-quiet      /* 标题栏右侧次级动作 */
.row-hover       /* 列表行 hover */
.btn-quiet       /* 紧凑次级按钮 h-8 */
```

涨跌色 helper：

- `moveColor(v)` → `text-stock-up` / `text-stock-down` / `text-muted-foreground`
- `pctChipCls(v)` → `chip-up` / `chip-down` / `chip-muted`
- 正向状态绿（如「✓ 未见风险」）用 emerald 状态色，**不得**用 `stock-down`。

**首页区块规格**（自上而下，信息架构不变）：

1. **页头** — 左：`text-xl font-bold`「今日该看什么」+ 刷新按钮；右：刷新时间 caption + 市场状态 pill（交易中用 amber 呼吸点，休市灰点）。
2. **组合速览条**（hero）— 无持仓：`empty-hint`。有持仓：左「今日盈亏」metric（24px mono + 涨跌幅 chip）；中「累计浮盈 / 60日超额 / 仓位」caption 标签 + `text-sm` mono 值；右 mini sparkline + 「持仓页 →」`link-quiet`。
3. **指数 pills** — `card-subtle` p-3；价格 `metric text-sm`；涨跌 `chip`；spark 高度 26px。
4. **主体栅格**（`lg:grid-cols-12` 不变）：
   - **今日要紧事**（col-span-7）— 列表行：左优先级色条（`w-0.5` 全高）、徽章 `chip`、名称 13px、why 11px、涨跌 `chip` 右对齐。
   - **组合体检**（col-span-5）— 超额用 `chip`；图表占位独立样式（不与 `empty-hint` 的 py-8 打架）；「AI 体检报告」`btn-quiet`。
   - **机会精选**（col-span-5）— `chip chip-primary`；评分 mono 13px。
   - **简报**（col-span-7）— 「AI · 日期」`chip chip-primary`；展开/收起 `link-quiet`。
5. **机会发现 DiscoveryPanel** — tab chip 选中态；涨跌走 stock token；空态 `empty-hint`。

### 测试边界

- 组件渲染测试：Dashboard 关键区块、`page-container`、涨跌 chip 映射（红涨绿跌）、空态。
- 不测具体像素；不 mock 生产逻辑。
- 既有 `frontend/tests/**` 必须继续通过。

## [S3] Out of Scope

- 不改动信息架构、API、数据加载策略、路由。
- 不重做持仓 / 机会 / 模拟盘等其他页面（仅通过共享 token 间接受益）。
- 不改 AmbientBackground、导航壳、登录页、分享卡图片输出样式。
- 不引入新图标库、新字体文件、新依赖。
- 不做英文文案、不做无障碍专项（仅保持现有语义标签）。
- 375px 真机/截图验收不在本轮（需运行中实例）。

## Tasks

- [x] T1: 在 `index.css` 落地 `page-container` / `chip*` / `metric` / `empty-hint` / `link-quiet` 等共享样式，微调 `.card` — acceptance: 类名可被 Tailwind content 扫到且 build 通过 (covers: S2)
- [x] T2: 重排 Dashboard 页头与组合速览 hero（字阶、chip、间距、涨跌 token） — acceptance: 今日盈亏呈 metric 级视觉主锚，次级指标降为 caption+mono，无 9/10px 正文 (covers: S2; depends: T1)
- [x] T3: 精致化指数 pills + 今日要紧事 feed（优先级色条、chip 体系、空态） — acceptance: feed 行有类型色条与统一 chip，空态走 `empty-hint` (covers: S2; depends: T1)
- [x] T4: 精致化组合体检 / 机会精选 / 简报三卡 — acceptance: 四卡控件语言统一，分享/跳转为 `link-quiet`，AI 体检按钮统一 (covers: S2; depends: T1)
- [x] T5: 精致化 DiscoveryPanel（tab、列表行、涨跌 token、空态） — acceptance: 与首页 chip/字阶/色 token 一致 (covers: S2; depends: T1)
- [x] T6: 移动端间距与 hero 折行校验（Dashboard 响应式 class） — acceptance: 375px 宽下 hero 与栅格不溢出、字阶不换档 (covers: S2; depends: T2, T3, T4)
- [x] T7: 补充/更新前端测试并跑通 test + build — acceptance: `pnpm test` 相关用例通过，`pnpm build` 成功 (covers: S2; depends: T2, T3, T4, T5)
