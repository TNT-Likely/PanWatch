import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI

from src.core import otel

logger = logging.getLogger(__name__)


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
            # OTel gen_ai span(默认关闭时为 no-op);token 用量在拿到 usage 后回填。
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                # 记录 token 用量
                if response.usage:
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
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
        temperature: float | None = 0.4,
    ) -> str:
        """
        多轮对话：传入完整 messages 列表。

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: 生成温度；传 None 时不下发该参数
                （用于 failover 对"参数不兼容"错误的摘参重试）
        """
        try:
            create_kwargs: dict = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
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
        temperature: float | None = 0.4,
    ):
        """带 tool use 的对话调用，返回原始 message 对象。

        temperature 传 None 时不下发该参数（供 failover 摘参重试）。
        """
        try:
            create_kwargs: dict = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool use 调用失败: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
    ):
        """流式对话通道（stream=True），支持可选 tool use。

        异步生成器，产出二元组事件：
        - ("token", str)：增量文本片段，边生成边产出；
        - ("message", dict)：流结束后产出一次完整消息，
          形如 {"content": 全量文本, "tool_calls": [{"id", "name", "arguments"}, ...]}，
          无工具调用时 tool_calls 为空列表。

        调用方（如 chat SSE 端点）根据 tool_calls 是否为空决定继续工具循环还是结束。
        """
        create_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if tools:
            create_kwargs["tools"] = tools

        try:
            stream = await self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            logger.error(f"AI 流式调用失败: {e}")
            raise

        content_parts: list[str] = []
        # OpenAI 流式协议下 tool_calls 按 index 分片下发（arguments 逐段拼接）
        tool_calls_acc: dict[int, dict] = {}

        async for chunk in stream:
            # 部分兼容服务会在末尾单发一个只含 usage 的 chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                self.total_tokens_used += usage.total_tokens
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield ("token", delta.content)
            for tc in delta.tool_calls or []:
                acc = tool_calls_acc.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        yield (
            "message",
            {
                "content": "".join(content_parts),
                "tool_calls": [tool_calls_acc[i] for i in sorted(tool_calls_acc)],
            },
        )

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
