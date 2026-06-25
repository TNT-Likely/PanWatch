"""通用本地 Skill 报告 — 复用 PanWatch 数据管道 + Hermes CLI。"""

from __future__ import annotations

import logging
from datetime import date, datetime

from src.agents.base import AgentContext
from src.agents.lmd_outlook import LmdOutlookAgent, _fetch_fundamental_line
from src.core.analysis_history import save_analysis
from src.core.hermes_config import HermesConfig, local_skill_agent_name, load_hermes_config
from src.core.hermes_runner import is_hermes_available, run_hermes_chat
from src.core.signals import SignalPackBuilder
from src.core.context_builder import ContextBuilder
from src.core.analysis_history import get_analysis, get_latest_analysis
from src.models.market import MarketCode
from src.web.database import SessionLocal
from src.web.models import LocalSkill

logger = logging.getLogger(__name__)

_PANWATCH_TASK_PREFIX = """你正在 PanWatch 盯盘系统中为自选股生成**可入库的完整 Markdown 分析报告**（不是情报简报、不是执行摘要）。

要求：
- 最终回复 = **一篇完整 Markdown 报告正文**，用户会直接展示在 UI。
- 基于下方 PanWatch 已采集的数据展开分析；缺失数据请诚实标注。
- 遵循你已加载 skill 中的分析框架与输出格式。
- 开头声明非投资建议；结尾有风险提示。

---

"""


class LocalSkillReportService:
    """对单只股票调用本地 skill + Hermes 生成报告。"""

    def __init__(self, hermes: HermesConfig | None = None):
        self.hermes = hermes or load_hermes_config()

    async def collect(self, context: AgentContext, skill_slug: str) -> dict:
        if not context.watchlist:
            raise RuntimeError("自选股列表为空，无法生成 skill 报告")

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
            agent_name=local_skill_agent_name(skill_slug),
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
            "skill_slug": skill_slug,
        }

    def build_user_content(
        self, data: dict, context: AgentContext, *, skill_display_name: str
    ) -> str:
        lmd = LmdOutlookAgent()
        lmd_body = lmd.build_user_content(data, context, for_hermes=True)
        marker = "## 日期"
        idx = lmd_body.find(marker)
        body = lmd_body[idx:] if idx >= 0 else lmd_body
        prefix = _PANWATCH_TASK_PREFIX.replace(
            "你已加载 skill", f"你已加载 skill「{skill_display_name}」"
        )
        return prefix + body

    async def run_for_stock(
        self,
        context: AgentContext,
        skill_slug: str,
        *,
        skill_display_name: str = "",
        skill_hermes_name: str = "",
    ) -> dict:
        """生成报告并入库，返回 {content, title, agent_name}。"""
        slug = (skill_slug or "").strip()
        if not slug:
            raise ValueError("skill_slug 不能为空")

        if not is_hermes_available(self.hermes.hermes_bin):
            raise RuntimeError(
                "未找到 Hermes CLI，请在「设置 → Hermes」配置可执行路径，"
                "或确保 hermes 在 PATH 中"
            )

        hermes_skill = (skill_hermes_name or slug).strip()
        display = (skill_display_name or slug).strip()

        data = await self.collect(context, slug)
        user_content = self.build_user_content(
            data, context, skill_display_name=display
        )

        content = await run_hermes_chat(
            query=user_content,
            skill=hermes_skill,
            hermes_bin=self.hermes.hermes_bin,
            hermes_profile=self.hermes.hermes_profile,
            skill_source_dir=self.hermes.hermes_skill_source_dir,
            max_turns=self.hermes.hermes_max_turns,
            timeout_sec=float(self.hermes.hermes_timeout_sec),
            followup_timeout_sec=float(self.hermes.hermes_followup_timeout_sec),
            model=self.hermes.hermes_model,
            ignore_rules=self.hermes.hermes_ignore_rules,
            auto_expand_summary=self.hermes.hermes_auto_expand_summary,
        )

        profile_label = self.hermes.hermes_profile or "default"
        content = (
            content.rstrip()
            + f"\n\n---\nAI: Hermes/{profile_label} ({hermes_skill})"
        )

        stock = context.watchlist[0]
        stock_names = stock.name
        title = f"【{display}】{stock_names}（{stock.symbol}）"
        agent_name = local_skill_agent_name(slug)

        save_analysis(
            agent_name=agent_name,
            stock_symbol=stock.symbol,
            content=content,
            title=title,
            raw_data={
                "timestamp": data.get("timestamp"),
                "quality_overview": data.get("quality_overview"),
                "engine": "hermes",
                "skill_slug": slug,
                "skill_display_name": display,
                "hermes_skill": hermes_skill,
                "hermes_profile": self.hermes.hermes_profile,
            },
        )

        return {
            "success": True,
            "content": content,
            "title": title,
            "agent_name": agent_name,
            "message": "ok",
        }


def get_enabled_local_skill(slug: str) -> LocalSkill | None:
    db = SessionLocal()
    try:
        row = (
            db.query(LocalSkill)
            .filter(LocalSkill.slug == slug.strip(), LocalSkill.enabled == True)
            .first()
        )
        return row
    finally:
        db.close()
