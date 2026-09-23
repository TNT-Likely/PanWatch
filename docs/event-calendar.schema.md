# 事件日历 Schema（`event_calendar_items`）

事件日历用于盘前操盘「事件前瞻」与复盘「事件对表」：记录未来 5 个交易日与近 30 天的大中型财经事件（议息、数据发布、财报、地缘、解禁等），并落到盘面决策。

- 存储表：`event_calendar_items`（见 `src/platform/persistence/models.py`）
- 导入脚本：`scripts/import_event_calendar.py --file <json>`
- HTTP API：`/api/event-calendar`（GET 日期窗 / POST 单条与批量 / PUT / DELETE）
- 每条事件按 **(event_date, name)** UPSERT：同日同名更新，否则新增，可重复导入

## 字段定义

| 字段 | 类型 | 必填 | 约束 / 说明 |
|---|---|---|---|
| `event_date` | string | ✅ | `YYYY-MM-DD`；导入后为 DATE 列，建索引 |
| `level` | string | ❌ | `high` / `medium` / `low`，缺省 `medium` |
| `name` | string | ✅ | 事件名，如「美联储FOMC决议」；与 `event_date` 组成 UPSERT 键 |
| `scope` | string | ❌ | 影响范围，如「全球」「中国」「行业:半导体」 |
| `expected` | string | ❌ | 预期值，自由文本（兼容「7.1%/前值6.8」这类写法），未定可留空 |
| `actual` | string | ❌ | 实际值，公布后经 PUT / 重新导入回填，未公布留空 |
| `direction` | string | ❌ | 方向倾向：`bullish` / `bearish` / `neutral`，缺省空 |
| `impact_boards` | array | ❌ | 受影响板块代码或名称列表，如 `["BK0475","半导体"]` |
| `meta` | object | ❌ | 扩展信息（来源、备注、链接等），API 自动补，脚本可省 |

## 空模板示例（不含真实数据）

```json
[
  {
    "event_date": "YYYY-MM-DD",
    "level": "medium",
    "name": "示例事件名（占位，请替换）",
    "scope": "全球",
    "expected": "",
    "actual": "",
    "direction": "neutral",
    "impact_boards": []
  },
  {
    "event_date": "YYYY-MM-DD",
    "level": "high",
    "name": "示例数据发布（占位，请替换）",
    "scope": "中国",
    "expected": "",
    "actual": "",
    "direction": "",
    "impact_boards": ["板块代码或名称"]
  }
]
```

## 导入

```bash
python scripts/import_event_calendar.py --file path/to/calendar.json
```

- 任一条目校验失败（日期格式/枚举值/缺 name）→ 整体回滚、不写入任何行，退出码 2；
- 导入是幂等的：同文件重复执行结果一致（已存在的同日同名事件被更新）。

## 校验规则速查

- `event_date`：前 10 位必须能按 ISO 解析为日期；
- `level` ∉ {high, medium, low} → 拒绝；
- `direction` ∉ {bullish, bearish, neutral, ""} → 拒绝；
- `name` 为空或纯空白 → 拒绝；
- `impact_boards` / `meta` 类型不符时按空值归一（数组/对象）。
