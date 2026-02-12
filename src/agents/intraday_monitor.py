"""盘中监测 Agent - 实时监控持仓，AI 判断是否需要提醒"""

import json
import logging
import re
from datetime import datetime, timedelta, date, timezone
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.akshare_collector import AkshareCollector
from src.collectors.kline_collector import KlineCollector
from src.core.analysis_history import get_latest_analysis, get_analysis
from src.core.suggestion_pool import save_suggestion
from src.core.signals import SignalPackBuilder
from src.core.signals.structured_output import try_parse_action_json
from src.models.market import MarketCode, StockData, MARKETS

logger = logging.getLogger(__name__)


def is_market_trading(market: MarketCode) -> bool:
    """按市场判断是否在交易时段。"""
    market_def = MARKETS.get(market)
    if not market_def:
        return False
    return market_def.is_trading_time()


def market_label(market: MarketCode) -> str:
    if market == MarketCode.CN:
        return "A股"
    if market == MarketCode.HK:
        return "港股"
    if market == MarketCode.US:
        return "美股"
    return market.value


# 标准化操作建议
SUGGESTION_TYPES = {
    "建仓": "buy",  # 新开仓位
    "加仓": "add",  # 增加现有仓位
    "减仓": "reduce",  # 减少仓位
    "清仓": "sell",  # 全部卖出
    "持有": "hold",  # 维持现状
    "观望": "watch",  # 暂不操作
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
            msg = f"当前{market_label(market)}非交易时段，已跳过执行"
            logger.info(f"{msg}: {symbol}")
            return {
                "stocks": [],
                "stock_data": None,
                "skip_reason": msg,
            }

        builder = SignalPackBuilder()
        packs = await builder.build_for_symbols(
            symbols=[(symbol, market, name)],
            include_news=False,
            news_hours=12,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=1,
        )
        pack = packs.get(symbol)

        stock_data = pack.quote if pack and pack.quote else None

        kline_summary = pack.technical if pack else None

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
            "stocks": [stock_data] if stock_data else [],
            "stock_data": stock_data,
            "kline_summary": kline_summary,
            "signal_pack": pack,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
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

        # 系统阈值（帮助 AI 做出更稳定的“提醒/不提醒”判断）
        lines.append("\n## 系统阈值")
        lines.append(f"- 价格异动：|涨跌幅| ≥ {self.price_alert_threshold:.1f}%")
        lines.append(f"- 量能异动：量比 ≥ {self.volume_alert_ratio:.1f}")
        lines.append(f"- 止损预警：浮亏 ≤ {self.stop_loss_warning:.1f}%")
        lines.append(f"- 止盈提醒：浮盈 ≥ {self.take_profit_warning:.1f}%")
        price_hit = (
            "触发" if abs(change_pct) >= self.price_alert_threshold else "未触发"
        )
        lines.append(f"- 当前涨跌幅：{change_pct:+.2f}%（{price_hit}）")

        # K 线和技术指标
        kline = data.get("kline_summary")
        if kline and not kline.get("error"):
            lines.append("\n## 技术分析")

            # 基础趋势
            lines.append(f"- 趋势：{kline.get('trend', 'N/A')}")
            lines.append(
                f"- 近5日：{kline.get('recent_5_up', 0)}涨{5 - kline.get('recent_5_up', 0)}跌"
            )
            lines.append(
                f"- 5日涨幅：{format_num(kline.get('change_5d'))}% | 20日涨幅：{format_num(kline.get('change_20d'))}%"
            )

            # MACD
            macd_info = f"MACD：{kline.get('macd_status', 'N/A')}"
            if kline.get("macd_cross_days"):
                macd_info += f"（{kline.get('macd_cross_days')}日前）"
            lines.append(f"- {macd_info}")

            # RSI
            rsi_status = kline.get("rsi_status")
            rsi6 = kline.get("rsi6")
            if rsi_status and rsi6 is not None:
                lines.append(f"- RSI(6)：{rsi6:.1f}（{rsi_status}）")

            # KDJ
            kdj_status = kline.get("kdj_status")
            kdj_k, kdj_d, kdj_j = (
                kline.get("kdj_k"),
                kline.get("kdj_d"),
                kline.get("kdj_j"),
            )
            if kdj_status and kdj_k is not None:
                lines.append(
                    f"- KDJ：K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}（{kdj_status}）"
                )

            # 布林带
            boll_status = kline.get("boll_status")
            boll_upper, boll_lower = kline.get("boll_upper"), kline.get("boll_lower")
            if boll_status and boll_upper is not None:
                lines.append(
                    f"- 布林带：上轨={format_num(boll_upper)} 下轨={format_num(boll_lower)}（{boll_status}）"
                )

            # 量能
            volume_trend = kline.get("volume_trend")
            volume_ratio = kline.get("volume_ratio")
            if volume_trend:
                vol_info = f"量能：{volume_trend}"
                if volume_ratio:
                    vol_info += f"（量比={volume_ratio:.2f}）"
                lines.append(f"- {vol_info}")
                if volume_ratio:
                    vol_hit = (
                        "触发" if volume_ratio >= self.volume_alert_ratio else "未触发"
                    )
                    lines.append(f"- 量比阈值判断：{vol_hit}")

            # 均线
            lines.append(
                f"- MA5：{format_num(kline.get('ma5'))} | MA10：{format_num(kline.get('ma10'))} | MA20：{format_num(kline.get('ma20'))} | MA60：{format_num(kline.get('ma60'))}"
            )

        # 资金流向（仅A股，若可用）
        pack = data.get("signal_pack")
        flow = getattr(pack, "capital_flow", None) if pack else None
        if (
            isinstance(flow, dict)
            and flow
            and not flow.get("error")
            and flow.get("status")
        ):
            try:
                inflow = float(flow.get("main_net_inflow") or 0)
                inflow_pct = float(flow.get("main_net_inflow_pct") or 0)
                inflow_str = (
                    f"{inflow / 1e8:+.2f}亿"
                    if abs(inflow) >= 1e8
                    else f"{inflow / 1e4:+.0f}万"
                )
                lines.append("\n## 资金面")
                lines.append(
                    f"- 资金：{flow.get('status')}，主力净流入{inflow_str}（{inflow_pct:+.1f}%）"
                )
                if flow.get("trend_5d") and flow.get("trend_5d") != "无数据":
                    lines.append(f"- 5日资金：{flow.get('trend_5d')}")
            except Exception:
                pass

            # 多级支撑压力
            support_m, resistance_m = kline.get("support_m"), kline.get("resistance_m")
            if support_m and resistance_m:
                lines.append(
                    f"- 中期支撑：{format_num(support_m)} | 中期压力：{format_num(resistance_m)}"
                )

            support_s, resistance_s = kline.get("support_s"), kline.get("resistance_s")
            if support_s and resistance_s:
                lines.append(
                    f"- 短期支撑：{format_num(support_s)} | 短期压力：{format_num(resistance_s)}"
                )

            # K线形态
            kline_pattern = kline.get("kline_pattern")
            if kline_pattern:
                lines.append(f"- K线形态：{kline_pattern}")

            # 振幅
            amplitude = kline.get("amplitude")
            amplitude_avg5 = kline.get("amplitude_avg5")
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
                pnl_pct = (
                    (current_price - cost_price) / cost_price * 100
                    if cost_price > 0
                    else 0
                )
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
                pnl_note = ""
                if pnl_pct <= self.stop_loss_warning:
                    pnl_note = "（触发止损预警）"
                elif pnl_pct >= self.take_profit_warning:
                    pnl_note = "（触发止盈提醒）"
                lines.append(f"- 浮动盈亏：{pnl_pct:+.1f}%{pnl_note}")
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
                content = (
                    daily_analysis[:300] + "..."
                    if len(daily_analysis) > 300
                    else daily_analysis
                )
                lines.append(f"\n### 昨日盘后分析摘要")
                lines.append(content)

            if premarket_analysis:
                content = (
                    premarket_analysis[:300] + "..."
                    if len(premarket_analysis) > 300
                    else premarket_analysis
                )
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
            "should_alert": False,
        }

        # 1) Prefer JSON output (structured mode)
        obj = try_parse_action_json(content) or self._try_parse_loose_json(content)
        if obj:
            action = (obj.get("action") or "watch").strip()
            result["action"] = action
            result["action_label"] = (
                obj.get("action_label") or result["action_label"]
            ).strip()[:20]
            result["signal"] = (obj.get("signal") or "").strip()[:60]
            result["reason"] = (obj.get("reason") or "").strip()[:160]
            result["should_alert"] = action in {
                "buy",
                "add",
                "reduce",
                "sell",
                "alert",
                "avoid",
            }
            result["triggers"] = (
                obj.get("triggers") if isinstance(obj.get("triggers"), list) else []
            )
            result["invalidations"] = (
                obj.get("invalidations")
                if isinstance(obj.get("invalidations"), list)
                else []
            )
            result["risks"] = (
                obj.get("risks") if isinstance(obj.get("risks"), list) else []
            )
            return result

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
            clean_content = re.sub(r"\*\*|##|#", "", content).strip()
            # 跳过无需提醒的情况
            if not clean_content.startswith("[无需提醒]"):
                result["reason"] = clean_content[:100]

        # 最终 should_alert 判定：只在明确“建仓/加仓/减仓/清仓”时提醒
        result["should_alert"] = result["action"] in {"buy", "add", "reduce", "sell"}
        return result

    def _try_parse_loose_json(self, text: str) -> dict | None:
        """宽松解析 JSON 输出，兜底兼容模型异常格式。"""
        raw = (text or "").strip()
        if not raw:
            return None

        # 兼容首行 "json"
        lines = raw.splitlines()
        if lines and lines[0].strip().lower() == "json":
            raw = "\n".join(lines[1:]).strip()

        # 去掉 fenced code block
        if raw.startswith("```"):
            block_lines = raw.splitlines()
            if len(block_lines) >= 3 and block_lines[-1].strip().startswith("```"):
                raw = "\n".join(block_lines[1:-1]).strip()
                if raw.lower().startswith("json\n"):
                    raw = raw[5:].strip()

        # 优先直接解析，失败则提取首个 JSON 对象片段
        try:
            obj = json.loads(raw)
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                return None
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return None

        if not isinstance(obj, dict):
            return None

        # 没有关键字段时不认为是建议 JSON
        keys = {"action", "action_label", "signal", "reason", "triggers", "invalidations", "risks"}
        if not any(k in obj for k in keys):
            return None
        return obj

    def _format_human_readable_content(
        self, stock: StockData, suggestion: dict, raw_content: str
    ) -> str:
        """当模型返回 JSON 时，生成可读通知内容。"""
        action_label = suggestion.get("action_label") or "观望"
        signal = suggestion.get("signal") or "无明显新信号"
        reason = suggestion.get("reason") or "请结合盘面与风控策略审慎判断。"
        triggers = (
            suggestion.get("triggers")
            if isinstance(suggestion.get("triggers"), list)
            else []
        )
        invalidations = (
            suggestion.get("invalidations")
            if isinstance(suggestion.get("invalidations"), list)
            else []
        )
        risks = (
            suggestion.get("risks") if isinstance(suggestion.get("risks"), list) else []
        )
        price = (
            f"{stock.current_price:.2f}" if getattr(stock, "current_price", None) else "N/A"
        )
        chg = f"{(stock.change_pct or 0):+.2f}%"
        lines = [
            f"{stock.name}（{stock.symbol}）",
            f"现价：{price}  涨跌：{chg}",
            f"建议：{action_label}",
            f"信号：{signal}",
            f"理由：{reason}",
        ]
        if triggers:
            lines.append("触发条件：")
            lines.extend([f"- {str(x)}" for x in triggers[:3]])
        if invalidations:
            lines.append("失效条件：")
            lines.extend([f"- {str(x)}" for x in invalidations[:3]])
        if risks:
            lines.append("风险提示：")
            lines.extend([f"- {str(x)}" for x in risks[:3]])
        # 若本次并非纯 JSON，附上简短原文摘要便于核对
        if not (try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content)):
            brief = re.sub(r"\s+", " ", (raw_content or "").strip())[:200]
            if brief:
                lines.append(f"备注：{brief}")
        return "\n".join(lines)

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

        raw_content = await context.ai_client.chat(system_prompt, user_content)

        # 打印 AI 返回结果
        logger.info(f"=== AI Response for {stock.symbol} ===\n{raw_content}")

        # 解析操作建议
        suggestion = self._parse_suggestion(raw_content)
        content = raw_content
        # JSON/类 JSON 输出时，统一转换为可读通知文本，避免渠道直接推送原始 JSON
        if try_parse_action_json(raw_content) or self._try_parse_loose_json(raw_content):
            content = self._format_human_readable_content(stock, suggestion, raw_content)

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
            expires_hours=6,  # 盘中建议 6 小时有效
            prompt_context=user_content,  # 保存 prompt 上下文
            ai_response=raw_content,  # 保存 AI 原始响应
            meta={
                "quote": {
                    "current_price": stock.current_price,
                    "change_pct": stock.change_pct,
                },
                "kline_meta": {
                    "computed_at": (data.get("kline_summary") or {}).get("computed_at"),
                    "asof": (data.get("kline_summary") or {}).get("asof"),
                },
                "event_gate": data.get("event_gate"),
                "plan": {
                    "triggers": suggestion.get("triggers")
                    if isinstance(suggestion, dict)
                    else [],
                    "invalidations": suggestion.get("invalidations")
                    if isinstance(suggestion, dict)
                    else [],
                    "risks": suggestion.get("risks")
                    if isinstance(suggestion, dict)
                    else [],
                },
            },
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
            logger.info(
                f"AI 判断无需提醒: {result.raw_data.get('stock', {}).get('symbol')}"
            )
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
                logger.info(
                    f"通知节流: {symbol} 在 {self.throttle_minutes} 分钟内已通知"
                )
                return False
        else:
            logger.info(f"跳过节流检查（测试模式）: {symbol}")

        return True

    def _check_throttle(self, symbol: str) -> bool:
        """检查是否可以发送通知（未被节流）"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            if not record:
                return True

            # 以 UTC 进行比较，避免容器/部署时区变化导致异常
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            threshold = now - timedelta(minutes=self.throttle_minutes)
            last = record.last_notify_at
            if last and last.tzinfo is not None:
                last = last.astimezone(timezone.utc).replace(tzinfo=None)
            return (last or datetime.fromtimestamp(0)) < threshold
        finally:
            db.close()

    def _update_throttle(self, symbol: str):
        """更新节流记录"""
        from src.web.database import SessionLocal
        from src.web.models import NotifyThrottle

        db = SessionLocal()
        try:
            record = (
                db.query(NotifyThrottle)
                .filter(
                    NotifyThrottle.agent_name == self.name,
                    NotifyThrottle.stock_symbol == symbol,
                )
                .first()
            )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if record:
                # 检查是否是新的一天
                if record.last_notify_at.date() < now.date():
                    record.notify_count = 1
                else:
                    record.notify_count += 1
                record.last_notify_at = now
            else:
                db.add(
                    NotifyThrottle(
                        agent_name=self.name,
                        stock_symbol=symbol,
                        last_notify_at=now,
                        notify_count=1,
                    )
                )

            db.commit()
        finally:
            db.close()

    async def run_single(
        self, context: AgentContext, stock_symbol: str
    ) -> AnalysisResult | None:
        """
        单只模式执行：只分析指定的一只股票

        用于实时监控场景，每只股票独立分析和通知
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
            if not data.get("stock_data"):
                return None

            # 事件门禁仅作为上下文信号，不阻断 AI 分析。
            # 产品策略：建议持续刷新，通知再由 should_alert + throttle 控制降噪。
            if self.event_only:
                try:
                    from src.core.intraday_event_gate import check_and_update

                    stock = data.get("stock_data")
                    kline_summary = data.get("kline_summary")
                    decision = check_and_update(
                        symbol=stock_symbol,
                        change_pct=getattr(stock, "change_pct", None),
                        volume_ratio=(kline_summary or {}).get("volume_ratio"),
                        kline_summary=kline_summary,
                        price_threshold=self.price_alert_threshold,
                        volume_threshold=self.volume_alert_ratio,
                    )
                    data["event_gate"] = {
                        "reasons": decision.reasons,
                        "should_analyze": bool(decision.should_analyze),
                    }
                except Exception as e:
                    logger.debug(f"事件门禁异常，继续分析: {e}")

            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

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
                    if not self.bypass_throttle:
                        self._update_throttle(stock_symbol)
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
