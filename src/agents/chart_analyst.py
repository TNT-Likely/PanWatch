"""技术分析 Agent - 多模态 K 线图分析"""

import logging
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.screenshot_collector import ScreenshotCollector, ChartScreenshot
from src.core.signals import SignalPackBuilder

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "chart_analyst.txt"


class ChartAnalystAgent(BaseAgent):
    """
    技术分析 Agent

    使用多模态 AI 分析 K 线图截图，输出技术分析报告。
    需要支持 Vision 的 AI 模型（如 GPT-4V、GLM-4V 等）。
    """

    name = "chart_analyst"
    display_name = "技术分析"
    description = "截取 K 线图并使用多模态 AI 进行技术分析"

    def __init__(self, period: str = "daily"):
        """
        Args:
            period: K线周期 (daily/weekly/monthly)
        """
        self.period = period
        self._collector: ScreenshotCollector | None = None

    async def collect(self, context: AgentContext) -> dict:
        """采集自选股 K 线图截图"""
        if not context.watchlist:
            logger.warning("自选股列表为空，跳过截图采集")
            return {"screenshots": [], "watchlist": []}

        # 准备股票列表
        stocks = [
            {
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market.value,
            }
            for stock in context.watchlist
        ]

        # 截图
        self._collector = ScreenshotCollector()
        try:
            screenshots = await self._collector.capture_batch(
                stocks, period=self.period
            )

            # 结构化信号（行情/技术/持仓），用于提示词增强（失败不影响截图）
            packs = {}
            try:
                builder = SignalPackBuilder()
                sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
                packs = await builder.build_for_symbols(
                    symbols=sym_list,
                    include_news=False,
                    news_hours=12,
                    portfolio=context.portfolio,
                    include_technical=True,
                    include_capital_flow=False,
                )
            except Exception as e:
                logger.warning(f"SignalPack 获取失败（chart_analyst 继续执行）：{e}")

            # 清理旧截图
            self._collector.cleanup_old_screenshots(max_age_hours=24)

            return {
                "screenshots": screenshots,
                "watchlist": context.watchlist,
                "signal_packs": packs,
                "period": self.period,
                "timestamp": datetime.now().isoformat(),
            }
        finally:
            await self._collector.close()
            self._collector = None

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建技术分析 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        lines.append(f"## 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## K线周期：{self._period_label(data.get('period', 'daily'))}\n")

        # 股票列表（含持仓信息）
        lines.append("## 待分析股票")
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        packs = data.get("signal_packs", {}) or {}

        if screenshots:
            for i, shot in enumerate(screenshots, 1):
                pack = packs.get(shot.symbol)
                position = context.portfolio.get_aggregated_position(shot.symbol)
                if position:
                    lines.append(
                        f"{i}. {shot.name}({shot.symbol}) - 见图{i}"
                        f" | 持仓{position['total_quantity']}股 成本{position['avg_cost']:.2f}"
                    )
                else:
                    lines.append(f"{i}. {shot.name}({shot.symbol}) - 见图{i} | 未持仓")

                # 补充结构化技术摘要（让多模态输出更稳定）
                tech = (pack.technical if pack else None) or {}
                quote = pack.quote if pack else None
                brief_parts = []
                if quote:
                    try:
                        brief_parts.append(
                            f"现价{quote.current_price:.2f} 涨跌{quote.change_pct:+.2f}%"
                        )
                    except Exception:
                        pass
                if tech and not tech.get("error"):
                    if tech.get("trend"):
                        brief_parts.append(f"趋势{tech.get('trend')}")
                    if tech.get("macd_status"):
                        brief_parts.append(f"MACD {tech.get('macd_status')}")
                    if tech.get("rsi_status") and tech.get("rsi6") is not None:
                        try:
                            brief_parts.append(
                                f"RSI {float(tech.get('rsi6')):.1f}({tech.get('rsi_status')})"
                            )
                        except Exception:
                            pass
                    if (
                        tech.get("support_m") is not None
                        and tech.get("resistance_m") is not None
                    ):
                        try:
                            brief_parts.append(
                                f"中期支撑{float(tech.get('support_m')):.2f}/压力{float(tech.get('resistance_m')):.2f}"
                            )
                        except Exception:
                            pass
                if brief_parts:
                    lines.append(f"   - 信号：{'；'.join(brief_parts)}")
        else:
            lines.append("- 无截图")

        # 账户资金概况
        if context.portfolio.accounts:
            lines.append("\n## 资金状况")
            total_funds = context.portfolio.total_available_funds
            total_cost = context.portfolio.total_cost
            if total_funds > 0 or total_cost > 0:
                lines.append(f"- 总可用资金: {total_funds:.0f}元")
                lines.append(f"- 总持仓成本: {total_cost:.0f}元")

        lines.append(
            "\n请根据上述股票的 K 线图进行技术分析，结合持仓情况给出操作建议。"
        )

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _period_label(self, period: str) -> str:
        """周期中文标签"""
        return {
            "daily": "日K",
            "weekly": "周K",
            "monthly": "月K",
        }.get(period, period)

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """
        重写分析方法以支持多模态

        将截图作为图片传给 AI
        """
        system_prompt, user_content = self.build_prompt(data, context)

        # 收集图片路径
        screenshots: list[ChartScreenshot] = data.get("screenshots", [])
        image_paths = [shot.filepath for shot in screenshots if shot.exists]

        if not image_paths:
            logger.warning("没有可用的截图，跳过分析")
            content = "未能获取到 K 线图截图，请检查网络连接或稍后重试。"
        else:
            # 调用多模态 AI
            logger.info(f"使用 {len(image_paths)} 张截图进行多模态分析")
            content = await context.ai_client.chat(
                system_prompt,
                user_content,
                images=image_paths,
            )

        # 构建标题
        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        # 附 AI 模型信息
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
            images=image_paths,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """有截图且有内容时通知"""
        screenshots = result.raw_data.get("screenshots", [])
        return len(screenshots) > 0 and len(result.content) > 50

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        单只模式执行：只分析指定的一只股票

        用于逐只分析场景，每只股票独立截图、分析和通知
        """
        # 过滤只保留指定股票
        original_watchlist = context.config.watchlist
        context.config.watchlist = [
            s for s in original_watchlist if s.symbol == stock_symbol
        ]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("screenshots"):
                return None

            result = await self.analyze(context, data)

            if await self.should_notify(result):
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                notified = bool(notify_result.get("success"))
                result.raw_data["notified"] = notified
                if notified:
                    logger.info(
                        f"Agent [{self.display_name}] 通知已发送: {stock_symbol}"
                    )
                else:
                    notify_error = notify_result.get("error") or "未知错误"
                    result.raw_data["notify_error"] = notify_error
                    logger.error(
                        f"Agent [{self.display_name}] 通知发送失败: {stock_symbol} - {notify_error}"
                    )
            else:
                result.raw_data["notified"] = False

            return result
        finally:
            context.config.watchlist = original_watchlist
