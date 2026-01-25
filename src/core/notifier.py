import logging
import os
import re

import apprise
import httpx

logger = logging.getLogger(__name__)


def sanitize_for_telegram(content: str) -> str:
    """清理内容以适配 Telegram（移除 HTML 和 Markdown 格式）"""
    # 移除 HTML 标签
    content = re.sub(r"</?table[^>]*>", "", content)
    content = re.sub(r"</?thead[^>]*>", "", content)
    content = re.sub(r"</?tbody[^>]*>", "", content)
    content = re.sub(r"</?tr[^>]*>", "\n", content)
    content = re.sub(r"</?th[^>]*>", " | ", content)
    content = re.sub(r"</?td[^>]*>", " | ", content)
    content = re.sub(r"</?div[^>]*>", "", content)
    content = re.sub(r"</?span[^>]*>", "", content)
    content = re.sub(r"</?p[^>]*>", "\n", content)
    content = re.sub(r"<br\s*/?>", "\n", content)

    # 移除 Markdown 格式
    content = re.sub(r"^#{1,6}\s*", "", content, flags=re.MULTILINE)  # 移除标题 #
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)  # 移除粗体 **
    content = re.sub(r"\*(.+?)\*", r"\1", content)  # 移除斜体 *
    content = re.sub(r"__(.+?)__", r"\1", content)  # 移除粗体 __
    content = re.sub(r"_(.+?)_", r"\1", content)  # 移除斜体 _
    content = re.sub(r"~~(.+?)~~", r"\1", content)  # 移除删除线
    content = re.sub(r"`(.+?)`", r"\1", content)  # 移除行内代码
    content = re.sub(r"^\s*[-*+]\s+", "· ", content, flags=re.MULTILINE)  # 列表符号改为 ·
    content = re.sub(r"^\s*\d+\.\s+", "", content, flags=re.MULTILINE)  # 移除有序列表数字

    # 清理多余空白
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)
    content = re.sub(r" +", " ", content)
    return content.strip()

# 渠道类型定义 (label + 表单字段)
CHANNEL_TYPES = {
    "telegram": {
        "label": "Telegram",
        "fields": ["bot_token", "chat_id", "proxy"],
    },
    "bark": {
        "label": "Bark",
        "fields": ["device_key", "server_url"],
    },
    "dingtalk": {
        "label": "钉钉机器人",
        "fields": ["token", "secret"],
    },
    "wecom": {
        "label": "企业微信机器人",
        "fields": ["webhook_key"],
    },
    "lark": {
        "label": "飞书机器人",
        "fields": ["webhook_token"],
    },
    "serverchan": {
        "label": "Server酱",
        "fields": ["sendkey"],
    },
    "pushplus": {
        "label": "PushPlus",
        "fields": ["token", "topic"],
    },
    "discord": {
        "label": "Discord",
        "fields": ["webhook_id", "webhook_token"],
    },
    "pushover": {
        "label": "Pushover",
        "fields": ["user_key", "app_token"],
    },
}

# 通过 Apprise 支持的渠道类型（无代理配置时）
_APPRISE_TYPES = {"telegram", "bark", "dingtalk", "lark", "discord", "pushover"}

# 自定义实现的渠道类型（带代理或特殊需求）
_CUSTOM_IMPL_TYPES = {"wecom", "serverchan", "pushplus"}

# 支持 Markdown 的渠道（不需要 sanitize）
_MARKDOWN_CHANNELS = {"wecom", "serverchan", "pushplus", "dingtalk", "lark", "discord"}

# 不支持 Markdown 的渠道（需要 sanitize）
_PLAIN_TEXT_CHANNELS = {"telegram", "bark", "pushover"}


def build_apprise_url(channel_type: str, config: dict) -> str | None:
    """
    根据渠道类型和配置构建 Apprise URL

    Returns:
        Apprise URL 或 None（如果需要使用自定义方式发送，如带代理的 Telegram）
    """
    if channel_type == "telegram":
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")
        # 如果配置了代理，返回 None，使用自定义方式发送
        proxy = config.get("proxy", "").strip()
        if proxy:
            return None
        return f"tgram://{bot_token}/{chat_id}"

    elif channel_type == "bark":
        device_key = config.get("device_key", "")
        server_url = config.get("server_url", "").strip("/")
        if not device_key:
            raise ValueError("Bark 需要 device_key")
        if server_url:
            host = server_url.replace("https://", "").replace("http://", "")
            return f"bark://{host}/{device_key}/"
        return f"bark://{device_key}/"

    elif channel_type == "dingtalk":
        token = config.get("token", "")
        secret = config.get("secret", "")
        if not token:
            raise ValueError("钉钉需要 token")
        if secret:
            return f"dingtalk://{token}/{secret}/"
        return f"dingtalk://{token}/"

    elif channel_type == "lark":
        webhook_token = config.get("webhook_token", "")
        if not webhook_token:
            raise ValueError("飞书需要 webhook_token")
        return f"lark://{webhook_token}/"

    elif channel_type == "discord":
        webhook_id = config.get("webhook_id", "")
        webhook_token = config.get("webhook_token", "")
        if not webhook_id or not webhook_token:
            raise ValueError("Discord 需要 webhook_id 和 webhook_token")
        return f"discord://{webhook_id}/{webhook_token}/"

    elif channel_type == "pushover":
        user_key = config.get("user_key", "")
        app_token = config.get("app_token", "")
        if not user_key or not app_token:
            raise ValueError("Pushover 需要 user_key 和 app_token")
        return f"pover://{user_key}@{app_token}/"

    else:
        raise ValueError(f"不支持的 Apprise 渠道类型: {channel_type}")


class NotifierManager:
    """通知管理器: Apprise 渠道 + 自定义渠道"""

    def __init__(self):
        self._ap = apprise.Apprise()
        self._custom_channels: list[tuple[str, dict]] = []
        self._channel_count = 0

    def add_channel(self, channel_type: str, config: dict):
        """添加通知渠道"""
        try:
            if channel_type in _APPRISE_TYPES:
                url = build_apprise_url(channel_type, config)
                if url is None:
                    # 需要自定义实现（如带代理的 Telegram）
                    self._custom_channels.append((channel_type, config))
                    self._channel_count += 1
                    logger.info(f"注册自定义通知渠道: {channel_type} (带代理)")
                elif self._ap.add(url):
                    self._channel_count += 1
                    logger.info(f"注册通知渠道: {channel_type}")
                else:
                    logger.error(f"注册通知渠道失败: {channel_type} (URL 无效)")
            else:
                self._custom_channels.append((channel_type, config))
                self._channel_count += 1
                logger.info(f"注册自定义通知渠道: {channel_type}")
        except ValueError as e:
            logger.error(f"注册通知渠道失败: {e}")

    async def notify(self, title: str, content: str, images: list[str] | None = None):
        """向所有已注册渠道发送通知（忽略错误）"""
        await self.notify_with_result(title, content, images)

    async def notify_with_result(self, title: str, content: str, images: list[str] | None = None) -> dict:
        """向所有已注册渠道发送通知，返回结果"""
        if self._channel_count == 0:
            logger.warning("没有可用的通知渠道")
            return {"success": False, "error": "没有可用的通知渠道"}

        # 准备纯文本版本（用于不支持 Markdown 的渠道）
        plain_content = sanitize_for_telegram(content)

        # 准备附件
        attachments = None
        if images:
            attachments = apprise.AppriseAttachment()
            for img_path in images:
                if img_path and os.path.exists(img_path):
                    attachments.add(img_path)

        errors = []

        # Apprise 渠道（使用纯文本，因为 Telegram 等不支持 Markdown）
        if len(self._ap) > 0:
            try:
                success = await self._ap.async_notify(
                    title=title,
                    body=plain_content,
                    body_format=apprise.NotifyFormat.TEXT,
                    attach=attachments,
                )
                if success:
                    logger.info(f"Apprise 通知发送成功: {title}")
                else:
                    error_msg = "Apprise 通知发送失败（可能是网络问题或配置错误）"
                    logger.error(f"{error_msg}: {title}")
                    errors.append(error_msg)
            except Exception as e:
                error_msg = f"Apprise 通知异常: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        # 自定义渠道（根据渠道类型自动选择格式）
        for ch_type, config in self._custom_channels:
            try:
                # 支持 Markdown 的渠道使用原始内容，否则使用纯文本
                ch_content = content if ch_type in _MARKDOWN_CHANNELS else plain_content
                await self._send_custom(ch_type, config, title, ch_content)
            except Exception as e:
                error_msg = f"{ch_type} 发送失败: {e}"
                logger.error(error_msg)
                errors.append(error_msg)

        if errors:
            return {"success": False, "error": "; ".join(errors)}
        return {"success": True}

    async def _send_custom(self, ch_type: str, config: dict, title: str, content: str):
        """发送自定义渠道通知"""
        if ch_type == "telegram":
            await self._send_telegram(config, title, content)
        elif ch_type == "wecom":
            await self._send_wecom(config, title, content)
        elif ch_type == "serverchan":
            await self._send_serverchan(config, title, content)
        elif ch_type == "pushplus":
            await self._send_pushplus(config, title, content)
        else:
            logger.warning(f"未知的自定义渠道类型: {ch_type}")

    async def _send_telegram(self, config: dict, title: str, content: str):
        """Telegram Bot API（支持代理）"""
        bot_token = config.get("bot_token", "")
        chat_id = config.get("chat_id", "")
        proxy = config.get("proxy", "").strip()

        if not bot_token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        text = f"*{title}*\n\n{content}" if title else content
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        # 配置代理
        transport = None
        if proxy:
            transport = httpx.AsyncHTTPTransport(proxy=proxy)
            logger.debug(f"Telegram 使用代理: {proxy}")

        try:
            async with httpx.AsyncClient(transport=transport, timeout=30) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if not data.get("ok"):
                    raise RuntimeError(f"Telegram API 错误: {data.get('description')}")
                logger.info(f"Telegram 通知发送成功: {title}")
        except httpx.ConnectError as e:
            if proxy:
                raise RuntimeError(f"连接代理失败 ({proxy}): {e}")
            else:
                raise RuntimeError(f"无法连接 Telegram API（可能需要配置代理）: {e}")
        except httpx.TimeoutException:
            raise RuntimeError("请求超时（网络问题或代理配置错误）")
        except Exception as e:
            if "ConnectError" in str(type(e).__name__) or "connection" in str(e).lower():
                if not proxy:
                    raise RuntimeError(f"网络连接失败，建议配置代理: {e}")
            raise

    async def _send_wecom(self, config: dict, title: str, content: str):
        """企业微信机器人 Webhook"""
        key = config.get("webhook_key", "")
        if not key:
            raise ValueError("企业微信需要 webhook_key")

        url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={key}"
        text = f"## {title}\n\n{content}" if title else content
        payload = {"msgtype": "markdown", "markdown": {"content": text}}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"企业微信发送失败: {data.get('errmsg')}")
            logger.info(f"企业微信通知发送成功: {title}")

    async def _send_serverchan(self, config: dict, title: str, content: str):
        """Server酱推送"""
        sendkey = config.get("sendkey", "")
        if not sendkey:
            raise ValueError("Server酱需要 sendkey")

        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        payload = {"title": title or "通知", "desp": content}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Server酱发送失败: {data.get('message')}")
            logger.info(f"Server酱通知发送成功: {title}")

    async def _send_pushplus(self, config: dict, title: str, content: str):
        """PushPlus 推送"""
        token = config.get("token", "")
        if not token:
            raise ValueError("PushPlus 需要 token")

        url = "https://www.pushplus.plus/send"
        payload = {
            "token": token,
            "title": title or "通知",
            "content": content,
            "template": "markdown",
        }
        topic = config.get("topic", "")
        if topic:
            payload["topic"] = topic

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=30)
            data = resp.json()
            if data.get("code") != 200:
                raise RuntimeError(f"PushPlus 发送失败: {data.get('msg')}")
            logger.info(f"PushPlus 通知发送成功: {title}")
