"""系统自检 API。"""

from fastapi import APIRouter, Query

from src.modules.administration.selfcheck import list_selfcheck_items, run_selfcheck
from src.web.errors import api_error

router = APIRouter()


@router.get("/selfcheck")
def selfcheck_list(
    list_only: bool = Query(False, alias="list", description="只列出待检项不探测"),
):
    """GET is read-only; probes can spend AI quota or send notifications."""
    if not list_only:
        raise api_error(405, "selfcheck_requires_post", "请使用 POST 执行系统自检", headers={"Allow": "POST"})
    return {"items": list_selfcheck_items()}


@router.post("/selfcheck")
async def selfcheck(
    notify_send: bool = Query(False, description="是否真实发送通知测试(默认只校验配置不发送)"),
    list_only: bool = Query(False, alias="list", description="只列出待检项不探测(前端先渲染列表)"),
    keys: str | None = Query(None, description="逗号分隔,只探测这些 key(前端逐项更新进度)"),
):
    """通过受保护的 POST 一键体检 数据源 / AI / 通知。

    - `?list=1`:只返回待检项身份 `{items:[{category,key,name}]}`,不探测。
    - `?keys=ds:1,ai:2`:只探测这些项(逐项进度)。
    - 无参:探测全部。
    """
    if list_only:
        return {"items": list_selfcheck_items()}
    key_list = [k for k in keys.split(",") if k] if keys else None
    return await run_selfcheck(notify_send=notify_send, keys=key_list)
