# AU(澳交所 ASX)市场支持

本 fork 在 CN/HK/US 之外新增第四个市场——澳大利亚(ASX),并提供单向的 Ghostfolio 持仓同步。

## 包含内容

- **市场核心**:`MarketCode.AU`,悉尼时区,10:00–16:00 AEST/AEDT 交易时段,代码规则 `^[A-Z]{1,6}$`(如 `VAS`、`CBA`、`WDS`)。Yahoo 符号使用 `.AX` 后缀(`VAS` → `VAS.AX`);市场判定先于 US 兜底执行,因此可以直接把三字母裸代码添加为 AU 标的。
- **交易日历**:ASX 2026 年休市日(元旦、澳大利亚日、耶稣受难日、复活节星期一、澳新军团日、国王诞辰、圣诞节、节礼日及新州特有假日),接入与 CN/HK/US 相同的日历子系统。
- **行情与 K 线**:Yahoo(yfinance)行情 vendor 与 Yahoo chart v8 K 线 vendor 均支持 `.AX` 符号;通过下表 `DataSource` 行启用。行情直接走 chart v8(零 crumb / 零 cookie),不受 Yahoo 对 yfinance 库的 429 限流影响。
- **新闻**:Yahoo 新闻 vendor 在 Yahoo 有覆盖时返回 AU 标的的新闻。实测备注(2026-10,NAS 部署):Yahoo 新闻搜索索引对 `.AX` 符号返回空——与 `.HK` 相同——因此在新供应商接入前,AU 新闻预期为空。
- **碎股数量**:`Position.quantity` 为 float,DRP/零碎申购的持仓可正确同步和显示(整数仍按 `10.5 股` 风格渲染)。
- **前端**:所有市场选择器(自选股、观察列表、持仓、发现、机会、模拟盘、助手选股)均出现 AU;价格经现有 Intl 货币格式化输出 AUD(`A$`)。

## DataSource 配置

行情引擎只读取 `data_sources` 表中存在的供应商。种子行:

| type | provider | priority | supports_batch |
|------|----------|----------|----------------|
| `quote` | `yfinance` | 20 | no |
| `kline` | `yahoo` | 20 | no |
| `news` | `yahoo` | 30 | no |

市场路由无需额外配置:引擎根据符号本身解析市场。

## Ghostfolio 同步(单向)

拉取 Ghostfolio 导出并把持仓镜像到 PanWatch 账户。契约:

- **单向、对 Ghostfolio 只读**:PanWatch 绝不回写;只创建/更新/删除*自己的* PanWatch 账户。
- **前缀限定**:PanWatch 只管理名称以配置前缀(默认 `GF: `)开头的账户;前缀之外的账户永不被触碰。删除只发生在前缀内、且 Ghostfolio 持仓消失时。
- **调度**:cron(默认 `17 8 * * *`),与其他 cron 任务走同一调度器,按应用时区求值。
- **开关**:`GHOSTFOLIO_ENABLED` 环境变量(默认开启)挂载/卸载本模块。
- **API**(需登录):`GET /api/ghostfolio/status`、`PUT /api/ghostfolio/config`、`POST /api/ghostfolio/sync/preview`、`POST /api/ghostfolio/sync`。令牌存于 PanWatch 数据库,status 接口绝不回显。
- **市场映射**:`.AX` → AU 裸代码;`.HK` 裸代码补零到 5 位(`0700.HK` → `00700`);仅当持仓数据源为 Yahoo 时,裸符号才按 US 兜底。

## 日历更新策略

ASX 日历按年内置,与 CN/HK/US 策略一致:交易所在发布下一年假日清单后,把该年份的日期与现有市场并列补入。适用新州规则(ASX 遵循新州公众假日)。
