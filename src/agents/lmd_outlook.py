"""老马视角 Agent — 将 LMD 产业周期框架与 AlphaMind 数据管道结合。"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path

from src.agents.base import AgentContext, BaseAgent, AnalysisResult
from src.collectors.akshare_collector import _fetch_tencent_quotes, _tencent_symbol
from src.core.lmd_report_snapshot import attach_lmd_snapshot_to_raw_data
from src.core.analysis_history import save_analysis, get_analysis, get_latest_analysis
from src.core.context_builder import ContextBuilder
from src.core.hermes_runner import is_hermes_available, run_hermes_chat
from src.core.signals import SignalPackBuilder
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "lmd_outlook.txt"
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"

# builtin 模式：追加到外部 skill 文件末尾
_PANWATCH_APPENDIX_BUILTIN = """

---

## AlphaMind 批处理模式

你正在智盘 Alpha（AlphaMind）系统中生成**结构化分析报告**，不是实时对话。
- 用户消息已包含系统采集的行情、技术、资金、新闻与持仓，请直接基于这些数据展开分析。
- 不要尝试 WebSearch；缺失的数据诚实标注即可。
- 输出一篇完整 Markdown 报告，遵循上文「输出结构」五段式。
- 若需将 Markdown 写入磁盘：**必须**保存到 `reports/{代码}/` 子目录，禁止写入项目根目录或其它路径。
  - 成稿：`reports/{代码}/{股票名}_{代码}_老马产业周期分析_{YYYYMMDD}.md`
  - 研究底稿：`reports/{代码}/{代码或Ticker}_Research_{YYYYMMDD}.md`
"""

_HERMES_TASK_PREFIX = """你正在智盘 Alpha（AlphaMind）系统中为自选股生成**可入库的完整老马视角产业周期分析报告**（不是情报简报、不是执行摘要）。

【交付物 — 最高优先级，覆盖 profile/SOUL 中的简洁偏好】
- 最终回复 = **一篇完整 Markdown 报告正文**，用户会直接展示在 UI，不会再追问。
- **禁止**输出「报告完成」「执行摘要」「研究阶段 Step 2」「结论 Step 3」等过程性内容；Step 2 研究在工具调用中静默完成。
- **禁止**在最终回复中输出 `review diff`、git diff 或文件变更摘要；用户要看的是报告正文，不是 diff。
- **禁止**使用 delegate_task / subagent 把写报告外包；研究与成稿必须在你本会话的最终回复中完成。
- 正文须含以下二级标题（缺一不可）：
  ## 一、整体定位
  ## 二、五维周期定位
  ## 三、路径推演
  ## 四、诚实边界
  ## 五、风险提示
- 单股报告不少于 **1800 字**；五维须逐项展开（基本面/估值/资金/预期差/时间节奏），引用 AlphaMind 数据与 WebSearch 的具体数字。
- 开头一句非投资建议声明；结尾有风险提示；用第一人称「我」、老马语气。

工作流：
1. 用 WebSearch 等工具完成 Step 2 研究（PE/订单/景气/预期差/产业链）。
2. 在同一轮最终回复中直接输出 Step 3 **完整成稿**（不是摘要）。

【本地文件落盘 — 若写入磁盘】
- **必须**保存到 AlphaMind 项目下的 `reports/{代码}/` 子目录，禁止写入项目根目录或其它路径。
- 老马视角成稿：`reports/{代码}/{股票名}_{代码}_老马产业周期分析_{YYYYMMDD}.md`
- Step 2 研究底稿（英文/中文均可）：`reports/{代码}/{代码或Ticker}_Research_{YYYYMMDD}.md`

---

"""


def _strip_yaml_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            return text[end + 3 :].lstrip()
    return text


def load_lmd_system_prompt(skill_path: str = "") -> str:
    """加载系统 Prompt：优先用户指定的 skill 文件，否则用内置 prompts/lmd_outlook.txt。"""
    if skill_path:
        path = Path(skill_path).expanduser()
        if path.is_file():
            raw = _strip_yaml_frontmatter(path.read_text(encoding="utf-8"))
            logger.info("lmd_outlook 使用外部 skill: %s", path)
            return raw + _PANWATCH_APPENDIX_BUILTIN
        logger.warning("lmd_outlook skill_path 不存在，回退内置 prompt: %s", path)
    return PROMPT_PATH.read_text(encoding="utf-8")


async def _fetch_fundamental_line(symbol: str, market: MarketCode) -> str:
    """腾讯行情中的 PE / 市值 / 换手率摘要。"""
    try:
        rows = await asyncio.to_thread(
            _fetch_tencent_quotes, [_tencent_symbol(symbol, market)]
        )
        if not rows:
            return ""
        q = rows[0]
        parts: list[str] = []
        if q.get("pe_ratio") not in (None, 0):
            parts.append(f"市盈率 {q['pe_ratio']}")
        if q.get("turnover_rate") not in (None, 0):
            parts.append(f"换手率 {q['turnover_rate']}%")
        if q.get("circulating_market_value"):
            parts.append(f"流通市值 {q['circulating_market_value']}亿")
        if q.get("total_market_value"):
            parts.append(f"总市值 {q['total_market_value']}亿")
        return "，".join(parts)
    except Exception as e:
        logger.debug("lmd_outlook 基本面采集失败 %s: %s", symbol, e)
        return ""


class LmdOutlookAgent(BaseAgent):
    """老马视角 — 产业周期 × 情绪博弈框架下的单股/组合分析。"""

    name = "lmd_outlook"
    display_name = "老马视角"
    description = (
        "以老马投资研究的产业周期×情绪博弈框架，"
        "结合行情/技术/资金/新闻生成深度分析报告（手动触发）"
    )

    def __init__(
        self,
        skill_path: str = "",
        engine: str = "hermes",
        hermes_skill: str = "lmd-finance-perspective",
        hermes_bin: str = "",
        hermes_profile: str = "agent-1-qingbaoxianfeng",
        hermes_skill_source_dir: str = "",
        hermes_max_turns: int = 40,
        hermes_timeout_sec: int = 420,
        hermes_followup_timeout_sec: int = 300,
        hermes_model: str = "",
        hermes_ignore_rules: bool = True,
        hermes_auto_expand_summary: bool = True,
    ):
        self.skill_path = (skill_path or "").strip()
        self.engine = (engine or "hermes").strip().lower()
        self.hermes_skill = (hermes_skill or "lmd-finance-perspective").strip()
        self.hermes_bin = (hermes_bin or "").strip()
        self.hermes_profile = (hermes_profile or "").strip()
        self.hermes_skill_source_dir = (hermes_skill_source_dir or "").strip()
        self.hermes_max_turns = int(hermes_max_turns or 40)
        self.hermes_timeout_sec = int(hermes_timeout_sec or 420)
        self.hermes_followup_timeout_sec = int(hermes_followup_timeout_sec or 300)
        self.hermes_model = (hermes_model or "").strip()
        self.hermes_ignore_rules = bool(hermes_ignore_rules)
        self.hermes_auto_expand_summary = bool(hermes_auto_expand_summary)

    async def collect(self, context: AgentContext) -> dict:
        if not context.watchlist:
            raise RuntimeError("自选股列表为空，无法生成老马视角报告")

        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        builder = SignalPackBuilder()
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
            persist_snapshot=True,
        )

        if not any(p.quote for p in packs.values()):
            raise RuntimeError("数据采集失败：未获取到任何行情数据")

        fundamentals: dict[str, str] = {}
        for w in context.watchlist:
            line = await _fetch_fundamental_line(w.symbol, w.market)
            if line:
                fundamentals[w.symbol] = line

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
            "signal_packs": packs,
            "symbol_contexts": context_pack.get("symbols", {}),
            "quality_overview": context_pack.get("quality_overview", {}),
            "fundamentals": fundamentals,
            "daily_analysis": daily_analysis.content if daily_analysis else None,
            "premarket_analysis": premarket_analysis.content
            if premarket_analysis
            else None,
            "timestamp": datetime.now().isoformat(),
            "engine": self.engine,
        }

    def build_user_content(
        self, data: dict, context: AgentContext, *, for_hermes: bool = False
    ) -> str:
        def safe_num(value, default=0):
            return value if value is not None else default

        lines: list[str] = []
        if for_hermes:
            lines.append(_HERMES_TASK_PREFIX)

        lines.append(f"## 日期：{datetime.now().strftime('%Y-%m-%d')}\n")
        symbol_contexts = data.get("symbol_contexts", {}) or {}
        quality_overview = data.get("quality_overview", {}) or {}
        packs = data.get("signal_packs", {}) or {}
        fundamentals = data.get("fundamentals", {}) or {}

        if quality_overview:
            lines.append("## 上下文质量概览")
            lines.append(
                f"- 平均质量分：{quality_overview.get('avg_score', 0)}"
                f"（最低 {quality_overview.get('min_score', 0)}"
                f" / 最高 {quality_overview.get('max_score', 0)}）"
            )
            lines.append("")

        if data.get("premarket_analysis"):
            lines.append("## 今日盘前分析摘要（供参考）")
            lines.append(data["premarket_analysis"][:1500])
            lines.append("")

        if data.get("daily_analysis"):
            lines.append("## 最近收盘复盘摘要（供参考）")
            lines.append(data["daily_analysis"][:1500])
            lines.append("")

        for w in context.watchlist:
            pack = packs.get(w.symbol)
            sym_ctx = symbol_contexts.get(w.symbol, {}) or {}
            lines.append(f"## {w.name}（{w.symbol}）")

            fund_line = fundamentals.get(w.symbol)
            if fund_line:
                lines.append(f"- 估值快照（AlphaMind/腾讯）：{fund_line}")

            quote = pack.quote if pack else None
            if quote:
                change_pct = safe_num(quote.change_pct)
                lines.append(
                    f"- 现价 {safe_num(quote.current_price):.2f}，"
                    f"涨跌 {change_pct:+.2f}%，"
                    f"成交额 {safe_num(quote.turnover) / 1e8:.2f} 亿"
                )

            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                parts = []
                if tech.get("trend"):
                    parts.append(f"趋势:{tech['trend']}")
                if tech.get("ma_arrangement"):
                    parts.append(f"均线:{tech['ma_arrangement']}")
                if tech.get("macd_signal"):
                    parts.append(f"MACD:{tech['macd_signal']}")
                if tech.get("change_5d") is not None:
                    parts.append(f"5日{tech['change_5d']:+.1f}%")
                if tech.get("change_20d") is not None:
                    parts.append(f"20日{tech['change_20d']:+.1f}%")
                if parts:
                    lines.append(f"- 技术面：{' | '.join(parts)}")

            capital = (pack.capital_flow if pack else None) or {}
            if capital and not capital.get("error"):
                main_net = capital.get("main_net_inflow")
                if main_net is not None:
                    lines.append(f"- 主力净流入：{main_net / 1e4:.0f} 万")

            position = context.portfolio.get_aggregated_position(w.symbol)
            if position:
                lines.append(
                    f"- 持仓：{position['total_quantity']} 股，"
                    f"成本 {position['avg_cost']:.2f}，"
                    f"浮盈 {position.get('profit_pct', 0):+.1f}%"
                )
            else:
                lines.append("- 持仓：未持有")

            if for_hermes:
                kline_hist = sym_ctx.get("kline_history") or {}
                if kline_hist.get("available"):
                    summary = (kline_hist.get("summary") or "").strip()
                    breakout = (kline_hist.get("breakout_state") or "").strip()
                    if summary or breakout:
                        lines.append("- K线历史：")
                        if summary:
                            lines.append(f"  - {summary[:200]}")
                        if breakout:
                            lines.append(f"  - 突破状态: {breakout}")

                events = sym_ctx.get("events") or []
                if events:
                    lines.append("- 近期公告/事件：")
                    for ev in events[:5]:
                        title = str(ev.get("title") or ev.get("name") or "").strip()
                        if title:
                            lines.append(f"  - {title[:80]}")

            layered_news = (sym_ctx.get("news") or {}) if sym_ctx else {}
            news_buckets = (
                [("近期", layered_news.get("realtime") or [])]
                if not for_hermes
                else [
                    ("实时", layered_news.get("realtime") or []),
                    ("扩展", layered_news.get("extended") or []),
                ]
            )
            for label, items in news_buckets:
                slice_items = items[:5] if label == "近期" else items[:4]
                if slice_items:
                    lines.append(f"- {label}新闻：")
                    for item in slice_items:
                        title = (item.get("title") or "").strip()
                        if title:
                            lines.append(f"  - {title[:80]}")

            lines.append("")

        if len(context.watchlist) == 1:
            w0 = context.watchlist[0]
            user_hint = (
                f"请对 {w0.name}（{w0.symbol}）"
                "生成一篇完整的老马视角产业周期分析报告。"
            )
        else:
            names = "、".join(s.name for s in context.watchlist[:5])
            user_hint = (
                f"请对以下自选股分别生成老马视角分析，并给出组合层面的周期观察：{names}"
            )

        lines.append(f"\n## 任务\n{user_hint}")
        return "\n".join(lines)

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        system_prompt = load_lmd_system_prompt(self.skill_path)
        return system_prompt, self.build_user_content(data, context, for_hermes=False)

    def _build_title(self, context: AgentContext) -> str:
        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        return f"【{self.display_name}】{stock_names}"

    async def _analyze_via_hermes(
        self, context: AgentContext, data: dict
    ) -> AnalysisResult:
        user_content = self.build_user_content(data, context, for_hermes=True)
        symbol = context.watchlist[0].symbol if context.watchlist else ""
        content = await run_hermes_chat(
            query=user_content,
            skill=self.hermes_skill,
            hermes_bin=self.hermes_bin,
            hermes_profile=self.hermes_profile,
            skill_source_dir=self.hermes_skill_source_dir,
            max_turns=self.hermes_max_turns,
            timeout_sec=float(self.hermes_timeout_sec),
            followup_timeout_sec=float(self.hermes_followup_timeout_sec),
            model=self.hermes_model,
            ignore_rules=self.hermes_ignore_rules,
            auto_expand_summary=self.hermes_auto_expand_summary,
            report_fallback_dir=REPORTS_DIR,
            report_fallback_symbol=symbol,
            report_fallback_date=date.today(),
        )
        profile_label = self.hermes_profile or "default"
        if context.model_label:
            content = (
                content.rstrip()
                + f"\n\n---\nAI: Hermes/{profile_label} ({self.hermes_skill})"
            )
        data = {
            **data,
            "hermes_skill": self.hermes_skill,
            "hermes_profile": self.hermes_profile,
        }
        return AnalysisResult(
            agent_name=self.name,
            title=self._build_title(context),
            content=content,
            raw_data=data,
        )

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        use_hermes = self.engine == "hermes"
        if use_hermes and not is_hermes_available(self.hermes_bin):
            logger.warning("lmd_outlook: Hermes 不可用，回退 builtin LLM")
            use_hermes = False

        actual_engine = "hermes" if use_hermes else "builtin"
        if use_hermes:
            try:
                result = await self._analyze_via_hermes(context, data)
            except Exception as e:
                logger.error("lmd_outlook Hermes 执行失败，回退 builtin: %s", e)
                actual_engine = "builtin"
                result = await super().analyze(context, data)
                result.raw_data = {**(result.raw_data or {}), "hermes_fallback": str(e)}
        else:
            result = await super().analyze(context, data)

        symbol = context.watchlist[0].symbol if context.watchlist else "*"
        try:
            save_analysis(
                agent_name=self.name,
                stock_symbol=symbol,
                content=result.content,
                title=result.title,
                raw_data=attach_lmd_snapshot_to_raw_data(
                    {
                        "timestamp": data.get("timestamp"),
                        "quality_overview": data.get("quality_overview"),
                        "engine": actual_engine,
                    },
                    result.content,
                    report_date=date.today().strftime("%Y-%m-%d"),
                ),
            )
        except Exception as e:
            logger.warning("lmd_outlook save_analysis 失败,不影响主流程: %s", e)
        return result

    async def should_notify(self, result) -> bool:
        return True
