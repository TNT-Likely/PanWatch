# 贡献指南

感谢你对 PanWatch 的兴趣！本文档将指导你如何贡献代码，特别是如何编写 Agent 和数据源。

## 目录

- [项目结构](#项目结构)
- [开发环境](#开发环境)
- [编写 Agent](#编写-agent)
- [编写数据源](#编写数据源)
- [提交规范](#提交规范)

---

## 项目结构

```
PanWatch/
├── src/
│   ├── agents/           # Agent 实现
│   │   ├── base.py       # 基类和数据结构
│   │   ├── daily_report.py
│   │   └── ...
│   ├── collectors/       # 数据采集器
│   │   ├── news_collector.py
│   │   ├── kline_collector.py
│   │   └── ...
│   ├── core/             # 核心模块
│   │   ├── ai_client.py
│   │   └── notifier.py
│   └── web/              # Web API
├── prompts/              # AI Prompt 模板
├── frontend/             # React 前端
└── server.py             # 入口文件
```

---

## 开发环境

```bash
# 后端
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py

# 前端
cd frontend
pnpm install
pnpm dev
```

---

## 编写 Agent

Agent 是 PanWatch 的核心分析单元，负责采集数据、调用 AI 分析、发送通知。

### 1. 创建 Agent 文件

在 `src/agents/` 目录创建新文件，例如 `my_agent.py`：

```python
import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult

logger = logging.getLogger(__name__)

# Prompt 文件路径
PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "my_agent.txt"


class MyAgent(BaseAgent):
    """我的自定义 Agent"""

    # 必填：Agent 标识（英文，用于数据库和 API）
    name = "my_agent"

    # 必填：显示名称（中文，用于界面展示）
    display_name = "我的 Agent"

    # 必填：描述
    description = "这是一个自定义 Agent 的示例"

    async def collect(self, context: AgentContext) -> dict:
        """
        采集数据

        Args:
            context: 包含 watchlist（自选股列表）、portfolio（持仓信息）等

        Returns:
            采集到的数据字典，将传递给 build_prompt
        """
        data = {
            "stocks": [],
            "timestamp": datetime.now().isoformat(),
        }

        # 遍历自选股采集数据
        for stock in context.watchlist:
            # stock.symbol: 股票代码
            # stock.name: 股票名称
            # stock.market: 市场（CN/HK/US）
            pass

        # 获取持仓信息
        # context.portfolio.all_positions: 所有持仓列表
        # context.portfolio.get_aggregated_position(symbol): 获取某只股票的汇总持仓

        return data

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        构建 AI Prompt

        Args:
            data: collect() 返回的数据
            context: Agent 上下文

        Returns:
            (system_prompt, user_content) 元组
        """
        # 读取 Prompt 模板
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 构建用户输入
        lines = []
        lines.append("## 数据")
        # ... 格式化数据

        user_content = "\n".join(lines)
        return system_prompt, user_content

    async def should_notify(self, result: AnalysisResult) -> bool:
        """
        是否发送通知（可选重写）

        默认返回 True，可根据分析结果决定是否通知
        """
        # 例如：只有重要信号才通知
        # return "重要" in result.content
        return True
```

### 2. 创建 Prompt 模板

在 `prompts/` 目录创建对应的 Prompt 文件 `my_agent.txt`：

```
你是一个专业的股票分析师。

## 任务
根据提供的数据进行分析...

## 输出格式
请按以下格式输出：
1. 概述
2. 详细分析
3. 建议
```

### 3. 注册 Agent

在 `server.py` 中注册：

```python
# 1. 导入
from src.agents.my_agent import MyAgent

# 2. 添加到 AGENT_REGISTRY
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    # ...
    "my_agent": MyAgent,  # 添加这行
}

# 3. 在 seed_agents() 中添加配置
def seed_agents():
    agents = [
        # ...
        {
            "name": "my_agent",
            "display_name": "我的 Agent",
            "description": "这是一个自定义 Agent",
            "enabled": False,  # 默认禁用，用户手动启用
            "schedule": "0 16 * * 1-5",  # cron 表达式
            "execution_mode": "batch",  # batch: 批量分析 / single: 逐只分析
        },
    ]
```

### 4. Agent 上下文说明

`AgentContext` 提供以下信息：

| 属性 | 类型 | 说明 |
|------|------|------|
| `watchlist` | `list[StockConfig]` | 关联的自选股列表 |
| `portfolio` | `PortfolioInfo` | 持仓组合信息 |
| `ai_client` | `AIClient` | AI 客户端 |
| `notifier` | `NotifierManager` | 通知管理器 |
| `model_label` | `str` | 当前使用的模型标签 |

### 5. 执行模式

- **batch**：所有股票一起分析，适合日报类
- **single**：逐只股票分析，适合实时监控类

---

## 编写数据源

数据源负责从外部 API 获取数据（行情、新闻、K线等）。

### 1. 数据源类型

| 类型 | 说明 | 示例 |
|------|------|------|
| `quote` | 实时行情 | 腾讯行情 |
| `kline` | K线数据 | 腾讯K线 |
| `news` | 新闻资讯 | 东方财富新闻 |
| `capital_flow` | 资金流向 | 东方财富资金 |
| `chart` | K线截图 | 雪球截图 |

### 2. 创建数据采集器

以新闻采集器为例，在 `src/collectors/` 创建文件：

```python
"""我的新闻采集器"""
import logging
from datetime import datetime
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    """新闻数据结构"""
    source: str           # 数据源标识
    external_id: str      # 外部唯一ID
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    url: str = ""


class MyNewsCollector:
    """我的新闻采集器"""

    source = "my_news"

    def __init__(self, config: dict = None):
        """
        初始化

        Args:
            config: 数据源配置（来自数据库 DataSource.config）
        """
        self.config = config or {}
        self.api_key = self.config.get("api_key", "")

    async def fetch_news(
        self,
        symbols: list[str] | None = None,
        since: datetime | None = None,
    ) -> list[NewsItem]:
        """
        获取新闻

        Args:
            symbols: 股票代码列表（可选，用于过滤）
            since: 起始时间（可选）

        Returns:
            NewsItem 列表
        """
        results = []

        async with httpx.AsyncClient() as client:
            # 调用 API
            resp = await client.get("https://api.example.com/news")
            data = resp.json()

            for item in data:
                results.append(NewsItem(
                    source=self.source,
                    external_id=str(item["id"]),
                    title=item["title"],
                    content=item["content"],
                    publish_time=datetime.fromisoformat(item["time"]),
                    symbols=item.get("symbols", []),
                    url=item.get("url", ""),
                ))

        return results
```

### 3. 注册数据源

在 `server.py` 的 `seed_data_sources()` 中添加：

```python
def seed_data_sources():
    sources = [
        # ...
        {
            "name": "我的新闻源",
            "type": "news",
            "provider": "my_news",  # 对应 collector 的 source
            "config": {
                "api_key": "",  # 用户在界面配置
            },
            "enabled": False,
            "priority": 10,  # 优先级，数字越小优先级越高
            "supports_batch": True,  # 是否支持批量查询
            "test_symbols": ["600519"],  # 测试用股票代码
        },
    ]
```

### 4. 在 Agent 中使用数据源

```python
from src.collectors.my_collector import MyNewsCollector

class MyAgent(BaseAgent):
    async def collect(self, context: AgentContext) -> dict:
        collector = MyNewsCollector()
        news = await collector.fetch_news(
            symbols=[s.symbol for s in context.watchlist]
        )
        return {"news": news}
```

---

## 提交规范

### Commit 格式

```
<type>: <subject>

<body>
```

**Type 类型：**
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `refactor`: 重构
- `style`: 格式调整
- `test`: 测试相关

**示例：**
```
feat: 添加盘中监控 Agent

- 支持价格异动检测
- 支持成交量异动检测
- AI 智能判断是否需要通知
```

### PR 要求

1. 确保代码通过 lint 检查
2. 新增功能需要更新文档
3. Agent 需要提供 Prompt 模板
4. 数据源需要说明 API 来源和限制

---

## 问题反馈

如有问题，请提交 Issue 或 PR。
