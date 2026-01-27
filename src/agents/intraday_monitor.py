"""盘中监测 Agent - 实时监控持仓，AI 判断是否需要提醒"""
import logging
import re
from datetime import datetime, timedelta, date
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.akshare_collector import AkshareCollector
from src.collectors.kline_collector import KlineCollector
from src.core.analysis_history import get_latest_analysis, get_analysis
from src.core.suggestion_pool import save_suggestion
from src.models.market import MarketCode, StockData, MARKETS

logger = logging.getLogger(__name__)


def is_any_market_trading() -> bool:
    """检查是否有任何市场正在交易"""
    for market_def in MARKETS.values():
        if market_def.is_trading_time():
            return True
    return False


# 标准化操作建议
SUGGESTION_TYPES = {
    "建仓": "buy",      # 新开仓位
    "加仓": "add",      # 增加现有仓位
    "减仓": "reduce",   # 减少仓位
    "清仓": "sell",     # 全部卖出
    "持有": "hold",     # 维持现状
    "观望": "watch",    # 暂不操作
}

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "intraday_monitor.txt"


class IntradayMonitorAgent(BaseAgent):
    """
    盘中监测 Agent

    特点：
    - 单只模式 (single): 逐只股票分析，每只单独发送通知
    - AI 智能判断: 把股票数据发给 AI，由 AI 决定是否值得提醒
    - 通知节流: 同一股票短时间内不重复通知
    - 技术分析: 包含 K 线和技术指标
    """

    name = "intraday_monitor"
    display_name = "盘中监测"
    description = "交易时段实时监控持仓，AI 判断是否有值得关注的信号"

    def __init__(self, throttle_minutes: int = 30, bypass_throttle: bool = False):
        """
        Args:
            throttle_minutes: 同一股票通知间隔（分钟）
            bypass_throttle: 是否跳过节流（测试用）
        """
        self.throttle_minutes = throttle_minutes
        self.bypass_throttle = bypass_throttle

    async def collect(self, context: AgentContext) -> dict:
        """采集实时行情 + K线 + 历史分析"""
        # 检查是否在交易时段
        if not is_any_market_trading():
            logger.info("当前非交易时段，跳过盘中监测")
            return {"stocks": [], "stock_data": None, "skip_reason": "非交易时段"}

        if not context.watchlist:
            logger.warning("自选股列表为空，跳过盘中监测")
            return {"stocks": [], "stock_data": None}

        # 按市场分组采集
        market_symbols: dict[MarketCode, list[str]] = {}
        for stock in context.watchlist:
            market_symbols.setdefault(stock.market, []).append(stock.symbol)

        all_stocks: list[StockData] = []
        for market_code, symbols in market_symbols.items():
            collector = AkshareCollector(market_code)
            try:
                stocks = await collector.get_stock_data(symbols)
                all_stocks.extend(stocks)
            except Exception as e:
                logger.error(f"采集 {market_code.value} 行情失败: {e}")

        # 单只模式下只有一只股票
        stock_data = all_stocks[0] if all_stocks else None

        # 采集 K 线和技术指标
        kline_summary = None
        if stock_data:
            try:
                stock_config = context.watchlist[0] if context.watchlist else None
                market = stock_config.market if stock_config else MarketCode.CN
                kline_collector = KlineCollector(market)
                kline_summary = kline_collector.get_kline_summary(stock_data.symbol)
            except Exception as e:
                logger.warning(f"获取 K 线数据失败: {e}")

        # 获取历史分析（为 AI 提供更多上下文）
        daily_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )
        premarket_analysis = get_analysis(
            agent_name="premarket_outlook",
            stock_symbol="*",
            analysis_date=date.today(),
        )

        return {
            "stocks": all_stocks,
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content if premarket_analysis else None,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建盘中分析 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 辅助函数：安全获取数值，None 转为默认值
        def safe_num(value, default=0):
            return value if value is not None else default

        def format_num(value, precision=2):
            if value is None:
                return "N/A"
            return f"{value:.{precision}f}"

        stock: StockData | None = data.get("stock_data")
        if not stock:
            return system_prompt, "无股票数据"

        # 获取所有账户的持仓信息
        positions = context.portfolio.get_positions_for_stock(stock.symbol)
        style_labels = {"short": "短线", "swing": "波段", "long": "长线"}

        lines = []
        lines.append(f"## 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

        # 股票行情
        current_price = safe_num(stock.current_price)
        change_pct = safe_num(stock.change_pct)
        change_amount = safe_num(stock.change_amount)
        open_price = safe_num(stock.open_price)
        high_price = safe_num(stock.high_price)
        low_price = safe_num(stock.low_price)
        prev_close = safe_num(stock.prev_close)
        volume = safe_num(stock.volume)
        turnover = safe_num(stock.turnover)

        lines.append("## 股票行情")
        lines.append(f"- 股票：{stock.name}（{stock.symbol}）")
        lines.append(f"- 现价：{current_price:.2f}")
        lines.append(f"- 涨跌幅：{change_pct:+.2f}%")
        lines.append(f"- 涨跌额：{change_amount:+.2f}")
        lines.append(f"- 今开：{open_price:.2f}")
        lines.append(f"- 最高：{high_price:.2f}")
        lines.append(f"- 最低：{low_price:.2f}")
        lines.append(f"- 昨收：{prev_close:.2f}")
        if volume > 0:
            lines.append(f"- 成交量：{volume:.0f} 手")
        if turnover > 0:
            lines.append(f"- 成交额：{turnover / 10000:.0f} 万")

        # K 线和技术指标
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## 技术分析")

            # 基础趋势
            lines.append(f"- 趋势：{kline.get('trend', 'N/A')}")
            lines.append(f"- 近5日：{kline.get('recent_5_up', 0)}涨{5-kline.get('recent_5_up', 0)}跌")
            lines.append(f"- 5日涨幅：{format_num(kline.get('change_5d'))}% | 20日涨幅：{format_num(kline.get('change_20d'))}%")

            # MACD
            macd_info = f"MACD：{kline.get('macd_status', 'N/A')}"
            if kline.get('macd_cross_days'):
                macd_info += f"（{kline.get('macd_cross_days')}日前）"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get('rsi_status')
            rsi6 = kline.get('rsi6')
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6)：{rsi6:.1f}（{rsi_status}）")

            # KDJ
            kdj_status = kline.get('kdj_status')
            kdj_k, kdj_d, kdj_j = kline.get('kdj_k'), kline.get('kdj_d'), kline.get('kdj_j')
            if kdj_status and kdj_k is not None:
                lines.append(f"- KDJ：K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}（{kdj_status}）")

            # 布林带
            boll_status = kline.get('boll_status')
            boll_upper, boll_lower = kline.get('boll_upper'), kline.get('boll_lower')
            if boll_status and boll_upper is not None:
                lines.append(f"- 布林带：上轨={format_num(boll_upper)} 下轨={format_num(boll_lower)}（{boll_status}）")

            # 量能
            volume_trend = kline.get('volume_trend')
            volume_ratio = kline.get('volume_ratio')
            if volume_trend:
                vol_info = f"量能：{volume_trend}"
                if volume_ratio:
                    vol_info += f"（量比={volume_ratio:.2f}）"
                lines.append(f"- {vol_info}")

            # 均线
            lines.append(f"- MA5：{format_num(kline.get('ma5'))} | MA10：{format_num(kline.get('ma10'))} | MA20：{format_num(kline.get('ma20'))} | MA60：{format_num(kline.get('ma60'))}")

            # 多级支撑压力
            support_m, resistance_m = kline.get('support_m'), kline.get('resistance_m')
            if support_m and resistance_m:
                lines.append(f"- 中期支撑：{format_num(support_m)} | 中期压力：{format_num(resistance_m)}")

            support_s, resistance_s = kline.get('support_s'), kline.get('resistance_s')
            if support_s and resistance_s:
                lines.append(f"- 短期支撑：{format_num(support_s)} | 短期压力：{format_num(resistance_s)}")

            # K线形态
            kline_pattern = kline.get('kline_pattern')
            if kline_pattern:
                lines.append(f"- K线形态：{kline_pattern}")

            # 振幅
            amplitude = kline.get('amplitude')
            amplitude_avg5 = kline.get('amplitude_avg5')
            if amplitude is not None:
                amp_info = f"今日振幅：{amplitude:.2f}%"
                if amplitude_avg5 is not None:
                    amp_info += f"（5日平均：{amplitude_avg5:.2f}%）"
                lines.append(f"- {amp_info}")

        # 账户资金情况
        lines.append(f"\n## 账户资金")
        lines.append(f"- 总可用资金：{context.portfolio.total_available_funds:.0f} 元")
        for acc in context.portfolio.accounts:
            lines.append(f"  - {acc.name}：{acc.available_funds:.0f} 元")

        # 各账户持仓信息
        if positions:
            lines.append(f"\n## 持仓情况（共 {len(positions)} 个账户）")
            for i, pos in enumerate(positions, 1):
                cost_price = safe_num(pos.cost_price, 1)
                pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
                style_label = style_labels.get(pos.trading_style, "波段")
                market_value = current_price * pos.quantity
                # 找到对应账户的可用资金
                acc_funds = 0
                for acc in context.portfolio.accounts:
                    if acc.id == pos.account_id:
                        acc_funds = acc.available_funds
                        break

                lines.append(f"\n### 持仓 {i}：{pos.account_name}")
                lines.append(f"- 交易风格：{style_label}")
                lines.append(f"- 成本价：{cost_price:.2f}")
                lines.append(f"- 持仓量：{pos.quantity} 股")
                lines.append(f"- 持仓市值：{market_value:.0f} 元")
                lines.append(f"- 浮动盈亏：{pnl_pct:+.1f}%")
                lines.append(f"- 账户可用：{acc_funds:.0f} 元")
        else:
            lines.append("\n## 未持仓（仅关注）")
            lines.append(f"- 可用资金充足，可考虑建仓")

        # 历史分析上下文（帮助 AI 做出更好的判断）
        daily_analysis = data.get("daily_analysis")
        premarket_analysis = data.get("premarket_analysis")

        if daily_analysis or premarket_analysis:
            lines.append("\n## 历史分析参考")

            if daily_analysis:
                # 截取与当前股票相关的部分（最多 300 字）
                content = daily_analysis[:300] + "..." if len(daily_analysis) > 300 else daily_analysis
                lines.append(f"\n### 昨日盘后分析摘要")
                lines.append(content)

            if premarket_analysis:
                content = premarket_analysis[:300] + "..." if len(premarket_analysis) > 300 else premarket_analysis
                lines.append(f"\n### 今日盘前分析摘要")
                lines.append(content)

        lines.append("\n请结合技术分析、资金情况和历史分析，给出明确的操作建议。")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestion(self, content: str) -> dict:
        """
        从 AI 响应中解析操作建议

        Returns:
            {
                "action": "hold",  # buy/add/reduce/sell/hold/watch
                "action_label": "持有",
                "signal": "...",
                "reason": "...",
                "should_alert": True
            }
        """
        result = {
            "action": "watch",
            "action_label": "观望",
            "signal": "",
            "reason": "",
            "should_alert": True,
        }

        # 检查是否无需提醒
        if "[无需提醒]" in content:
            result["should_alert"] = False
            result["action"] = "hold"
            result["action_label"] = "持有"
            return result

        # 提取建议类型（从全文搜索）
        for label, action in SUGGESTION_TYPES.items():
            if label in content:
                result["action"] = action
                result["action_label"] = label
                break

        # 提取信号（支持多种格式）
        signal_patterns = [
            r"「信号」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*信号\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"信号\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in signal_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["signal"] = match.group(1).strip()[:50]
                break

        # 提取建议内容（支持多种格式）
        suggest_patterns = [
            r"「建议」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*建议\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"建议\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in suggest_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                suggest_text = match.group(1).strip()
                # 从建议中提取操作类型
                for label, action in SUGGESTION_TYPES.items():
                    if label in suggest_text:
                        result["action"] = action
                        result["action_label"] = label
                        break
                # 如果信号为空，使用建议内容作为信号
                if not result["signal"]:
                    result["signal"] = suggest_text[:50]
                break

        # 提取理由（支持多种格式）
        reason_patterns = [
            r"「理由」\s*[:：]?\s*(.+?)(?=「|$|\n\n)",
            r"\*\*理由\*\*\s*[:：]?\s*(.+?)(?=\*\*|$|\n\n)",
            r"理由\s*[:：]\s*(.+?)(?=\n|$)",
        ]
        for pattern in reason_patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                result["reason"] = match.group(1).strip()[:100]
                break

        # 如果没有提取到信号和理由，尝试使用整段内容的前部分
        if not result["signal"] and not result["reason"]:
            # 清理 markdown 格式后取前 100 字符
            clean_content = re.sub(r'\*\*|##|#', '', content).strip()
            # 跳过无需提醒的情况
            if not clean_content.startswith("[无需提醒]"):
                result["reason"] = clean_content[:100]

        return result

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """AI 分析并判断是否需要提醒"""
        # 非交易时段跳过
        if data.get("skip_reason"):
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】跳过",
                content=data.get("skip_reason", "跳过执行"),
                raw_data={"skipped": True, **data},
            )

        stock: StockData | None = data.get("stock_data")

        if not stock:
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】无数据",
                content="未获取到股票数据",
                raw_data=data,
            )

        system_prompt, user_content = self.build_prompt(data, context)

        # 打印完整 prompt 用于调试
        logger.info(f"=== Prompt for {stock.symbol} ===\n{user_content}")

        content = await context.ai_client.chat(system_prompt, user_content)

        # 打印 AI 返回结果
        logger.info(f"=== AI Response for {stock.symbol} ===\n{content}")

        # 解析操作建议
        suggestion = self._parse_suggestion(content)

        # 保存到建议池（包含 prompt 上下文）
        save_suggestion(
            stock_symbol=stock.symbol,
            stock_name=stock.name,
            action=suggestion["action"],
            action_label=suggestion["action_label"],
            signal=suggestion.get("signal", ""),
            reason=suggestion.get("reason", ""),
            agent_name=self.name,
            agent_label=self.display_name,
            expires_hours=4,  # 盘中建议 4 小时有效
            prompt_context=user_content,  # 保存 prompt 上下文
            ai_response=content,  # 保存 AI 原始响应
        )

        # 构建标题
        title = f"【{self.display_name}】{stock.name} {stock.change_pct:+.2f}%"

        # 附 AI 模型信息
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data={
                "stock": {
                    "symbol": stock.symbol,
                    "name": stock.name,
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "suggestion": suggestion,
                "should_alert": suggestion["should_alert"],
                "kline_summary": data.get("kline_summary"),
                **data,
            },
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """检查是否需要通知"""
        # 跳过的结果不通知
        if result.raw_data.get("skipped"):
            return False

        # AI 判断不需要提醒
        if not result.raw_data.get("should_alert", True):
            logger.info(f"AI 判断无需提醒: {result.raw_data.get('stock', {}).get('symbol')}")
            return False

        stock_data = result.raw_data.get("stock")
        if not stock_data:
            return False

        symbol = stock_data.get("symbol")
        if not symbol:
            return False

        # 检查节流（测试模式可跳过）
        if not self.bypass_throttle:
            if not self._check_throttle(symbol):
                logger.info(f"通知节流: {symbol} 在 {self.throttle_minutes} 分钟内已通知")
                return False
            # 更新节流记录
            self._update_throttle(symbol)
        else:
            logger.info(f"跳过节流检查（测试模式）: {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """检查是否可以发送通知（未被节流）"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = db.query(NotifyThrottle).filter(
                NotifyThrottle.agent_name == self.name,
                NotifyThrottle.stock_symbol == symbol,
            ).first()

            if not record:
                return True

            # 检查是否超过节流时间
            threshold = datetime.now() - timedelta(minutes=self.throttle_minutes)
            return record.last_notify_at < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """更新节流记录"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = db.query(NotifyThrottle).filter(
                NotifyThrottle.agent_name == self.name,
                NotifyThrottle.stock_symbol == symbol,
            ).first()

            now = datetime.now()
            if record:
                # 检查是否是新的一天
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(NotifyThrottle(
                    agent_name=self.name,
                    stock_symbol=symbol,
                    last_notify_at=now,
                    notify_count=1,
                ))

            db.commit()
        finally:
            db.close()

    async def run_single(self, context: AgentContext, stock_symbol: str) -> AnalysisResult | None:
        """
        单只模式执行：只分析指定的一只股票

        用于实时监控场景，每只股票独立分析和通知
        """
        # 过滤只保留指定股票
        original_watchlist = context.config.watchlist
        context.config.watchlist = [s for s in original_watchlist if s.symbol == stock_symbol]

        if not context.config.watchlist:
            return None

        try:
            data = await self.collect(context)
            if not data.get("stock_data"):
                return None

            result = await self.analyze(context, data)

            if await self.should_notify(result):
                await context.notifier.notify(
                    result.title,
                    result.content,
                    result.images,
                )
                logger.info(f"Agent [{self.display_name}] 通知已发送: {stock_symbol}")

            return result
        finally:
            context.config.watchlist = original_watchlist
