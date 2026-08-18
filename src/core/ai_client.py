import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class TokenLimitError(Exception):
    """Token 超出模型限制时抛出的错误"""

    def __init__(self, prompt_tokens: int, max_tokens: int, context_limit: int, message: str = ""):
        self.prompt_tokens = prompt_tokens
        self.max_tokens = max_tokens
        self.context_limit = context_limit
        base_msg = (
            f"输入内容过长（约 {prompt_tokens} tokens）+ 请求输出 {max_tokens} tokens "
            f"超过模型限制 {context_limit} tokens"
        )
        if message:
            base_msg += f"，{message}"
        super().__init__(base_msg)


def _estimate_tokens(text: str) -> int:
    """保守估算文本的 token 数量（中文/混合文本）"""
    if not text:
        return 0
    # 中文/混合文本：约 1.2 个字符/tokens
    # 纯英文：约 4 个字符/tokens
    # 保守起见统一按 1.2 估算
    return max(1, int(len(text) * 1.2))


def _clamp_max_tokens(
    configured_max_tokens: int | None,
    prompt_tokens: int,
    context_limit: int,
    safety_margin: int = 512,
    minimum_output: int = 64,
) -> int:
    """根据 prompt 长度和上下文限制计算安全的 max_tokens 值

    Args:
        configured_max_tokens: 配置的输出 token 上限（None 表示不限制）
        prompt_tokens: prompt 的估算 token 数
        context_limit: 模型的上下文窗口大小
        safety_margin: 安全余量，避免 tokenizer 差异导致的溢出
        minimum_output: 最小保证的输出 token 数

    Returns:
        安全的 max_tokens 值
    """
    if prompt_tokens >= context_limit - minimum_output - safety_margin:
        raise TokenLimitError(prompt_tokens, 0, context_limit,
                               "请减少股票数量、新闻数量或历史K线范围后重试")

    available = context_limit - prompt_tokens - safety_margin
    if configured_max_tokens is not None:
        return max(minimum_output, min(configured_max_tokens, available))
    return available


class AIClient:
    """OpenAI 协议兼容的 AI 客户端"""

    def __init__(self, base_url: str, api_key: str, model: str = "", proxy: str = ""):
        kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }
        if proxy:
            kwargs["http_client"] = None  # TODO: 如需代理，用 httpx 配置
        self.client = AsyncOpenAI(**kwargs)
        # 保留原始配置作为实例属性,供需要桥接到第三方 LLM 框架的 agent 使用
        # (e.g. TradingAgents 需要 base_url+api_key 重新构造 langchain 的 LLM)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.total_tokens_used = 0

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        """
        调用 LLM 获取文本回复。

        Args:
            system_prompt: 系统提示词
            user_content: 用户输入内容
            images: 图片路径列表（用于多模态，可选）
            temperature: 生成温度
        """
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 构建 user message
        if images:
            content_parts = [{"type": "text", "text": user_content}]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_data}"}
                    })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        try:
            create_kwargs = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            response = await self.client.chat.completions.create(**create_kwargs)
            # 记录 token 用量
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
                logger.debug(
                    f"Token usage: {response.usage.prompt_tokens} + "
                    f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI 调用失败: {e}")
            raise

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float = 0.4,
    ) -> str:
        """
        多轮对话：传入完整 messages 列表。

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: 生成温度
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
                logger.debug(
                    f"Token usage: {response.usage.prompt_tokens} + "
                    f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"AI 多轮对话调用失败: {e}")
            raise

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.4,
    ):
        """带 tool use 的对话调用，返回原始 message 对象。"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                temperature=temperature,
            )
            if response.usage:
                self.total_tokens_used += response.usage.total_tokens
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool use 调用失败: {e}")
            raise

    async def list_models(self) -> list[str]:
        """通过 OpenAI 兼容的 /v1/models 拉取可用模型 id 列表。"""
        resp = await self.client.models.list()
        return sorted(m.id for m in resp.data)

    def _encode_image(self, image_path: str) -> str | None:
        """将图片文件编码为 base64"""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"图片不存在: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
