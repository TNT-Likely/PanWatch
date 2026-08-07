"""通达信问小达 API。

数据源: 通达信 MCP 问小达(tdx_wenda_quotes), 经 TDX MCP endpoint 直连。
覆盖: 个股行情 / 智能选股 / 板块排行 / 财务 / 技术 / 资金流向(自然语言问答)。

鉴权: 环境变量 TDX_API_KEY(Bearer token), 由容器 env 注入, 不写进代码。
数据源注册在 data_sources 表(type=wenda, provider=tdx)。
本接口直接调用 marketdata 的 tdx vendor, 不进 MarketData Engine(问小达是问答型,
返回表格, 与个股 symbol 模型不同)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/ask")
async def tdx_ask(
    q: str = Query(..., description="自然语言查询, 如 '近5日主力净流入前10的半导体'"),
    max_rows: int = Query(50, description="返回最大行数, 0=不截断"),
):
    """通达信问小达自然语言投研问答。"""
    if not q or not q.strip():
        raise HTTPException(400, "q 不能为空")

    try:
        from marketdata.vendors.tdx import ask_wenda

        result = ask_wenda(q.strip(), config=None)
    except Exception as e:
        logger.warning(f"通达信问小达调用失败: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")

    if not result:
        raise HTTPException(502, "通达信问小达无返回(可能 key 无效或服务不可用)")

    meta = result.get("meta") or {}
    if meta.get("code") not in (0, None):
        raise HTTPException(502, f"通达信问小达返回错误码: {meta.get('code')}")

    headers = result.get("headers") or []
    data = result.get("data") or []
    total = meta.get("total") or len(data)

    if max_rows and max_rows > 0 and len(data) > max_rows:
        data = data[:max_rows]

    return {
        "query": q,
        "total": total,
        "returned": len(data),
        "headers": headers,
        "rows": data,
    }
