"""站内消息中心 + 外发推送联动。

设计要点(踩坑固化):
- 站内写入永远不能因为外发失败而丢失 → 先落库, 再推送, 推送结果回写 push_status。
- 后台线程调用 → 自带 session 生命周期, 不复用请求 session。
- 无渠道不是错误, 是 push_status='skipped'(站内仍可见), 避免"沉默失败"。
"""

from __future__ import annotations

import asyncio
import logging

from src.web.database import SessionLocal
from src.web.models import Notification, NotifyChannel

logger = logging.getLogger(__name__)

# 哪些级别需要同时外发推送(info 只留站内, 避免手机被刷屏)
_PUSH_LEVELS = {"success", "warning", "error"}


def _build_notifier():
    """从 DB 启用渠道构建 NotifierManager, 无渠道返回 None。"""
    from src.core.notifier import NotifierManager

    db = SessionLocal()
    try:
        channels = db.query(NotifyChannel).filter(NotifyChannel.enabled.is_(True)).all()
        if not channels:
            return None
        mgr = NotifierManager()
        ok = 0
        for ch in channels:
            try:
                mgr.add_channel(ch.type, ch.config or {})
                ok += 1
            except Exception as e:
                logger.warning("[通知中心] 渠道 %s 初始化失败: %s", ch.type, e)
        return mgr if ok else None
    except Exception as e:
        logger.warning("[通知中心] 读取渠道失败: %s", e)
        return None
    finally:
        db.close()


def push_notification(
    title: str,
    body: str = "",
    *,
    category: str = "system",
    level: str = "info",
    link: str = "",
    source: str = "",
    trace_id: str = "",
    also_push: bool | None = None,
) -> int | None:
    """写一条站内通知, 并按级别决定是否外发。返回 notification id。

    绝不抛异常 —— 通知失败不能拖垮业务主流程。
    """
    nid = None
    db = SessionLocal()
    try:
        n = Notification(
            category=category,
            level=level,
            title=title[:200],
            body=(body or "")[:4000],
            link=link,
            source=source,
            trace_id=trace_id,
            push_status="pending",
        )
        db.add(n)
        db.commit()
        db.refresh(n)
        nid = n.id
    except Exception as e:
        logger.exception("[通知中心] 站内写入失败: %s", e)
        db.rollback()
        db.close()
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass

    do_push = also_push if also_push is not None else (level in _PUSH_LEVELS)
    if not do_push:
        _set_push_status(nid, "skipped", "级别不外发")
        return nid

    mgr = _build_notifier()
    if mgr is None:
        # 显式状态, 不静默 —— 前端能看到"站内已记录, 未配置外发渠道"
        _set_push_status(nid, "skipped", "未配置通知渠道")
        return nid

    try:
        result = asyncio.run(mgr.notify_with_result(title, body or title))
        if result.get("success"):
            _set_push_status(nid, "sent", "")
        else:
            _set_push_status(nid, "failed", str(result.get("error") or result.get("skipped") or "")[:400])
    except RuntimeError:
        # 已在事件循环里(异步上下文) → 交给调用方的 loop
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(_async_push(nid, mgr, title, body or title))
            _set_push_status(nid, "pending", "异步发送中")
        except Exception as e:
            _set_push_status(nid, "failed", str(e)[:400])
    except Exception as e:
        logger.warning("[通知中心] 外发失败: %s", e)
        _set_push_status(nid, "failed", str(e)[:400])
    return nid


async def push_notification_async(title: str, body: str = "", **kw) -> int | None:
    """异步上下文里安全调用(把阻塞部分丢线程池)。"""
    return await asyncio.to_thread(push_notification, title, body, **kw)


async def _async_push(nid: int, mgr, title: str, body: str) -> None:
    try:
        result = await mgr.notify_with_result(title, body)
        _set_push_status(nid, "sent" if result.get("success") else "failed",
                         str(result.get("error") or "")[:400])
    except Exception as e:
        _set_push_status(nid, "failed", str(e)[:400])


def _set_push_status(nid: int, status: str, err: str = "") -> None:
    if not nid:
        return
    db = SessionLocal()
    try:
        n = db.query(Notification).filter(Notification.id == nid).first()
        if n:
            n.push_status = status
            n.push_error = err
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ── 便捷封装: 后台任务生命周期 ─────────────────────────────────────

_CATEGORY_LINKS = {
    "agent_run": "/agents",
    "report": "/reports",
    "strategy": "/opportunities",
}


def notify_task_done(
    task_label: str,
    *,
    ok: bool,
    detail: str = "",
    category: str = "agent_run",
    source: str = "",
    trace_id: str = "",
    duration_ms: int | None = None,
    link: str = "",
) -> int | None:
    """后台任务收尾统一入口: 成功/失败都写站内, 失败额外外发。"""
    dur = f"（耗时 {duration_ms / 1000:.1f}s）" if duration_ms else ""
    if ok:
        title = f"✅ {task_label} 已完成{dur}"
        level = "success"
    else:
        title = f"❌ {task_label} 执行失败{dur}"
        level = "error"
    return push_notification(
        title,
        detail,
        category=category,
        level=level,
        link=link or _CATEGORY_LINKS.get(category, ""),
        source=source or task_label,
        trace_id=trace_id,
    )
