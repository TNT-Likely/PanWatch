"""PanWatch business adapters for the framework-free PanAgent runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from pan_agent import RunRequest, ToolRegistry, ToolResult, ToolRisk, ToolSpec
from sqlalchemy.orm import Session

from src.modules.market.price_alert_service import (
    compact_alert_rule,
    create_alert_rule,
    delete_alert_rule,
    get_alert_rule,
    list_alert_rules,
    update_alert_rule,
)
from src.modules.portfolio import build_portfolio_service
from src.modules.strategy.strategy_engine import list_strategy_signals
from src.platform.marketdata.collectors.kline_collector import KlineCollector
from src.platform.marketdata.marketdata_client import md_news, md_quote_rows
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.models import Stock


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


def _optional_market(arguments: dict[str, Any]) -> MarketCode | None:
    """Parse an optional market filter without forcing queries to one market."""
    raw = str(arguments.get("market") or "").strip().upper()
    if not raw:
        return None
    try:
        return MarketCode(raw)
    except ValueError:
        return None


def _alert_condition_summary(item: dict[str, Any]) -> str:
    direction = "≥" if item.get("direction") == "above" else "≤"
    target = item.get("target_price")
    if target is None:
        return "条件未知"
    return f"价格 {direction} {target:g}"


def _published_at(value: object) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _format_candidate_price(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _compact_research_candidate(item: dict[str, Any]) -> dict[str, Any]:
    entry_low = item.get("entry_low")
    entry_high = item.get("entry_high")
    if entry_low is not None or entry_high is not None:
        entry_range = f"{_format_candidate_price(entry_low) or '--'} ~ {_format_candidate_price(entry_high) or '--'}"
    else:
        entry_range = ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
    quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return {
        "symbol": str(item.get("stock_symbol") or ""),
        "market": str(item.get("stock_market") or "CN"),
        "name": str(item.get("stock_name") or item.get("stock_symbol") or ""),
        "score": item.get("rank_score", item.get("score")),
        "action": item.get("action_label") or item.get("action") or "观望",
        "risk": item.get("risk_level_label") or item.get("risk_level") or "未知",
        "source": item.get("source_pool_label") or item.get("source_pool") or "未知",
        "signal": item.get("signal") or "",
        "reason": item.get("reason") or "",
        "entry_range": entry_range,
        "target_price": item.get("target_price"),
        "stop_loss": item.get("stop_loss"),
        "invalidation": item.get("invalidation") or "",
        "current_price": quote.get("current_price"),
        "change_pct": quote.get("change_pct"),
    }


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

    async def find_research_candidates(_request: RunRequest, arguments: dict) -> ToolResult:
        market = str(arguments.get("market") or "").strip().upper()
        holding = str(arguments.get("holding") or "unheld").strip().lower()
        risk_level = str(arguments.get("risk_level") or "").strip().lower()
        try:
            min_score = float(arguments.get("min_score", 70))
            limit = int(arguments.get("limit", 5))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="机会筛选参数无效。",
                error_code="candidate_filter_invalid",
            )
        if (
            (market and market not in {"CN", "HK", "US"})
            or holding not in {"all", "held", "unheld"}
            or (risk_level and risk_level not in {"all", "low", "medium", "high"})
            or not 0 <= min_score <= 100
            or not 1 <= limit <= 10
        ):
            return ToolResult.failure(
                summary="机会筛选参数无效。",
                error_code="candidate_filter_invalid",
            )

        try:
            result = await asyncio.to_thread(
                list_strategy_signals,
                market=market,
                status="active",
                min_score=min_score,
                limit=limit,
                source_pool="all",
                holding=holding,
                risk_level=risk_level,
                include_payload=True,
            )
        except Exception:  # noqa: BLE001 - provider/database failures become controlled tool results
            return ToolResult.failure(
                summary="机会数据暂时不可用。",
                error_code="candidate_data_unavailable",
            )

        items = [_compact_research_candidate(item) for item in result.get("items", [])]
        names = "、".join(item["name"] for item in items[:3])
        summary = (
            f"找到 {len(items)} 个研究候选：{names}。"
            if items
            else "暂无符合条件的研究候选。"
        )
        return ToolResult.success(
            summary=summary,
            data={
                "snapshot_date": result.get("snapshot_date") or "",
                "count": len(items),
                "items": items,
            },
            sources=[{"name": "PanWatch 机会信号"}],
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

    async def _find_or_register_stock(
        symbol: str, market: MarketCode
    ) -> tuple[Stock | None, bool]:
        """Resolve a stock id for write tools without requiring watchlist setup.

        Price-alert rules reference the local ``stocks`` table, while research
        tools can operate on any symbol returned by the market-data providers.
        A verified quote is enough to create the lightweight stock directory
        record; an unverified symbol remains a controlled ``stock_not_found``
        result and never produces a dangling alert rule.
        """
        stock = (
            session.query(Stock)
            .filter(Stock.symbol == symbol, Stock.market == market.value)
            .first()
        )
        if stock is not None:
            return stock, False

        try:
            rows = await asyncio.to_thread(md_quote_rows, [symbol], market.value)
        except Exception:  # noqa: BLE001 - quote failures become a controlled write failure
            return None, False

        def matches(row: dict[str, Any]) -> bool:
            row_symbol = str(row.get("symbol") or "").strip().upper()
            if market is MarketCode.HK and row_symbol.isdigit():
                row_symbol = row_symbol.zfill(5)
            return row_symbol == symbol

        quote = next((row for row in rows if matches(row)), None)
        if quote is None:
            return None, False

        stock = Stock(
            symbol=symbol,
            name=str(quote.get("name") or symbol).strip() or symbol,
            market=market.value,
        )
        session.add(stock)
        # 规则通过外键引用新登记的股票；先 flush 获取主键，仍由下方
        # 的单次 commit 保证股票目录和提醒规则一起成功或一起回滚。
        session.flush()
        return stock, True

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

        stock, stock_registered = await _find_or_register_stock(symbol, market)
        if stock is None:
            return ToolResult.failure(
                summary=f"PanWatch 股票库中未找到 {market.value}:{symbol}，未创建提醒。",
                error_code="stock_not_found",
            )
        direction_label = "≥" if direction == "above" else "≤"
        display_price = f"{target_price:g}"
        name = (
            str(arguments.get("name") or "").strip()
            or f"{stock.name} 价格 {direction_label} {display_price}"
        )
        rule = create_alert_rule(
            session,
            stock_id=stock.id,
            name=name,
            enabled=True,
            condition_group={
                "op": "and",
                "items": [
                    {
                        "type": "price",
                        "op": ">=" if direction == "above" else "<=",
                        "value": target_price,
                    }
                ],
            },
            market_hours_mode="trading_only",
            cooldown_minutes=cooldown_minutes,
            max_triggers_per_day=3,
            repeat_mode="repeat",
            notify_channel_ids=[],
        )
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
                "stock_registered": stock_registered,
            },
            sources=[{"name": "PanWatch 价格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def get_price_alerts(_request: RunRequest, arguments: dict) -> ToolResult:
        """Return compact alert facts so the model can reference a rule ID."""
        symbol = str(arguments.get("symbol") or "").strip().upper() or None
        market = _optional_market(arguments)
        if arguments.get("market") and market is None:
            return ToolResult.failure(
                summary="不支持的市场代码。", error_code="market_invalid"
            )
        if symbol and market is MarketCode.HK and symbol.isdigit():
            symbol = symbol.zfill(5)
        try:
            limit = max(1, min(int(arguments.get("limit") or 20), 50))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="查询条数必须是数字。", error_code="limit_invalid"
            )
        enabled = arguments.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            return ToolResult.failure(
                summary="enabled 必须是布尔值。", error_code="enabled_invalid"
            )

        rows = list_alert_rules(
            session,
            symbol=symbol,
            market=market.value if market else None,
            enabled=enabled,
            limit=limit,
        )
        items = [compact_alert_rule(row) for row in rows]
        if not items:
            return ToolResult.success(
                summary="没有找到符合条件的价格提醒。",
                data={"count": 0, "items": []},
                sources=[{"name": "PanWatch 价格提醒"}],
                observed_at=datetime.now(UTC),
            )
        summary = "；".join(
            f"#{item['rule_id']} {item['stock_name'] or item['symbol']}（{item['market']}:{item['symbol']}，"
            f"{_alert_condition_summary(item)}，{'启用' if item['enabled'] else '停用'}）"
            for item in items[:5]
        )
        if len(items) > 5:
            summary += f"；另有 {len(items) - 5} 条"
        return ToolResult.success(
            summary=f"找到 {len(items)} 条价格提醒：{summary}",
            data={"count": len(items), "items": items},
            sources=[{"name": "PanWatch 价格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def update_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Update one rule after the runtime's human approval gate."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="请提供有效的提醒 ID。", error_code="rule_id_invalid"
            )
        updates = {
            key: arguments[key]
            for key in (
                "name",
                "enabled",
                "direction",
                "target_price",
                "cooldown_minutes",
                "max_triggers_per_day",
                "repeat_mode",
                "market_hours_mode",
                "expire_at",
            )
            if key in arguments
        }
        try:
            rule = update_alert_rule(session, rule_id, updates)
        except LookupError:
            return ToolResult.failure(
                summary=f"未找到价格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        except ValueError as exc:
            return ToolResult.failure(
                summary=str(exc), error_code="price_alert_invalid"
            )
        item = compact_alert_rule(rule)
        return ToolResult.success(
            summary=(
                f"已更新价格提醒 #{rule_id}：{item['stock_name'] or item['symbol']}，"
                f"{_alert_condition_summary(item)}，{'启用' if item['enabled'] else '停用'}。"
            ),
            data=item,
            sources=[{"name": "PanWatch 价格提醒"}],
            observed_at=datetime.now(UTC),
        )

    async def delete_price_alert(_request: RunRequest, arguments: dict) -> ToolResult:
        """Delete one rule and its hit history after human approval."""
        try:
            rule_id = int(arguments.get("rule_id"))
        except (TypeError, ValueError):
            return ToolResult.failure(
                summary="请提供有效的提醒 ID。", error_code="rule_id_invalid"
            )
        rule = get_alert_rule(session, rule_id)
        if rule is None:
            return ToolResult.failure(
                summary=f"未找到价格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        item = compact_alert_rule(rule)
        try:
            delete_alert_rule(session, rule_id)
        except LookupError:
            return ToolResult.failure(
                summary=f"未找到价格提醒 #{rule_id}。",
                error_code="price_alert_not_found",
            )
        return ToolResult.success(
            summary=f"已删除价格提醒 #{rule_id}：{item['stock_name'] or item['symbol']}。",
            data={"rule_id": rule_id, "deleted": True},
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
            name="find_research_candidates",
            title="发现研究候选",
            description="查询 PanWatch 最新机会信号，返回适合进一步研究的候选标的及其评分、风险和入场计划。只读，不会刷新策略或执行交易。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "description": "可选市场代码；不填表示全部市场",
                    },
                    "holding": {
                        "type": "string",
                        "enum": ["all", "held", "unheld"],
                        "default": "unheld",
                        "description": "持仓过滤；默认只看未持仓标的",
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["all", "low", "medium", "high"],
                        "default": "all",
                        "description": "可选风险等级过滤",
                    },
                    "min_score": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 100,
                        "default": 70,
                        "description": "最低机会分数",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 5,
                        "description": "最多返回候选数量",
                    },
                },
            },
        ),
        find_research_candidates,
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
            name="get_price_alerts",
            title="查询价格提醒",
            description="查询用户已创建的价格提醒，返回提醒 ID、标的、条件和启用状态。",
            risk=ToolRisk.READ,
            input_schema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "可选股票代码；不填则查询所有标的",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["CN", "HK", "US"],
                        "description": "可选市场代码",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "可选，仅返回启用或停用的提醒",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 20,
                    },
                },
            },
        ),
        get_price_alerts,
    )
    registry.register(
        ToolSpec(
            name="update_price_alert",
            title="修改价格提醒",
            description="修改一条价格提醒的名称、目标价、方向或启用状态，需要用户批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "查询价格提醒得到的提醒 ID",
                    },
                    "name": {"type": "string", "description": "新的提醒名称"},
                    "enabled": {"type": "boolean", "description": "是否启用"},
                    "direction": {
                        "type": "string",
                        "enum": ["above", "below"],
                        "description": "价格触发方向",
                    },
                    "target_price": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "新的目标价格",
                    },
                    "cooldown_minutes": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "触发后的冷却分钟数",
                    },
                    "max_triggers_per_day": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "每日最大触发次数，0 表示不限制",
                    },
                    "repeat_mode": {
                        "type": "string",
                        "enum": ["once", "repeat"],
                    },
                    "market_hours_mode": {
                        "type": "string",
                        "enum": ["trading_only", "always"],
                    },
                    "expire_at": {
                        "type": ["string", "null"],
                        "description": "ISO-8601 到期时间；传 null 清除到期时间",
                    },
                },
            },
        ),
        update_price_alert,
    )
    registry.register(
        ToolSpec(
            name="delete_price_alert",
            title="删除价格提醒",
            description="删除一条价格提醒及其历史命中记录，需要用户批准。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={
                "type": "object",
                "required": ["rule_id"],
                "properties": {
                    "rule_id": {
                        "type": "integer",
                        "description": "查询价格提醒得到的提醒 ID",
                    }
                },
            },
        ),
        delete_price_alert,
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
