"""新闻速递 Agent - 自选股相关新闻摘要"""

import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.news_collector import NewsCollector, NewsItem
from src.core.analysis_history import save_analysis
from src.core.cn_symbol import get_cn_prefix
from src.core.suggestion_pool import save_suggestion
from src.core.signals import SignalPackBuilder
from src.core.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "news_digest.txt"

# 新闻速递建议类型映射（偏“消息面”）
NEWS_ACTION_MAP = {
    "设置预警": {"action": "alert", "label": "设置预警"},
    "关注": {"action": "watch", "label": "关注"},
    "继续持有": {"action": "hold", "label": "继续持有"},
    "考虑减仓": {"action": "reduce", "label": "考虑减仓"},
    "暂时回避": {"action": "avoid", "label": "暂时回避"},
}


class NewsDigestAgent(BaseAgent):
    """新闻速递 Agent"""

    name = "news_digest"
    display_name = "新闻速递"
    description = "定时抓取与持仓相关的新闻资讯并推送摘要"

    def __init__(self, since_hours: int = 12, fallback_since_hours: int = 24):
        """
        Args:
            since_hours: 获取最近 N 小时的新闻
            fallback_since_hours: 当近 N 小时无新闻时，自动回退到更长时间窗（避免“空跑”）
        """
        self.since_hours = since_hours
        self.fallback_since_hours = fallback_since_hours

    def _dedupe_with_db(self, items: list[NewsItem]) -> list[NewsItem]:
        """使用 NewsCache 表去重（跨进程/重启也有效），避免重复推送同一条新闻。"""
        if not items:
            return []

        from src.web.database import SessionLocal
        from src.web.models import NewsCache

        db = SessionLocal()
        try:
            by_source: dict[str, list[str]] = {}
            for it in items:
                if not it.external_id:
                    continue
                by_source.setdefault(it.source, []).append(it.external_id)

            existing: set[tuple[str, str]] = set()
            for source, ids in by_source.items():
                if not ids:
                    continue
                rows = (
                    db.query(NewsCache.external_id)
                    .filter(NewsCache.source == source, NewsCache.external_id.in_(ids))
                    .all()
                )
                existing.update((source, r[0]) for r in rows)

            new_items: list[NewsItem] = []
            for it in items:
                if it.external_id and (it.source, it.external_id) in existing:
                    continue

                new_items.append(it)
                if it.external_id:
                    # 写入缓存表（内容适度截断，避免膨胀）
                    try:
                        db.add(
                            NewsCache(
                                source=it.source,
                                external_id=it.external_id,
                                title=it.title or "",
                                content=(it.content or "")[:2000],
                                publish_time=it.publish_time,
                                symbols=it.symbols or [],
                                importance=it.importance or 0,
                            )
                        )
                    except Exception:
                        # 单条写入失败不影响本次返回
                        pass

            db.commit()
            return new_items
        except Exception as e:
            logger.warning(f"NewsCache 去重失败，回退为不去重: {e}")
            db.rollback()
            return items
        finally:
            db.close()

    async def collect(self, context: AgentContext) -> dict:
        """采集新闻（自选股相关 + 重要市场新闻）"""
        symbols = [stock.symbol for stock in context.watchlist]

        if not symbols:
            logger.warning("自选股列表为空，跳过新闻采集")
            return {"news": [], "related_news": [], "watchlist": []}

        collector = NewsCollector.from_database()
        since_hours_used = self.since_hours
        news_list = await collector.fetch_all(
            symbols=symbols,
            since_hours=self.since_hours,
        )
        if (
            not news_list
            and self.fallback_since_hours
            and self.fallback_since_hours > self.since_hours
        ):
            logger.info(
                f"近 {self.since_hours} 小时无新闻，回退到近 {self.fallback_since_hours} 小时"
            )
            since_hours_used = self.fallback_since_hours
            news_list = await collector.fetch_all(
                symbols=symbols,
                since_hours=self.fallback_since_hours,
            )

        # 跨次去重：只保留“新新闻”，避免 agent 看起来一直在重复同样内容
        news_list = self._dedupe_with_db(news_list)

        # 分类：自选股相关 + 重要市场新闻
        related_news = self._filter_related_news(news_list, symbols)
        important_news = [
            n for n in news_list if n.importance >= 2 and n not in related_news
        ]

        # 结构化信号：补充行情/技术/资金/持仓，提高“建议摘要”稳定性
        packs = {}
        try:
            builder = SignalPackBuilder()
            sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
            packs = await builder.build_for_symbols(
                symbols=sym_list,
                include_news=False,
                news_hours=self.since_hours,
                portfolio=context.portfolio,
                include_technical=True,
                include_capital_flow=True,
                include_events=True,
                events_days=3,
            )
        except Exception as e:
            logger.warning(f"SignalPack 获取失败（news_digest 继续执行）：{e}")

        return {
            "news": news_list,  # 全部新闻
            "related_news": related_news,  # 自选股相关
            "important_news": important_news,  # 重要市场新闻
            "watchlist": context.watchlist,
            "signal_packs": packs,
            "timestamp": datetime.now().isoformat(),
            "since_hours_used": since_hours_used,
        }

    def _filter_related_news(
        self, news_list: list[NewsItem], symbols: list[str]
    ) -> list[NewsItem]:
        """过滤与自选股相关的新闻"""
        related = []
        for news in news_list:
            # 新闻已标注股票
            if news.symbols and any(s in symbols for s in news.symbols):
                related.append(news)
                continue
            # 检查标题/内容是否包含股票代码
            text = news.title + news.content
            if any(s in text for s in symbols):
                related.append(news)

        return related

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建新闻速递 Prompt"""
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")

        lines = []
        since_hours_used = data.get("since_hours_used") or self.since_hours
        lines.append(f"## 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"## 时间窗：近 {since_hours_used} 小时\n")

        # 自选股列表（标记持仓）
        lines.append("## 自选股")
        watchlist_map = {s.symbol: s for s in context.watchlist}
        packs = data.get("signal_packs", {}) or {}
        for stock in context.watchlist:
            pack = packs.get(stock.symbol)
            position = context.portfolio.get_aggregated_position(stock.symbol)

            extra_parts = []
            if pack and pack.quote:
                try:
                    extra_parts.append(
                        f"现价{pack.quote.current_price:.2f}({pack.quote.change_pct:+.2f}%)"
                    )
                except Exception:
                    pass
            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                if tech.get("trend"):
                    extra_parts.append(f"趋势{tech.get('trend')}")
                if tech.get("macd_status"):
                    extra_parts.append(f"MACD {tech.get('macd_status')}")
            flow = (pack.capital_flow if pack else None) or {}
            if flow and not flow.get("error") and flow.get("status"):
                extra_parts.append(f"资金{flow.get('status')}")

            extra = (" | " + " ".join(extra_parts)) if extra_parts else ""
            if position:
                lines.append(
                    f"- {stock.name}({stock.symbol}) [持仓{position['total_quantity']}股]{extra}"
                )
            else:
                lines.append(f"- {stock.name}({stock.symbol}){extra}")

        # 自选股相关新闻
        related_news: list[NewsItem] = data.get("related_news", [])
        lines.append(f"\n## 自选股相关新闻 ({len(related_news)} 条)")
        if related_news:
            for news in related_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- 暂无自选股相关新闻")

        # 重要市场新闻
        important_news: list[NewsItem] = data.get("important_news", [])
        lines.append(f"\n## 重要市场新闻 ({len(important_news)} 条)")
        if important_news:
            for news in important_news[:10]:
                self._format_news_item(lines, news, watchlist_map)
        else:
            lines.append("- 暂无重要市场新闻")

        user_content = "\n".join(lines)
        return system_prompt, user_content

    def _format_news_item(
        self, lines: list[str], news: NewsItem, watchlist_map: dict
    ) -> None:
        """格式化单条新闻"""
        importance_label = ["", "[一般]", "[重要]", "[重大]"][min(news.importance, 3)]
        time_str = news.publish_time.strftime("%H:%M")
        source_label = {"sina": "新浪", "eastmoney": "东财"}.get(
            news.source, news.source
        )

        # 关联股票名称
        stock_names = []
        for symbol in news.symbols:
            if symbol in watchlist_map:
                stock_names.append(watchlist_map[symbol].name)
        stock_info = f"[{','.join(stock_names)}] " if stock_names else ""

        link = f" ([原文]({news.url}))" if news.url else ""
        lines.append(
            f"- {importance_label} [{source_label} {time_str}] {stock_info}{news.title}{link}"
        )
        if news.content:
            content_brief = news.content[:200] + (
                "..." if len(news.content) > 200 else ""
            )
            lines.append(f"  > {content_brief}")

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
            sym = (getattr(s, "symbol", "") or "").strip()
            if not sym:
                continue
            symbol_map[sym.upper()] = sym

            if getattr(s, "market", None) == MarketCode.HK and sym.isdigit():
                try:
                    symbol_map[str(int(sym))] = sym  # 兼容去掉前导 0（如 00700 -> 700）
                except ValueError:
                    pass
                symbol_map[f"HK{sym}"] = sym
                symbol_map[f"{sym}.HK"] = sym

            if (
                getattr(s, "market", None) == MarketCode.CN
                and sym.isdigit()
                and len(sym) == 6
            ):
                prefix = get_cn_prefix(sym, upper=True)
                symbol_map[f"{prefix}{sym}"] = sym
                symbol_map[f"{sym}.{prefix}"] = sym

            if getattr(s, "name", ""):
                name_map[s.name] = sym

        action_texts = list(NEWS_ACTION_MAP.keys())
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            action_text = next((t for t in action_texts if t in line), None)
            if not action_text:
                continue

            # 1) 优先匹配「...」/【...】里的代码
            m = re.search(
                r"[「【\[]\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*[」】\]]",
                line,
            )
            sym_raw = m.group("sym") if m else ""

            # 2) 再匹配括号里的代码（如 腾讯控股(00700)）
            if not sym_raw:
                m = re.search(
                    r"\(\s*(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\s*\)", line
                )
                sym_raw = m.group("sym") if m else ""

            # 3) 再匹配行首代码
            if not sym_raw:
                m = re.match(r"^(?P<sym>[A-Za-z][A-Za-z0-9\.\-]{0,9}|\d{3,6})\b", line)
                sym_raw = m.group("sym") if m else ""

            # 4) 包含方式兜底
            if not sym_raw:
                for k in sorted(symbol_map.keys(), key=len, reverse=True):
                    if k and k in line.upper():
                        sym_raw = k
                        break

            # 5) 名称兜底
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

            # 提取理由：从“建议类型”后截取
            reason = ""
            m_reason = re.search(
                rf"{re.escape(action_text)}\s*[：:：\\-—]?\s*(?P<r>.+)$", line
            )
            if m_reason:
                reason = m_reason.group("r").strip()

            action_info = NEWS_ACTION_MAP.get(
                action_text, {"action": "watch", "label": "关注"}
            )
            suggestions[canonical] = {
                "action": action_info["action"],
                "action_label": action_info["label"],
                "reason": reason[:140],
                "should_alert": action_info["action"] in ["alert", "reduce", "sell"],
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
            sym = (getattr(s, "symbol", "") or "").strip()
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
                prefix = get_cn_prefix(sym, upper=True)
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
            action_label = (it.get("action_label") or "关注").strip()
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
                "should_alert": action in ["alert", "reduce", "sell"],
            }
        return suggestions

    async def should_notify(self, result: AnalysisResult) -> bool:
        """有自选股相关新闻或重要市场新闻时通知"""
        related_news = result.raw_data.get("related_news", [])
        important_news = result.raw_data.get("important_news", [])

        # 有自选股相关新闻
        if related_news:
            return True
        # 有重要市场新闻
        if important_news:
            return True
        return False

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """重写分析：落库到历史，便于在 UI 中查看“新闻速递”产物。"""
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

        stock_items = [
            f"{(s.name or s.symbol).strip()}({s.symbol})"
            for s in context.watchlist[:5]
        ]
        stock_names = "、".join(stock_items) if stock_items else "无股票"
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # 解析个股建议并写入建议池
        suggestions = self._parse_suggestions_json(structured, context.watchlist)
        if not suggestions:
            suggestions = self._parse_suggestions(result.content, context.watchlist)
        result.raw_data["suggestions"] = suggestions
        stock_map = {s.symbol: s for s in context.watchlist}
        for symbol, sug in suggestions.items():
            stock = stock_map.get(symbol)
            if not stock:
                continue
            save_suggestion(
                stock_symbol=symbol,
                stock_name=stock.name,
                action=sug["action"],
                action_label=sug["action_label"],
                signal=(sug.get("signal") or "") if isinstance(sug, dict) else "",
                reason=sug.get("reason", ""),
                agent_name=self.name,
                agent_label=self.display_name,
                expires_hours=12,
                prompt_context=user_content,
                ai_response=result.content,
                stock_market=stock.market.value,
                meta={
                    "source": "news_digest",
                    "since_hours_used": data.get("since_hours_used", self.since_hours),
                    "related_count": len(data.get("related_news", []) or []),
                    "important_count": len(data.get("important_news", []) or []),
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

        # 保存到历史记录（使用 "*" 表示全局）
        related_news: list[NewsItem] = data.get("related_news", []) or []
        important_news: list[NewsItem] = data.get("important_news", []) or []
        payload_news = []
        for it in (related_news + important_news)[:30]:
            payload_news.append(
                {
                    "source": it.source,
                    "external_id": it.external_id,
                    "title": it.title,
                    "publish_time": it.publish_time.isoformat(),
                    "symbols": it.symbols,
                    "importance": it.importance,
                    "url": it.url,
                }
            )

        save_analysis(
            agent_name=self.name,
            stock_symbol="*",
            content=result.content,
            title=result.title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "since_hours": self.since_hours,
                "since_hours_used": data.get("since_hours_used", self.since_hours),
                "related_count": len(related_news),
                "important_count": len(important_news),
                "news": payload_news,
                "suggestions": suggestions,
                "prompt_context": user_content[:2000],
            },
        )

        return result
