"""Session collection for the mandatory investment-research graph."""
import asyncio
import logging
from datetime import datetime

from src.modules.automation.base import AgentContext
from src.modules.automation.session_research import SessionResearchAgent
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.signals import SignalPackBuilder
from src.platform.marketdata.models import MarketCode
from src.modules.automation.scheduling_policy import market_allowed

logger = logging.getLogger(__name__)


def is_market_trading(market):
    return market_allowed("intraday_monitor", market)

def market_label(market, language="zh-CN"):
    return {MarketCode.CN: "A-shares", MarketCode.HK: "Hong Kong market", MarketCode.US: "U.S. market"}.get(market, market.value) if language == "en-US" else {MarketCode.CN: "A股", MarketCode.HK: "港股", MarketCode.US: "美股"}.get(market, market.value)


class IntradayMonitorAgent(SessionResearchAgent):
    name = "intraday_monitor"
    display_name = "盘中监测"
    description = "交易时段实时监控持仓，AI 判断是否有值得关注的信号"

    def __init__(
        self,
        throttle_minutes: int = 30,
        bypass_throttle: bool = False,
        bypass_market_hours: bool = False,
        event_only: bool = True,
        price_alert_threshold: float = 3.0,
        volume_alert_ratio: float = 2.0,
        stop_loss_warning: float = -5.0,
        take_profit_warning: float = 10.0,
        research_graph=None,
    ):
        """
        Args:
            throttle_minutes: 同一股票通知间隔（分钟）
            bypass_throttle: 是否跳过节流（测试用）
            bypass_market_hours: 是否跳过交易时段门禁（仅手动分析场景）
            price_alert_threshold: 涨跌幅超过阈值视为价格异动（%）
            volume_alert_ratio: 量比超过阈值视为放量异动
            stop_loss_warning: 浮亏超过阈值触发止损预警（%）
            take_profit_warning: 浮盈超过阈值触发止盈提醒（%）
        """
        super().__init__(research_graph=research_graph)
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle
        self.bypass_market_hours = bypass_market_hours
        self.event_only = event_only
        self.price_alert_threshold = price_alert_threshold
        self.volume_alert_ratio = volume_alert_ratio
        self.stop_loss_warning = stop_loss_warning
        self.take_profit_warning = take_profit_warning


    async def collect(self, context: AgentContext) -> dict:
        """采集实时行情 + K线 + 历史分析"""
        if not context.watchlist:
            logger.warning("自选股列表为空，跳过盘中监测")
            return {"stocks": [], "stock_data": None}

        # SignalPack: 统一结构化输入（quote/technical/position）
        stock_config = context.watchlist[0] if context.watchlist else None
        market = stock_config.market if stock_config else MarketCode.CN
        symbol = stock_config.symbol if stock_config else ""
        name = stock_config.name if stock_config else symbol

        # 按股票所属市场做交易时段门禁（而非全局任一市场开盘）
        if not self.bypass_market_hours and not is_market_trading(market):
            msg = (
                f"The {market_label(market, context.report_language)} is closed; the run was skipped."
                if context.report_language == "en-US"
                else f"当前{market_label(market)}非交易时段，已跳过执行"
            )
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=True,
            news_hours=24,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=not getattr(context, "_research_fast", False),
            include_events=True,
            events_days=3,
        )
        pack = packs.get(symbol)

        if getattr(context, "_research_fast", False):
            # Delay historical context/fulltext work until the change gate admits this stock.
            return {"signal_pack": pack, "stock_data": pack.quote if pack else None,
                "kline_summary": pack.technical if pack else {}, "symbol_context": {},
                "timestamp": datetime.now().isoformat()}

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=6,
            extended_hours=24,
            history_days=7,
            kline_days=60,
            persist_snapshot=not getattr(context, "_research_collect", False),
        )
        symbol_context = (context_pack.get("symbols", {}) or {}).get(symbol, {})
        quality_overview = context_pack.get("quality_overview", {}) or {}

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

        return {
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "symbol_context": symbol_context,
            "quality_overview": quality_overview,
            "timestamp": datetime.now().isoformat(),
        }


    async def run_single(self, context, stock_symbol):
        from src.modules.automation.scheduling_policy import scoped_context
        targets = [s for s in context.watchlist if s.symbol == stock_symbol]
        if len({s.market for s in targets}) > 1:
            raise ValueError("run_single requires an unambiguous market-scoped symbol")
        if not targets:
            return None
        return await self.run(scoped_context(context, targets))
