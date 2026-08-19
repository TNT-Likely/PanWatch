"""MCP Server —— 把 chat 的 5 个只读工具暴露为 Model Context Protocol 端点。

设计选择(在报告中说明):
- **手写轻量 JSON-RPC**(Streamable HTTP 的 JSON 响应模式),不引入 mcp SDK ——
  依赖最小、测试自包含、协议表面小(只读场景仅需 initialize/tools/list/tools/call);
- 挂在**顶层 `/mcp`**(不在 `/api/` 下),绕开 ResponseWrapperMiddleware 的
  `{code,data,message}` 包装,保证 JSON-RPC 报文原样返回;
- 鉴权用**独立 PAT 体系**(pwmcp_ 前缀 + sha256 存库 + 常数时间比较 + mcp:read
  scope),与登录 JWT 分流;工具全只读,天然安全;每次调用落审计日志。

工具实现直接复用 chat.py 的 CHAT_TOOLS(schema)与 _execute_tool(dispatch),
不重写业务逻辑。
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.core.pat import (
    SCOPE_MCP_READ,
    hash_token,
    looks_like_pat,
    verify_pat_hash,
)
from src.web.api.chat import CHAT_TOOLS, _execute_tool
from src.web.database import SessionLocal, get_db
from src.web.models import MCPCallLog, PersonalAccessToken

logger = logging.getLogger(__name__)
router = APIRouter()

# 协议版本(客户端未协商时的默认值)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "PanWatch", "version": "0.1.0"}

# 只读工具白名单(复用 chat 的工具定义,新增工具自动纳入)
READ_TOOL_NAMES = {t["function"]["name"] for t in CHAT_TOOLS}

# last_used 写入节流窗口(秒),避免每次 tool call 都写库
_LAST_USED_THROTTLE_S = 60
# 审计摘要长度上限
_ARG_SUMMARY_MAX = 200
_ARG_VALUE_MAX = 40


# ──────────────── PAT 鉴权 ────────────────


def _to_utc(dt: datetime | None) -> datetime | None:
    """SQLite 存的 DateTime 是 naive,统一按 UTC 处理,避免 aware/naive 比较报错。"""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bump_last_used(db: Session, row: PersonalAccessToken, request: Request) -> None:
    now = datetime.now(timezone.utc)
    last = _to_utc(row.last_used_at)
    if last is None or (now - last).total_seconds() > _LAST_USED_THROTTLE_S:
        row.last_used_at = now.replace(tzinfo=None)
        row.last_used_ip = request.client.host if request.client else None
        db.commit()


def authenticate_pat(request: Request, db: Session) -> dict:
    """校验 Authorization: Bearer pwmcp_...，返回 PAT 元数据；失败抛 HTTPException。"""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "缺少 Bearer PAT")
    token = header[7:].strip()
    if not looks_like_pat(token):
        raise HTTPException(403, "MCP 端点需要 PAT(pwmcp_ 前缀)")

    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == hash_token(token))
        .first()
    )
    if row is None or not verify_pat_hash(token, row.token_hash):
        raise HTTPException(401, "无效的 token")
    if row.revoked_at is not None:
        raise HTTPException(401, "token 已吊销")
    exp = _to_utc(row.expires_at)
    if exp is not None and exp < datetime.now(timezone.utc):
        raise HTTPException(401, "token 已过期")

    try:
        scopes = set(json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = set()
    if SCOPE_MCP_READ not in scopes:
        raise HTTPException(403, f"PAT 缺少所需 scope: {SCOPE_MCP_READ}")

    _bump_last_used(db, row, request)
    return {
        "id": row.id,
        "prefix": row.prefix,
        "name": row.name,
        "client_ip": request.client.host if request.client else None,
    }


# ──────────────── 审计日志 ────────────────


def _summarize_args(args: dict) -> str | None:
    """脱敏摘要:结构化字段 k=v，截断，供审计调试。"""
    if not args:
        return None
    parts: list[str] = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str):
            shown = v if len(v) <= _ARG_VALUE_MAX else v[: _ARG_VALUE_MAX - 1] + "…"
        elif isinstance(v, (list, tuple)):
            shown = f"[{len(v)}]"
        elif isinstance(v, dict):
            shown = f"{{{len(v)}}}"
        else:
            shown = repr(v)
        parts.append(f"{k}={shown}")
    summary = ", ".join(parts)
    return summary[:_ARG_SUMMARY_MAX] if summary else None


def _write_call_log(
    pat: dict,
    tool_name: str,
    status: str,
    error: str | None,
    args_summary: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """落审计日志(独立 session，失败静默不影响主流程)。"""
    db = SessionLocal()
    try:
        db.add(
            MCPCallLog(
                pat_id=pat.get("id"),
                pat_prefix=pat.get("prefix"),
                tool_name=(tool_name or "")[:200],
                status=status,
                error_message=(str(error)[:500] if error else None),
                args_summary=args_summary,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
        )
        db.commit()
    except Exception:
        logger.warning("写入 MCPCallLog 失败", exc_info=True)
        db.rollback()
    finally:
        db.close()


MCP_LOG_RETENTION_DAYS = 30


def prune_mcp_logs(retention_days: int = MCP_LOG_RETENTION_DAYS) -> int:
    """清理超过保留期的 MCP 调用日志,返回删除条数(供每日调度调用)。"""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(MCPCallLog).filter(MCPCallLog.called_at < cutoff).delete()
        )
        db.commit()
        if deleted:
            logger.info("MCP 调用日志保留期清理: 删除 %d 条", deleted)
        return deleted
    except Exception:
        logger.warning("MCP 调用日志清理失败", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


# ──────────────── JSON-RPC 处理 ────────────────


def _mcp_tools() -> list[dict]:
    """CHAT_TOOLS(OpenAI function schema)→ MCP tool 列表。"""
    tools = []
    for t in CHAT_TOOLS:
        fn = t["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _rpc_result(req_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def _handle_tools_call(params: dict, db: Session, pat: dict, req_id) -> JSONResponse:
    import time

    name = params.get("name")
    args = params.get("arguments") or {}
    if not name:
        return _rpc_error(req_id, -32602, "缺少工具名 name")
    if name not in READ_TOOL_NAMES:
        _write_call_log(pat, str(name), "error", "unknown tool", None, 0, pat.get("client_ip"))
        return _rpc_error(req_id, -32602, f"未知或不允许的工具: {name}")

    start = time.perf_counter()
    err: str | None = None
    try:
        text = await _execute_tool(db, name, args if isinstance(args, dict) else {})
        is_error = text.startswith("工具执行出错")
        if is_error:
            err = text
        return _rpc_result(
            req_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    except Exception as e:  # noqa: BLE001 — 兜底,不让异常穿透协议层
        err = str(e)
        return _rpc_error(req_id, -32603, f"工具执行异常: {e}")
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _write_call_log(
            pat,
            str(name),
            "error" if err else "ok",
            err,
            _summarize_args(args if isinstance(args, dict) else {}),
            duration_ms,
            pat.get("client_ip"),
        )


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    """MCP Streamable HTTP 单端点:处理 initialize / tools/list / tools/call 等。"""
    pat = authenticate_pat(request, db)

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "JSON 解析失败")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "仅支持单条 JSON-RPC 请求")

    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    # 通知类消息(无 id)不需要响应,返回 202
    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return _rpc_result(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _mcp_tools()})
    if method == "tools/call":
        return await _handle_tools_call(params, db, pat, req_id)

    return _rpc_error(req_id, -32601, f"方法不支持: {method}")
