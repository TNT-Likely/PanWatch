"""LLM-as-judge：语义维度评分框架（相关性/有据性/清晰度）。

设计约束：
- judge 模型配置**只从环境变量读取**（EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY /
  EVAL_JUDGE_MODEL），绝不读用户数据库里的 AI 服务配置；
- 固定模型 + 低温（temperature=0），保证评分可复现；
- 单测里注入 mock 客户端，不发任何真实请求；真实运行由作者
  `EVAL_JUDGE_*=... make eval EVAL_ARGS=--judge` 触发；
- 评分结果建议抽样人工校准后再纳入门禁参考——规则断言（framework.py）永远优先。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# 评分维度（1-5 分）
JUDGE_DIMENSIONS = {
    "relevance": "相关性：回答是否切中用户问题",
    "groundedness": "有据性：结论是否仅基于工具返回的数据，未编造价格/指标等具体数值",
    "clarity": "清晰度：表达是否简洁、结构清楚、结论明确",
}

JUDGE_SYSTEM_PROMPT = """你是严格的 AI 投资助手回答评审员。

给你：用户问题、助手可用的工具返回数据、助手的最终回答。
请按以下维度打 1-5 分（5 最好）：
- relevance（相关性）：回答是否切中用户问题
- groundedness（有据性）：结论是否仅基于工具返回的数据；出现工具数据里没有的具体价格、
  指标数值即视为编造，最多 2 分；工具失败时如实说明应得高分
- clarity（清晰度）：表达是否简洁、结构清楚、给出明确观点

只输出 JSON，不要任何其他文字：
{"relevance": 1-5, "groundedness": 1-5, "clarity": 1-5, "comment": "一句话点评"}"""


@dataclass
class JudgeConfig:
    """judge 模型配置（固定模型 + 低温）。"""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "JudgeConfig | None":
        """从环境变量读取；不全则返回 None（judge 环节跳过）。"""
        base_url = os.environ.get("EVAL_JUDGE_BASE_URL", "").strip()
        api_key = os.environ.get("EVAL_JUDGE_API_KEY", "").strip()
        model = os.environ.get("EVAL_JUDGE_MODEL", "").strip()
        if not (base_url and api_key and model):
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


@dataclass
class JudgeScore:
    relevance: int
    groundedness: int
    clarity: int
    comment: str = ""

    @property
    def mean(self) -> float:
        return (self.relevance + self.groundedness + self.clarity) / 3


class LLMJudge:
    """调用固定 judge 模型对回答打分。client 可注入（测试用 mock）。"""

    def __init__(self, config: JudgeConfig, client=None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            from src.core.ai_client import AIClient

            self.client = AIClient(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )

    async def judge(self, question: str, tool_results: list[str], answer: str) -> JudgeScore:
        """对一条 (问题, 工具数据, 回答) 打分。"""
        tool_block = "\n\n".join(tool_results) if tool_results else "（本轮未调用工具）"
        user_content = (
            f"## 用户问题\n{question}\n\n"
            f"## 工具返回数据\n{tool_block}\n\n"
            f"## 助手回答\n{answer}"
        )
        raw = await self.client.chat(
            JUDGE_SYSTEM_PROMPT, user_content, temperature=self.config.temperature
        )
        return self.parse_score(raw)

    @staticmethod
    def parse_score(raw: str) -> JudgeScore:
        """解析 judge 输出（容忍 ```json 代码围栏），非法输出抛 ValueError。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"judge 输出不是合法 JSON: {raw[:200]!r}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"judge 输出不是 JSON 对象: {raw[:200]!r}")

        def clamp(key: str) -> int:
            try:
                return max(1, min(5, int(obj.get(key))))
            except (TypeError, ValueError) as e:
                raise ValueError(f"judge 输出缺少/非法维度 {key}: {obj!r}") from e

        return JudgeScore(
            relevance=clamp("relevance"),
            groundedness=clamp("groundedness"),
            clarity=clamp("clarity"),
            comment=str(obj.get("comment") or ""),
        )
