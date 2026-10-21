"""Session collection for the mandatory investment-research graph."""
import asyncio
import logging
from datetime import datetime

from src.modules.automation.base import AgentContext
from src.modules.automation.session_research import SessionResearchAgent
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.signals import SignalPackBuilder
from src.platform.marketdata.models import MarketCode, IndexData

logger = logging.getLogger(__name__)

_CN_INDEX_TENCENT_SYMBOLS = ["sh000001", "sz399001", "sz399006"]

def get_market_data():
    from src.platform.marketdata.marketdata_client import get_market_data as get
    return get()


class DailyReportAgent(SessionResearchAgent):
    name = "daily_report"
    display_name = "收盘复盘"
    description = "每日收盘后生成自选股日报，包含大盘概览、个股分析和明日关注"

    async def _fetch_index_for_market(self, market_code: MarketCode) -> list[IndexData]:
        """按 market 取大盘指数。

        直接走 marketdata 新包(index_quotes)。
        与旧 _get_cn_index 口径一致：仅 CN 出数，其余市场返回空 list。
        """
        if market_code != MarketCode.CN:
            return []
        items = await asyncio.to_thread(get_market_data().index_quotes, _CN_INDEX_TENCENT_SYMBOLS)
        return [
            IndexData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.CN,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                timestamp=datetime.now(),
            )
            for item in items
        ]


    async def collect(self, context: AgentContext) -> dict:
        """采集大盘指数 + 自选股结构化数据包（行情/技术/资金/新闻/持仓）"""

        all_indices: list[IndexData] = []
        markets = []
        seen = set()
        for s in context.watchlist:
            if s.market not in seen:
                seen.add(s.market)
                markets.append(s.market)

        for market_code in markets:
            try:
                indices = await self._fetch_index_for_market(market_code)
                all_indices.extend(indices)
            except Exception as e:
                logger.warning(f"获取 {market_code.value} 指数失败: {e}")

        builder = SignalPackBuilder()
        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        packs = await builder.build_for_symbols(
            symbols=sym_list,
            include_news=True,
            news_hours=72,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=7,
        )

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=24,
            extended_hours=72,
            history_days=30,
            kline_days=120,
            persist_snapshot=not getattr(context, "_research_collect", False),
        )

        if not all_indices and not any(p.quote for p in packs.values()):
            raise RuntimeError("数据采集失败：未获取到任何行情数据，请检查网络连接")

        return {
            "indices": all_indices,
            "signal_packs": packs,
            "symbol_contexts": context_pack.get("symbols", {}),
            "quality_overview": context_pack.get("quality_overview", {}),
            "timestamp": datetime.now().isoformat(),
        }
