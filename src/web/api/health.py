"""系统自检 API。"""

from fastapi import APIRouter, Query

from src.modules.administration.selfcheck import list_selfcheck_items, run_selfcheck

router = APIRouter()


@router.get("/selfcheck")
async def selfcheck(
    notify_send: bool = Query(False, description="是否真实发送通知测试(默认只校验配置不发送)"),
    list_only: bool = Query(False, alias="list", description="只列出待检项不探测(前端先渲染列表)"),
    keys: str | None = Query(None, description="逗号分隔,只探测这些 key(前端逐项更新进度)"),
):
    """一键体检 数据源 / AI / 通知。

    - `?list=1`:只返回待检项身份 `{items:[{category,key,name}]}`,不探测。
    - `?keys=ds:1,ai:2`:只探测这些项(逐项进度)。
    - 无参:探测全部。
    """
    if list_only:
        return {"items": list_selfcheck_items()}
    key_list = [k for k in keys.split(",") if k] if keys else None
    return await run_selfcheck(notify_send=notify_send, keys=key_list)
