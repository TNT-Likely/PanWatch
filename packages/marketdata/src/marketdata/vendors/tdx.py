"""通达信问小达 MCP 客户端(HTTP 直连,免 Hermes)。

通过 MCP JSON-RPC 协议调用 tdx 问小达工具(实际工具名 tdx_screener, 参数 message),
为 PanWatch 提供通达信独家数据: 个股行情/智能选股/板块排行/财务/技术/资金流向。

配置优先级: 显式参数 > DB(AppSettings.tdx_api_key) > 环境变量 TDX_API_KEY。
MCP endpoint: https://txmcp.tdx.com.cn:3001/txmcp
鉴权: Authorization: Bearer <key>, Accept: application/json, text/event-stream
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://txmcp.tdx.com.cn:3001/txmcp"
_PROTOCOL_VERSION = "2024-11-05"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_sse(raw: str) -> dict | None:
    """解析 MCP SSE / JSON 响应。支持 text/event-stream 与纯 JSON 两种。"""
    if not raw:
        return None
    raw = raw.strip()
    if "data:" in raw:
        for line in reversed(raw.splitlines()):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[len("data:"):].strip()
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _load_token() -> str:
    """从 PanWatch AppSettings 表读取 tdx_api_key(DB 优先于环境变量)。

    用 lazy import 避免与 src.web 循环依赖; 读取失败(非 web 环境)返回空,
    由调用方回退到环境变量。
    """
    try:
        from src.web.database import SessionLocal
        from src.web.models import AppSettings

        db = SessionLocal()
        try:
            row = (
                db.query(AppSettings)
                .filter(AppSettings.key == "tdx_api_key")
                .first()
            )
            return (row.value or "").strip() if row else ""
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"从 DB 读取 tdx_api_key 失败, 回退环境变量: {e}")
        return ""


class TdxMCPClient:
    """tdx 问小达 MCP 客户端: initialize + tools/call。

    配置优先级: 显式参数 > DB(AppSettings.tdx_api_key) > 环境变量 TDX_API_KEY。
    """

    _lock = threading.Lock()
    _session_id: str | None = None

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = url or os.getenv("TDX_MCP_URL") or _DEFAULT_URL
        # token 优先级: 显式参数 > DB(AppSettings.tdx_api_key) > 环境变量 TDX_API_KEY
        self.token = token or _load_token() or os.getenv("TDX_API_KEY") or ""
        self._headers = dict(_HEADERS)
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"

    def _post(self, body: dict, headers: dict | None = None) -> dict | None:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers or dict(self._headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid and not self._session_id:
                    with self._lock:
                        if not self._session_id:
                            self._session_id = sid
                return _parse_sse(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            logger.warning(f"TDX HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            return None
        except Exception as e:
            logger.warning(f"TDX 请求失败: {type(e).__name__}: {e}")
            return None

    def _rpc(self, method: str, params: dict | None = None, _id: int = 1) -> dict | None:
        body = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = self._post(body, headers)
        if resp is None and self._session_id:
            with self._lock:
                self._session_id = None
            self.initialize()
            headers["Mcp-Session-Id"] = self._session_id or ""
            resp = self._post(body, headers)
        return resp

    def initialize(self) -> bool:
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "panwatch-marketdata", "version": "1.0"},
                },
            }
        )
        return bool(resp and resp.get("result"))

    def call_tool(self, name: str, args: dict) -> dict | None:
        """调工具, 返回完整 result(dict); 失败返回 None。

        tdx 的 tdx_screener 等工具直接返回 {meta, headers, data} 表格(dict),
        无 structuredContent/content 包裹, 故优先直接返回 result。
        """
        if not self._session_id:
            if not self.initialize():
                return None
        resp = self._rpc("tools/call", {"name": name, "arguments": args}, _id=2)
        if not resp:
            return None
        result = resp.get("result") or {}
        if result.get("isError"):
            logger.warning(f"TDX 工具 {name} 返回错误: {json.dumps(result, ensure_ascii=False)[:300]}")
            return None
        # tdx 工具直接返回结构化 dict(meta/headers/data)
        if "meta" in result or "headers" in result or "data" in result:
            return result
        sc = result.get("structuredContent")
        if sc is not None:
            return sc
        # 兜底: 部分实现把 JSON 放在 content[0].text
        content = result.get("content") or []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    return json.loads(item.get("text", ""))
                except json.JSONDecodeError:
                    continue
        return None


# 模块级单例(线程安全 + session 复用)
_client = TdxMCPClient()


def _get_client(config: dict | None = None) -> TdxMCPClient:
    global _client
    url = (config or {}).get("url")
    token = (config or {}).get("token") or (config or {}).get("api_key")
    if url and url != _client.url:
        _client = TdxMCPClient(url=url, token=token)
    elif token and token != _client.token:
        _client = TdxMCPClient(url=url or _client.url, token=token)
    return _client


def ask_wenda(question: str, *, config: dict | None = None) -> dict | None:
    """通达信问小达自然语言问答(实际工具: tdx_screener, 参数 message)。

    Args:
        question: 自然语言查询, 如 "近5日主力净流入前10的半导体"
        config: 透传 datasource config(url/token)
    Returns:
        {
          "meta": {"code": 0, "total": N, ...},
          "headers": [...],          # 列名
          "data": [[...], ...],      # 每行一个 list, 与 headers 对齐
        }
        失败返回 None
    """
    if not question or not question.strip():
        return None
    # 多 key 池化: config 带 key_pool 时, 用 KeyPool 按健康度选 key 注入 client
    key_pool = (config or {}).get("key_pool") if isinstance(config, dict) else None
    if key_pool:
        from marketdata.keypool import KeyPool
        kp = KeyPool(list(key_pool))
        api_key = kp.pick()
        config = {**(config or {}), "api_key": api_key}
        try:
            result = _get_client(config).call_tool("tdx_screener", {"message": question})
            kp.mark_success(api_key)
            return result
        except Exception as e:
            rl = any(c in str(e) for c in ("401", "403", "429", "rate", "quota", "incorrect", "unauthorized"))
            kp.mark_failure(api_key, rate_limited=rl)
            # 限流/失效: 切下一个 key 重试
            if rl:
                nxt = kp.pick()
                if nxt and nxt != api_key:
                    config = {**(config or {}), "api_key": nxt}
                    kp.mark_success(nxt)
                    return _get_client(config).call_tool("tdx_screener", {"message": question})
            raise
    client = _get_client(config)
    # 真实工具名为 tdx_screener, 参数为 message(非 tdx_wenda_quotes/question)
    return client.call_tool("tdx_screener", {"message": question})
