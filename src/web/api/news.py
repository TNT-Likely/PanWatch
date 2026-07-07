"""新闻 API - 基于数据源配置"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import Stock, DataSource
from src.collectors.news_collector import NewsCollector, NewsItem

router = APIRouter()

# 来源显示名称
SOURCE_LABELS = {
    "xueqiu": "雪球",
    "eastmoney_news": "东财资讯",
    "eastmoney": "东财公告",
}


class NewsItemResponse(BaseModel):
    source: str
    source_label: str
    external_id: str
    title: str
    content: str
    publish_time: str
    symbols: list[str]
    importance: int
    url: str = ""


@router.get("", response_model=list[NewsItemResponse])
async def get_news(
    symbols: str = Query(default="", description="股票代码，逗号分隔"),
    names: str = Query(default="", description="股票名称，逗号分隔（优先使用，比 symbols 更稳定）"),
    hours: int = Query(default=168, ge=1, le=720, description="时间范围（小时，默认7天）"),
    limit: int = Query(default=50, ge=1, le=200, description="返回数量"),
    filter_related: bool = Query(default=True, description="只显示相关新闻"),
    source: str = Query(default="", description="来源过滤，逗号分隔：xueqiu/eastmoney_news/eastmoney"),
    db: Session = Depends(get_db),
):
    """
    获取新闻列表（基于数据源配置）

    - symbols: 股票代码过滤，逗号分隔，空则获取所有自选股相关新闻
    - names: 股票名称过滤，逗号分隔（前端直接传递名称，更稳定）
    - hours: 时间范围
    - limit: 返回数量限制
    - filter_related: 是否只显示与自选股相关的新闻
    """
    # 获取所有自选股（用于匹配）
    all_stocks = db.query(Stock).all()
    stock_map = {s.symbol: s.name for s in all_stocks}
    name_to_symbol = {s.name: s.symbol for s in all_stocks}

    # 解析股票 - 优先使用 names 参数
    if names:
        # 前端直接传递股票名称
        name_list = [n.strip() for n in names.split(",") if n.strip()]
        # 转换为 symbol 列表（用于匹配和返回）
        symbol_list = [name_to_symbol.get(n) for n in name_list if name_to_symbol.get(n)]
        # 直接使用传入的名称构建 symbol_names
        passed_symbol_names = {name_to_symbol.get(n, ""): n for n in name_list if name_to_symbol.get(n)}
        # 名称未匹配到数据库中的股票时，直接用名称作为搜索关键词
        if not symbol_list:
            symbol_list = name_list
            passed_symbol_names = {n: n for n in name_list}
    elif symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
        passed_symbol_names = {s: stock_map.get(s, s) for s in symbol_list}
    else:
        symbol_list = list(stock_map.keys())
        passed_symbol_names = stock_map

    if not symbol_list:
        return []

    source_filters = {s.strip() for s in source.split(",") if s.strip()} if source else set()

    # 构建匹配关键词（股票代码 + 股票名称）
    keywords = set(symbol_list)
    for sym in symbol_list:
        if sym in stock_map:
            keywords.add(stock_map[sym])

    # 基于数据源配置构建采集器，直接传递股票名称映射避免重复查库
    collector = NewsCollector.from_database()
    news_items = await collector.fetch_all(
        symbols=symbol_list,
        since_hours=hours,
        symbol_names=passed_symbol_names,  # 直接传递已有的股票名称映射
    )

    def is_related(item: NewsItem) -> bool:
        """判断新闻是否与自选股相关"""
        # 公告类天然与股票相关
        if item.source == "eastmoney":
            return True
        # 已标记相关股票
        if item.symbols and any(s in symbol_list for s in item.symbols):
            return True
        # 标题或内容包含关键词
        text = item.title + (item.content or "")
        return any(kw in text for kw in keywords)

    # 补充按来源优先级排序（兜底，若 collector 排序变更仍能保证顺序）
    source_priority = {"eastmoney_news": 3, "eastmoney": 2, "xueqiu": 1}
    news_items.sort(key=lambda x: (source_priority.get(x.source, 0), x.importance, x.publish_time), reverse=True)

    result = []
    for item in news_items:
        if source_filters and item.source not in source_filters:
            continue
        # 过滤不相关的新闻
        if filter_related and not is_related(item):
            continue

        # 标记匹配的股票
        matched_symbols = []
        text = item.title + (item.content or "")
        for sym, name in stock_map.items():
            if sym in symbol_list and (sym in text or name in text):
                matched_symbols.append(sym)

        result.append(NewsItemResponse(
            source=item.source,
            source_label=SOURCE_LABELS.get(item.source, item.source),
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            publish_time=item.publish_time.strftime("%Y-%m-%d %H:%M"),
            symbols=matched_symbols or item.symbols,
            importance=item.importance,
            url=item.url,
        ))

        if len(result) >= limit:
            break

    return result


@router.get("/sources")
def get_news_sources(db: Session = Depends(get_db)):
    """获取已配置的新闻数据源列表"""
    data_sources = (
        db.query(DataSource)
        .filter(DataSource.type == "news")
        .order_by(DataSource.priority)
        .all()
    )

    return [
        {
            "id": ds.provider,
            "name": ds.name,
            "enabled": ds.enabled,
            "priority": ds.priority,
        }
        for ds in data_sources
    ]
