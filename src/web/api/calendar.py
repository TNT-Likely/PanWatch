"""事件日历 API: 宏观财经日历(百度财经日历, 经 FTShare MCP)。

数据源: FTShare MCP `ft_baidu_financial_calendar`
覆盖: 经济数据 / IPO / 财报披露时间 / 交易提醒
粒度: 按日期范围查询(跨度 ≤3 天), 市场级, 不绑定个股。

数据源注册在 data_sources 表(type=macro_calendar, provider=ftshare),
本接口直接调用 marketdata 包的 ftshare vendor, 不进 MarketData Engine
(宏观日历是市场级, 与个股 symbol 模型不同)。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


def _default_range() -> tuple[str, str]:
    """默认范围: 今天 ~ 今天+3 天(ftshare 跨度上限 3 天)。"""
    today = date.today()
    return today.strftime("%Y-%m-%d"), (today + timedelta(days=3)).strftime("%Y-%m-%d")


@router.get("/events")
async def calendar_events(
    start_date: str | None = Query(None, description="开始日期 YYYY-MM-DD, 默认今天"),
    end_date: str | None = Query(None, description="结束日期 YYYY-MM-DD, 默认今天+3天"),
    category: str | None = Query(
        None,
        description="economic / ipo / report_time / trade_reminder, 缺省全部",
    ),
    region: str | None = Query(None, description="地区过滤(如 中国/美国), 缺省全部"),
    min_star: int = Query(0, description="最小重要性(1-3), 0=不过滤"),
):
    """查询宏观财经日历事件。"""
    if not start_date or not end_date:
        start_date, end_date = _default_range()

    # 跨度校验(ftshare 限制 ≤3 天)
    try:
        d0 = date.fromisoformat(start_date)
        d1 = date.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(400, "日期格式应为 YYYY-MM-DD")
    if (d1 - d0).days < 0:
        raise HTTPException(400, "end_date 不能早于 start_date")
    if (d1 - d0).days > 3:
        # 自动截断到 3 天窗口(避免上游拒绝)
        end_date = (d0 + timedelta(days=3)).strftime("%Y-%m-%d")

    try:
        from marketdata.vendors.ftshare import fetch_financial_calendar

        rows = fetch_financial_calendar(
            start_date, end_date, category=category, config=None
        )
    except Exception as e:
        logger.warning(f"百度财经日历取数失败: {e}")
        raise HTTPException(502, f"数据源调用失败: {e}")

    if region:
        rows = [r for r in rows if r.get("region") == region]
    if min_star:
        rows = [r for r in rows if (r.get("star") or 0) >= min_star]

    # 按日期+时间排序
    rows.sort(key=lambda r: (r.get("stat_date", ""), r.get("time", "")))
    return {
        "start_date": start_date,
        "end_date": end_date,
        "category": category,
        "region": region,
        "total": len(rows),
        "items": rows,
    }
