"""PanWatch business adapters for the framework-free PanAgent runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pan_agent import RunRequest, ToolRegistry, ToolResult, ToolRisk, ToolSpec
from sqlalchemy.orm import Session

from src.modules.portfolio import build_portfolio_service
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.marketdata_client import md_news, md_quote_rows
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import PriceAlertRule, Stock


def _symbol_and_market(arguments: dict[str, Any]) -> tuple[str, MarketCode] | None:
    """Validate the small symbol contract shared by all market tools."""
    symbol = str(arguments.get("symbol") or "").strip().upper()
    try:
        market = MarketCode(str(arguments.get("market") or "CN").strip().upper())
    except ValueError:
        return None
    return (symbol, market) if symbol else None


def _failure_for_symbol(arguments: dict[str, Any]) -> ToolResult:
    if not str(arguments.get("symbol") or "").strip():
        return ToolResult.failure(
            summary="请提供要查询的股票代码。", error_code="symbol_required"
        )
    return ToolResult.failure(summary="不支持的市场代码。", error_code="market_invalid")


def _published_at(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def build_panwatch_tool_registry(session: Session) -> ToolRegistry:
    """Register the host-owned market and portfolio tools for an assistant run."""
    registry = ToolRegistry()
    portfolio_service = build_portfolio_service(session)

    async def get_portfolio(_request: RunRequest, _arguments: dict) -> ToolResult:
        summary = portfolio_service.build_assistant_summary() or "用户暂无持仓。"
        return ToolResult.success(
            summary=summary,
            data={"has_positions": summary != "用户暂无持仓。"},
            sources=[{"name": "PanWatch 持仓"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_quote(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - provider failures become controlled tool results
            return ToolResult.failure(
                summary="行情数据暂时不可用。", error_code="quote_unavailable"
            )
        quote = next(
            (row for row in rows if str(row.get("symbol") or "") == symbol), None
        )
        if quote is None:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的行情。",
                error_code="quote_unavailable",
            )
        data = {
            key: quote.get(key)
            for key in (
                "symbol",
                "name",
                "market",
                "current_price",
                "change_pct",
                "change_amount",
                "prev_close",
                "open_price",
                "high_price",
                "low_price",
                "volume",
                "turnover",
                "turnover_rate",
                "pe_ratio",
                "total_market_value",
                "circulating_market_value",
            )
        }
        name = data.get("name") or symbol
        return ToolResult.success(
            summary=(
                f"{name}（{market.value}:{symbol}）最新价 {data.get('current_price')}，"
                f"涨跌幅 {data.get('change_pct')}%。"
            ),
            data=data,
            sources=[{"name": "PanWatch 行情数据"}],
            observed_at=datetime.now(UTC),
        )

    async def get_kline_summary(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            summary = await asyncio.to_thread(
                KlineCollector(market).get_kline_summary, symbol
            )
        except Exception:  # noqa: BLE001 - source issues must not abort an agent run
            return ToolResult.failure(
                summary="K 线数据暂时不可用。", error_code="kline_unavailable"
            )
        if not isinstance(summary, dict) or not summary:
            return ToolResult.failure(
                summary=f"未找到 {market.value}:{symbol} 的 K 线摘要。",
                error_code="kline_unavailable",
            )
        return ToolResult.success(
            summary=f"{market.value}:{symbol} 的 K 线摘要已就绪：{summary}",
            data=summary,
            sources=[{"name": "PanWatch K 线数据"}],
            observed_at=datetime.now(UTC),
        )

    async def get_stock_news(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        try:
            limit = max(1, min(int(arguments.get("limit") or 5), 10))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="新闻条数必须是数字。", error_code="limit_invalid"
            )
        try:
            articles = await asyncio.to_thread(
                md_news, [symbol], since_hours=168, names=None
            )
        except Exception:  # noqa: BLE001 - data-source failures stay within the tool result
            return ToolResult.failure(
                summary="新闻数据暂时不可用。", error_code="news_unavailable"
            )
        items = [
            {
                "title": str(getattr(article, "title", "") or ""),
                "source": str(getattr(article, "source", "") or ""),
                "published_at": _published_at(getattr(article, "publish_time", None)),
                "url": str(getattr(article, "url", "") or ""),
                "importance": int(getattr(article, "importance", 0) or 0),
            }
            for article in articles[:limit]
        ]
        return ToolResult.success(
            summary=f"{market.value}:{symbol} 近 7 天相关新闻 {len(items)} 条。",
            data={"symbol": symbol, "market": market.value, "items": items},
            sources=[{"name": "PanWatch 新闻数据"}],
            observed_at=datetime.now(UTC),
        )

    async def create_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        parsed = _symbol_and_market(arguments)
        if parsed is None:
            return _failure_for_symbol(arguments)
        symbol, market = parsed
        direction = str(arguments.get("direction") or "").strip().lower()
        if direction not in {"above", "below"}:
            return ToolResult.failure(
                summary="提醒方向只能是 above 或 below。",
                error_code="direction_invalid",
            )
        try:
            target_price = float(arguments.get("target_price"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="提醒价格必须是大于零的数字。",
                error_code="target_price_invalid",
            )
        if target_price <= 0:
            return ToolResult.failure(
                summary="提醒价格必须大于零。", error_code="target_price_invalid"
            )
        try:
            cooldown_minutes = max(0, int(arguments.get("cooldown_minutes") or 30))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="冷却时间必须是非负整数。", error_code="cooldown_invalid"
            )

        stock = (
            session.query(Stock)
            .filter(Stock.symbol == symbol, Stock.market == market.value)
            .first()
        )
        if stock is None:
            return ToolResult.failure(
                summary=f"PanWatch 股票库中未找到 {market.value}:{symbol}，未创建提醒。",
                error_code="stock_not_found",
            )
        operator = ">=" if direction == "above" else "<="
        direction_label = "≥" if direction == "above" else "≤"
        display_price = f"{target_price:g}"
        name = (
            str(arguments.get("name") or "").strip()
            or f"{stock.name} 价格 {direction_label} {display_price}"
        )
        rule = PriceAlertRule(
            stock_id=stock.id,
            name=name,
            enabled=True,
            condition_group={
                "op": "and",
                "items": [{"type": "price", "op": operator, "value": target_price}],
            },
            market_hours_mode="trading_only",
            cooldown_minutes=cooldown_minutes,
            max_triggers_per_day=3,
            repeat_mode="repeat",
            notify_channel_ids=[],
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        return ToolResult.success(
            summary=(
                f"已为 {stock.name}（{market.value}:{symbol}）创建价格 {direction_label} {display_price} "
                f"的盘中提醒，冷却 {cooldown_minutes} 分钟。"
            ),
            data={
                "rule_id": rule.id,
                "symbol": symbol,
                "market": market.value,
                "direction": direction,
                "target_price": target_price,
            },
            sources=[{"name": "PanWatch 价格提醒"}],
            observed_at=datetime.now(UTC),
        )

    registry.register(
        ToolSpec(
            name="get_portfolio",
            title="查询持仓",
            description="查询用户的实盘和模拟盘持仓摘要。",
            risk=ToolRisk.READ,
            input_schema={"type": "object", "properties": {}},
        ),
        get_portfolio,
    )
    registry.register(
        ToolSpec(
            name="get_stock_quote",
            title="查询实时行情",
            description="查询一只股票的最新价、涨跌幅和日内交易数据。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市场代码",
                    },
                },
            },
        ),
        get_stock_quote,
    )
    registry.register(
        ToolSpec(
            name="get_kline_summary",
            title="分析 K 线走势",
            description="获取一只股票的均线、动量和近期 K 线指标摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市场代码",
                    },
                },
            },
        ),
        get_kline_summary,
    )
    registry.register(
        ToolSpec(
            name="get_stock_news",
            title="检索股票新闻",
            description="检索一只股票最近七天的相关新闻并返回精简摘要。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "required": ["symbol"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市场代码",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                    },
                },
            },
        ),
        get_stock_news,
    )
    registry.register(
        ToolSpec(
            name="create_price_alert",
            title="创建价格提醒",
            description="为已收录的股票创建盘中价格提醒，需要用户批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["symbol", "direction", "target_price"],
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "股票代码，例如 600519",
                    },
                    "market": {
                        "type": "string",
                        "default": "CN",
                        "description": "市场代码",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "价格向上或向下触及目标价",
                    },
                    "target_price": {"type": "number", "exclusiveMinimum": 0},
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 30,
                    },
                    "name": {"type": "string", "description": "可选的提醒名称"},
                },
            },
        ),
        create_price_alert,
    )
    return registry
