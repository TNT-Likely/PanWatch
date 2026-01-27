import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.akshare_collector import AkshareCollector
from src.collectors.kline_collector import KlineCollector
from src.collectors.capital_flow_collector import CapitalFlowCollector
from src.core.analysis_history import save_analysis
from src.core.suggestion_pool import save_suggestion
from src.models.market import MarketCode, StockData, IndexData

logger = logging.getLogger(__name__)

# 盘后建议类型映射
DAILY_ACTION_MAP = {
    "继续持有": {"action": "hold", "label": "继续持有"},
    "考虑加仓": {"action": "add", "label": "考虑加仓"},
    "考虑减仓": {"action": "reduce", "label": "考虑减仓"},
    "考虑止损": {"action": "sell", "label": "考虑止损"},
    "明日关注": {"action": "watch", "label": "明日关注"},
    "暂时回避": {"action": "avoid", "label": "暂时回避"},
}

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "daily_report.txt"


class DailyReportAgent(BaseAgent):
    """盘后日报 Agent"""

    name = "daily_report"
    display_name = "盘后日报"
    description = "每日收盘后生成自选股日报，包含大盘概览、个股分析和明日关注"

    async def collect(self, context: AgentContext) -> dict:
        """采集大盘指数 + 自选股行情 + 技术指标 + 资金流向"""
        all_indices: list[IndexData] = []
        all_stocks: list[StockData] = []
        technical_data: dict[str, dict] = {}
        capital_flow_data: dict[str, dict] = {}

        # 按市场分组采集
        market_symbols: dict[MarketCode, list[str]] = {}
        for stock in context.watchlist:
            market_symbols.setdefault(stock.market, []).append(stock.symbol)

        for market_code, symbols in market_symbols.items():
            # 实时行情
            collector = AkshareCollector(market_code)
            indices = await collector.get_index_data()
            all_indices.extend(indices)
            stocks = await collector.get_stock_data(symbols)
            all_stocks.extend(stocks)

            # K线和技术指标
            kline_collector = KlineCollector(market_code)
            for symbol in symbols:
                try:
                    technical_data[symbol] = kline_collector.get_kline_summary(symbol)
                except Exception as e:
                    logger.warning(f"获取 {symbol} 技术指标失败: {e}")
                    technical_data[symbol] = {"error": str(e)}

            # 资金流向（仅A股）
            if market_code == MarketCode.CN:
                flow_collector = CapitalFlowCollector(market_code)
                for symbol in symbols:
                    try:
                        capital_flow_data[symbol] = flow_collector.get_capital_flow_summary(symbol)
                    except Exception as e:
                        logger.warning(f"获取 {symbol} 资金流向失败: {e}")
                        capital_flow_data[symbol] = {"error": str(e)}

        if not all_indices and not all_stocks:
            raise RuntimeError("数据采集失败：未获取到任何行情数据，请检查网络连接")

        return {
            "indices": all_indices,
            "stocks": all_stocks,
            "technical": technical_data,
            "capital_flow": capital_flow_data,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建日报 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 辅助函数：安全获取数值，None 转为默认值
        def safe_num(value, default=0):
            return value if value is not None else default

        # 构建用户输入：结构化的市场数据
        lines = []
        lines.append(f"## 日期：{datetime.now().strftime('%Y-%m-%d')}\n")

        # 大盘指数
        lines.append("## 大盘指数")
        for idx in data["indices"]:
            change_pct = safe_num(idx.change_pct)
            direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
            lines.append(
                f"- {idx.name}: {safe_num(idx.current_price):.2f} "
                f"{direction} {change_pct:+.2f}% "
                f"成交额:{safe_num(idx.turnover)/1e8:.0f}亿"
            )

        # 自选股详情
        lines.append("\n## 自选股详情")
        watchlist_map = {s.symbol: s for s in context.watchlist}
        technical = data.get("technical", {})
        capital_flow = data.get("capital_flow", {})

        for stock in data["stocks"]:
            change_pct = safe_num(stock.change_pct)
            direction = "↑" if change_pct > 0 else "↓" if change_pct < 0 else "→"
            stock_name = stock.name or (watchlist_map.get(stock.symbol) and watchlist_map[stock.symbol].name) or stock.symbol

            lines.append(f"\n### {stock_name}（{stock.symbol}）")

            # 基本行情
            current_price = safe_num(stock.current_price)
            high_price = safe_num(stock.high_price)
            low_price = safe_num(stock.low_price)
            prev_close = safe_num(stock.prev_close, 1)  # 避免除零
            turnover = safe_num(stock.turnover)

            lines.append(f"- 今日：{current_price:.2f} {direction} {change_pct:+.2f}%")
            amplitude = (high_price - low_price) / prev_close * 100 if prev_close > 0 else 0
            lines.append(f"- 振幅：{amplitude:.1f}%  最高{high_price:.2f} 最低{low_price:.2f}")
            lines.append(f"- 成交额：{turnover/1e8:.2f}亿")

            # 技术指标
            tech = technical.get(stock.symbol, {})
            if not tech.get("error"):
                ma5 = safe_num(tech.get('ma5'))
                ma10 = safe_num(tech.get('ma10'))
                ma20 = safe_num(tech.get('ma20'))
                lines.append(f"- 均线：MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}")
                lines.append(f"- 趋势：{tech.get('trend', '未知')}，MACD {tech.get('macd_status', '未知')}")
                change_5d = tech.get("change_5d")
                change_20d = tech.get("change_20d")
                if change_5d is not None:
                    lines.append(f"- 近期：5日{change_5d:+.1f}% 20日{safe_num(change_20d):+.1f}%")
                support = tech.get("support")
                resistance = tech.get("resistance")
                if support is not None and resistance is not None:
                    lines.append(f"- 支撑压力：支撑{support:.2f} 压力{resistance:.2f}")

            # 资金流向（仅A股）
            flow = capital_flow.get(stock.symbol, {})
            if not flow.get("error") and flow.get("status"):
                inflow = safe_num(flow.get("main_net_inflow"))
                inflow_pct = safe_num(flow.get("main_net_inflow_pct"))
                inflow_str = f"{inflow/1e8:+.2f}亿" if abs(inflow) >= 1e8 else f"{inflow/1e4:+.0f}万"
                lines.append(f"- 资金：{flow['status']}，主力净流入{inflow_str}（{inflow_pct:+.1f}%）")
                if flow.get("trend_5d") != "无数据":
                    lines.append(f"- 5日资金：{flow['trend_5d']}")

            # 持仓信息
            position = context.portfolio.get_aggregated_position(stock.symbol)
            if position:
                total_qty = position["total_quantity"]
                avg_cost = safe_num(position["avg_cost"], 1)
                pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
                style_labels = {"short": "短线", "swing": "波段", "long": "长线"}
                style = style_labels.get(position.get("trading_style", "swing"), "波段")
                lines.append(f"- 持仓：{total_qty}股 成本{avg_cost:.2f} 浮盈{pnl_pct:+.1f}%（{style}）")

        if not data["stocks"]:
            lines.append("- 今日无行情数据")

        # 账户资金概况
        if context.portfolio.accounts:
            lines.append("\n## 账户概况")
            for acc in context.portfolio.accounts:
                if acc.positions or acc.available_funds > 0:
                    acc_cost = acc.total_cost
                    lines.append(f"- {acc.name}: 持仓成本{acc_cost:.0f}元 可用资金{acc.available_funds:.0f}元")
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(f"- 合计: 总持仓成本{total_cost:.0f}元 总可用资金{total_funds:.0f}元")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestions(self, content: str, watchlist: list) -> dict[str, dict]:
        """
        从 AI 响应中解析个股建议
        返回: {symbol: {action, action_label, reason, should_alert}}
        """
        suggestions = {}
        symbol_set = {s.symbol for s in watchlist}

        # 匹配格式: 「股票代码」建议类型：理由
        # 也支持 【】 和其他变体
        patterns = [
            r'[「【](\d{5,6})[」】]\s*(继续持有|考虑加仓|考虑减仓|考虑止损|明日关注|暂时回避)[：:]\s*(.+?)(?=\n[「【]|\n\n|\Z)',
            r'[「【]([A-Z]+)[」】]\s*(继续持有|考虑加仓|考虑减仓|考虑止损|明日关注|暂时回避)[：:]\s*(.+?)(?=\n[「【]|\n\n|\Z)',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, content, re.DOTALL):
                symbol = match.group(1)
                action_text = match.group(2)
                reason = match.group(3).strip()

                # 验证是否在自选股列表中
                if symbol not in symbol_set:
                    continue

                action_info = DAILY_ACTION_MAP.get(action_text, {"action": "hold", "label": "继续持有"})
                suggestions[symbol] = {
                    "action": action_info["action"],
                    "action_label": action_info["label"],
                    "reason": reason[:100],  # 限制长度
                    "should_alert": action_info["action"] in ["add", "reduce", "sell"],
                }

        return suggestions

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """调用 AI 分析并保存到历史"""
        result = await super().analyze(context, data)

        # 解析个股建议
        suggestions = self._parse_suggestions(result.content, context.watchlist)
        result.raw_data["suggestions"] = suggestions

        # 保存各股票建议到建议池
        stock_map = {s.symbol: s for s in context.watchlist}
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if stock:
                save_suggestion(
                    stock_symbol=symbol,
                    stock_name=stock.name,
                    action=sug["action"],
                    action_label=sug["action_label"],
                    signal="",  # 盘后日报无单独信号
                    reason=sug.get("reason", ""),
                    agent_name=self.name,
                    agent_label=self.display_name,
                    expires_hours=16,  # 盘后建议隔夜有效
                )

        # 保存到历史记录（使用 "*" 表示全局分析）
        # 简化 raw_data，只保存关键信息
        symbols = [s.symbol for s in data.get("stocks", [])]
        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "symbols": symbols,
                "timestamp": data.get("timestamp"),
                "suggestions": suggestions,
            },
        )
        logger.info(f"盘后日报已保存到历史记录，包含 {len(suggestions)} 条建议")

        return result
