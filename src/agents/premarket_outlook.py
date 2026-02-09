"""盘前分析 Agent - 开盘前展望今日走势"""

import logging
import re
from datetime import datetime, date, timedelta
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.core.signals import SignalPackBuilder
from src.core.analysis_history import save_analysis, get_latest_analysis
from src.core.suggestion_pool import save_suggestion
from src.core.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

# 盘前建议类型映射
PREMARKET_ACTION_MAP = {
    "准备建仓": {"action": "buy", "label": "准备建仓"},
    "准备加仓": {"action": "add", "label": "准备加仓"},
    "准备减仓": {"action": "reduce", "label": "准备减仓"},
    "设置预警": {"action": "alert", "label": "设置预警"},
    "观望": {"action": "watch", "label": "观望"},
}

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "premarket_outlook.txt"


class PremarketOutlookAgent(BaseAgent):
    """盘前分析 Agent"""

    name = "premarket_outlook"
    display_name = "盘前分析"
    description = "开盘前综合昨日分析和隔夜信息，展望今日走势"

    async def collect(self, context: AgentContext) -> dict:
        """采集盘前数据"""
        # 1. 获取昨日盘后分析
        yesterday_analysis = get_latest_analysis(
            agent_name="daily_report",
            stock_symbol="*",
            before_date=date.today(),
        )

        # 2. 获取美股指数（隔夜表现）
        us_indices = []
        try:
            # 复用腾讯行情解析（避免手写解析导致 symbol 格式不一致）
            from src.collectors.akshare_collector import _fetch_tencent_quotes

            items = _fetch_tencent_quotes(["usDJI", "usIXIC", "usINX"])
            for item in items:
                us_indices.append(
                    {
                        "name": item.get("name") or item.get("symbol"),
                        "current": item.get("current_price"),
                        "change_pct": item.get("change_pct"),
                    }
                )
        except Exception as e:
            logger.warning(f"获取美股指数失败: {e}")

        # 3/4. SignalPack（技术面+持仓+新闻）
        builder = SignalPackBuilder()
        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        packs = await builder.build_for_symbols(
            symbols=sym_list,
            include_news=True,
            news_hours=12,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
        )

        # Flatten news for headline section
        news_items = []
        try:
            seen = set()
            for sym in [s.symbol for s in context.watchlist]:
                pack = packs.get(sym)
                for it in (pack.news.items if pack and pack.news else [])[:3]:
                    key = (it.get("source"), it.get("external_id"), it.get("title"))
                    if key in seen:
                        continue
                    seen.add(key)
                    news_items.append(
                        {
                            "source": it.get("source"),
                            "title": it.get("title"),
                            "content": "",
                            "time": (it.get("time") or "").split(" ")[-1],
                            "symbols": [sym],
                            "importance": it.get("importance") or 0,
                            "url": it.get("url"),
                        }
                    )
                    if len(news_items) >= 10:
                        break
                if len(news_items) >= 10:
                    break
        except Exception:
            news_items = []

        return {
            "yesterday_analysis": yesterday_analysis.content
            if yesterday_analysis
            else None,
            "us_indices": us_indices,
            "signal_packs": packs,
            "news": news_items,
            "timestamp": datetime.now().isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建盘前分析 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        # 辅助函数：安全获取数值，None 转为默认值
        def safe_num(value, default=0):
            return value if value is not None else default

        lines = []
        lines.append(f"## 日期：{datetime.now().strftime('%Y-%m-%d')} 盘前\n")

        # 昨日分析回顾
        if data.get("yesterday_analysis"):
            lines.append("## 昨日盘后分析回顾")
            # 截取前 500 字，避免过长
            content = data["yesterday_analysis"]
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(content)
            lines.append("")

        # 隔夜美股表现
        if data.get("us_indices"):
            lines.append("## 隔夜美股表现")
            for idx in data["us_indices"]:
                direction = (
                    "↑"
                    if idx["change_pct"] > 0
                    else "↓"
                    if idx["change_pct"] < 0
                    else "→"
                )
                lines.append(
                    f"- {idx['name']}: {idx['current']:.2f} {direction} {idx['change_pct']:+.2f}%"
                )
            lines.append("")

        # 相关新闻
        if data.get("news"):
            lines.append("## 相关新闻资讯")
            for news in data["news"]:
                source_label = {"sina": "新浪", "eastmoney": "东财"}.get(
                    news["source"], news["source"]
                )
                importance_star = (
                    "⭐" * news.get("importance", 0) if news.get("importance") else ""
                )
                symbols_tag = (
                    f"[{','.join(news['symbols'])}]" if news["symbols"] else ""
                )
                link = f"([原文]({news['url']}))" if news.get("url") else ""
                lines.append(
                    f"- [{news['time']}] {importance_star}{news['title']} {symbols_tag} {link}".strip()
                )
                if news.get("content"):
                    lines.append(f"  > {news['content'][:100]}...")
            lines.append("")

        # 自选股技术状态（来自 SignalPack）
        lines.append("## 自选股技术状态")
        packs = data.get("signal_packs", {}) or {}
        watchlist_map = {s.symbol: s for s in context.watchlist}
        news_items = data.get("news", []) or []

        for stock in context.watchlist:
            pack = packs.get(stock.symbol)
            tech = (pack.technical if pack else None) or {}
            if tech.get("error"):
                lines.append(f"\n### {stock.name}（{stock.symbol}）")
                lines.append(f"- 数据获取失败：{tech.get('error')}")
                continue

            lines.append(f"\n### {stock.name}（{stock.symbol}）")
            last_close = tech.get("last_close")
            if last_close is not None:
                lines.append(f"- 昨收价：{last_close:.2f}")
            if tech.get("trend"):
                lines.append(f"- 均线趋势：{tech['trend']}")
            if tech.get("macd_status"):
                lines.append(f"- MACD 状态：{tech['macd_status']}")
            # RSI / KDJ / 布林 / 量能 / 形态
            if tech.get("rsi6") is not None and tech.get("rsi_status"):
                lines.append(
                    f"- RSI：{tech.get('rsi6'):.1f}（{tech.get('rsi_status')}）"
                )
            if tech.get("kdj_status"):
                kdj_k = tech.get("kdj_k")
                kdj_d = tech.get("kdj_d")
                kdj_j = tech.get("kdj_j")
                if kdj_k is not None and kdj_d is not None and kdj_j is not None:
                    lines.append(
                        f"- KDJ：{tech.get('kdj_status')}（K={kdj_k:.1f} D={kdj_d:.1f} J={kdj_j:.1f}）"
                    )
                else:
                    lines.append(f"- KDJ：{tech.get('kdj_status')}")
            if tech.get("boll_status"):
                boll_upper = tech.get("boll_upper")
                boll_lower = tech.get("boll_lower")
                if boll_upper is not None and boll_lower is not None:
                    lines.append(
                        f"- 布林：{tech.get('boll_status')}（上轨{boll_upper:.2f} 下轨{boll_lower:.2f}）"
                    )
                else:
                    lines.append(f"- 布林：{tech.get('boll_status')}")
            if tech.get("volume_trend"):
                vol_ratio = tech.get("volume_ratio")
                ratio_str = f"（量比{vol_ratio:.2f}）" if vol_ratio is not None else ""
                lines.append(f"- 量能：{tech.get('volume_trend')}{ratio_str}")
            if tech.get("kline_pattern"):
                lines.append(f"- 形态：{tech.get('kline_pattern')}")

            # 资金流向（仅A股，若可用）
            flow = (pack.capital_flow if pack else None) or {}
            if (
                getattr(stock, "market", None) == MarketCode.CN
                and isinstance(flow, dict)
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
                    lines.append(
                        f"- 资金：{flow.get('status')}，主力净流入{inflow_str}（{inflow_pct:+.1f}%）"
                    )
                    if flow.get("trend_5d") and flow.get("trend_5d") != "无数据":
                        lines.append(f"- 5日资金：{flow.get('trend_5d')}")
                except Exception:
                    pass

            # 个股相关新闻（便于 AI 在每只股票维度结合消息面）
            stock_news = [
                n for n in news_items if stock.symbol in (n.get("symbols") or [])
            ]
            if stock_news:
                lines.append("- 相关新闻：")
                for n in stock_news[:3]:
                    source_label = {"sina": "新浪", "eastmoney": "东财"}.get(
                        n.get("source"), n.get("source")
                    )
                    importance_star = (
                        "⭐" * n.get("importance", 0) if n.get("importance") else ""
                    )
                    time_str = n.get("time") or ""
                    title = n.get("title") or ""
                    link = f"[原文]({n.get('url')})" if n.get("url") else ""
                    lines.append(
                        f"  - [{time_str}] {importance_star}{title}（{source_label}）{(' ' + link) if link else ''}"
                    )
            else:
                lines.append("- 相关新闻：暂无")

            # 多级支撑压力（优先中期）
            support_m = tech.get("support_m")
            resistance_m = tech.get("resistance_m")
            if support_m is not None and resistance_m is not None:
                lines.append(
                    f"- 支撑压力：中期支撑{support_m:.2f} / 中期压力{resistance_m:.2f}"
                )
            else:
                support = tech.get("support")
                resistance = tech.get("resistance")
                if support is not None and resistance is not None:
                    lines.append(f"- 支撑压力：{support:.2f} / {resistance:.2f}")
            change_5d = tech.get("change_5d")
            if change_5d is not None:
                lines.append(f"- 近期表现：5日{change_5d:+.1f}%")
            if tech.get("amplitude") is not None:
                amp = tech.get("amplitude")
                amp5 = tech.get("amplitude_avg5")
                if amp5 is not None:
                    lines.append(f"- 振幅：{amp:.1f}%（5日均{amp5:.1f}%）")
                else:
                    lines.append(f"- 振幅：{amp:.1f}%")

            # 持仓信息
            position = context.portfolio.get_aggregated_position(stock.symbol)
            if position:
                style_labels = {"short": "短线", "swing": "波段", "long": "长线"}
                style = style_labels.get(position.get("trading_style", "swing"), "波段")
                avg_cost = safe_num(position.get("avg_cost"), 1)
                lines.append(
                    f"- 持仓：{position['total_quantity']}股 成本{avg_cost:.2f}（{style}）"
                )

        lines.append("\n请根据以上信息，给出今日交易展望。")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _parse_suggestions(self, content: str, watchlist: list) -> dict[str, dict]:
        """
        从 AI 响应中解析个股建议
        返回: {symbol: {action, action_label, reason, should_alert}}
        """
        suggestions: dict[str, dict] = {}
        if not content or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        name_map: dict[str, str] = {}

        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym
            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = "SH" if sym.startswith("6") or sym.startswith("000") else "SZ"
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym
            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(PREMARKET_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            m = re.search(r"[「【\[]\s*(?P<sym>[A-Za-z]{1,5}|\d{3,6})\s*[」】\]]", line)
            sym_raw = m.group("sym") if m else ""

            if not sym_raw:
                m = re.search(r"\(\s*(?P<sym>[A-Za-z]{1,5}|\d{3,6})\s*\)", line)
                sym_raw = m.group("sym") if m else ""

            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z]{1,5}|\d{3,6})\b", line)
                sym_raw = m.group("sym") if m else ""

            if not sym_raw:
                for k in sorted(symbol_map.keys(), key=len, reverse=True):
                    if k and k in line.upper():
                        sym_raw = k
                        break

            if not sym_raw:
                for name, sym in name_map.items():
                    if name and name in line:
                        sym_raw = sym
                        break

            if not sym_raw:
                continue

            sym_key = sym_raw.strip()
            canonical = symbol_map.get(sym_key.upper()) or symbol_map.get(sym_key)
            if not canonical and sym_key.isdigit():
                canonical = symbol_map.get(sym_key)

            if not canonical or canonical not in symbol_set:
                continue

            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[：:：\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = PREMARKET_ACTION_MAP.get(
                action_text, {"action": "watch", "label": "观望"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:100],
                "should_alert": action_info["action"] in ["buy", "add", "reduce"],
            }

        return suggestions

    def _parse_suggestions_json(self, obj: dict, watchlist: list) -> dict[str, dict]:
        suggestions: dict[str, dict] = {}
        items = obj.get("suggestions")
        if not isinstance(items, list) or not watchlist:
            return suggestions

        symbol_set = {s.symbol for s in watchlist}
        symbol_map: dict[str, str] = {}
        for s in watchlist:
            sym = (s.symbol or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym
            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym
            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = "SH" if sym.startswith("6") or sym.startswith("000") else "SZ"
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym

        for it in items:
            if not isinstance(it, dict):
                continue
            sym_raw = (it.get("symbol") or "").strip()
            canonical = symbol_map.get(sym_raw.upper()) or symbol_map.get(sym_raw)
            if not canonical or canonical not in symbol_set:
                continue
            action = (it.get("action") or "watch").strip()
            action_label = (it.get("action_label") or "观望").strip()
            reason = (it.get("reason") or "").strip()
            signal = (it.get("signal") or "").strip()
            suggestions[canonical] = {
                "action": action,
                "action_label": action_label,
                "reason": reason[:160],
                "signal": signal[:60],
                "triggers": it.get("triggers")
                if isinstance(it.get("triggers"), list)
                else [],
                "invalidations": it.get("invalidations")
                if isinstance(it.get("invalidations"), list)
                else [],
                "risks": it.get("risks") if isinstance(it.get("risks"), list) else [],
                "should_alert": action in ["buy", "add", "reduce"],
            }
        return suggestions

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """调用 AI 分析并保存到历史/建议池"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        if context.model_label:
            idx = content.rfind(TAG_START)
            if idx >= 0:
                content = (
                    content[:idx].rstrip()
                    + f"\n\n---\nAI: {context.model_label}\n\n"
                    + content[idx:]
                )
            else:
                content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        structured = try_extract_tagged_json(content) or {}
        display_content = strip_tagged_json(content)

        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # 解析个股建议
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
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
                    signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                    reason=sug.get("reason", ""),
                    agent_name=self.name,
                    agent_label=self.display_name,
                    expires_hours=12,  # 盘前建议当日有效
                    prompt_context=user_content,
                    ai_response=result.content,
                    meta={
                        "analysis_date": (data.get("timestamp") or "")[:10],
                        "source": "premarket_outlook",
                        "plan": {
                            "triggers": sug.get("triggers")
                            if isinstance(sug.get("triggers"), list)
                            else [],
                            "invalidations": sug.get("invalidations")
                            if isinstance(sug.get("invalidations"), list)
                            else [],
                            "risks": sug.get("risks")
                            if isinstance(sug.get("risks"), list)
                            else [],
                        }
                        if isinstance(sug, dict)
                        else {},
                    },
                )

        # 保存到历史记录
        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "us_indices": data.get("us_indices"),
                "timestamp": data.get("timestamp"),
                "suggestions": suggestions,
            },
        )
        logger.info(f"盘前分析已保存到历史记录，包含 {len(suggestions)} 条建议")

        return result
