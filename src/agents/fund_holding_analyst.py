"""基金分析 Agent

轻量级Agent，只能由基金标的使用，股票无法配置。
功能：
1. 分析基金重仓股与用户持仓的重叠度（机构共识）
2. 周报形式展示基金估值表现、重仓股涨跌情况
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.base import BaseAgent, AgentContext, AnalysisResult
from src.collectors.fund_collector import fetch_fund_top_holdings, fetch_fund_performance
from src.core.analysis_history import save_analysis
from src.core.suggestion_pool import save_suggestion
from src.core.signals.structured_output import (
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "fund_holding_analyst.md"

# 建议类型映射
FUND_ACTION_MAP = {
    "继续持有": {"action": "hold", "label": "继续持有"},
    "考虑加仓": {"action": "add", "label": "考虑加仓"},
    "考虑减仓": {"action": "reduce", "label": "考虑减仓"},
    "定投买入": {"action": "dca", "label": "定投买入"},
    "暂时观望": {"action": "watch", "label": "暂时观望"},
}


class FundHoldingAnalystAgent(BaseAgent):
    """基金分析 Agent - 仅适用于基金标的"""

    name = "fund_holding_analyst"
    display_name = "基金分析"
    description = "分析基金重仓股与持仓重叠度，跟踪基金业绩与重仓股表现（仅基金可用）"
    # 标记此Agent仅限基金使用
    market_filter = ["FUND"]

    async def collect(self, context: AgentContext) -> dict:
        """采集基金数据：重仓股、业绩走势、与用户持仓的重叠"""

        fund_data_list = []

        # 只处理基金类型的标的
        fund_watchlist = [s for s in context.watchlist if s.market.value == "FUND"]

        if not fund_watchlist:
            raise RuntimeError("当前关注列表中没有基金，此Agent仅适用于基金标的")

        # 获取用户所有持仓股票代码（用于计算机构共识）
        user_stock_symbols = set()
        for pos in context.portfolio.all_positions:
            if pos.market.value != "FUND":
                user_stock_symbols.add(pos.symbol)

        for fund in fund_watchlist:
            fund_code = fund.symbol
            try:
                # 获取重仓股
                holdings = fetch_fund_top_holdings(fund_code, topline=10)
                # 获取业绩走势
                perf = fetch_fund_performance(fund_code)

                # 计算与用户持仓的重叠
                overlap_stocks = []
                for h in holdings:
                    if h.get("code") in user_stock_symbols:
                        overlap_stocks.append({
                            "code": h.get("code"),
                            "name": h.get("name"),
                            "weight": h.get("weight"),
                            "change_pct": h.get("change_pct"),
                        })

                # 计算近期业绩
                points = perf.get("points", [])
                recent_return = None
                week_return = None
                if points:
                    # 最近一周收益
                    if len(points) >= 5:
                        week_start = points[-5]["value"] if points[-5].get("value") else None
                        week_end = points[-1]["value"] if points[-1].get("value") else None
                        if week_start and week_end and week_start > 0:
                            week_return = (week_end / week_start - 1) * 100
                    # 最近一月收益
                    if len(points) >= 20:
                        month_start = points[-20]["value"] if points[-20].get("value") else None
                        month_end = points[-1]["value"] if points[-1].get("value") else None
                        if month_start and month_end and month_start > 0:
                            recent_return = (month_end / month_start - 1) * 100

                fund_data_list.append({
                    "fund_code": fund_code,
                    "fund_name": fund.name,
                    "holdings": holdings,
                    "overlap_stocks": overlap_stocks,
                    "overlap_count": len(overlap_stocks),
                    "since_return_pct": perf.get("since_return_pct"),
                    "week_return_pct": week_return,
                    "month_return_pct": recent_return,
                    "latest_nav": points[-1]["value"] if points else None,
                    "holding_info": self._get_holding_info(context, fund_code),
                })

            except Exception as e:
                logger.warning(f"获取基金 {fund_code} 数据失败: {e}")
                fund_data_list.append({
                    "fund_code": fund_code,
                    "fund_name": fund.name,
                    "error": str(e),
                })

        if not any(d.get("holdings") for d in fund_data_list):
            raise RuntimeError("未能获取任何基金的持仓数据，请检查网络连接")

        return {
            "fund_data": fund_data_list,
            "user_stock_count": len(user_stock_symbols),
            "timestamp": datetime.now().isoformat(),
        }

    def _get_holding_info(self, context: AgentContext, fund_code: str) -> dict | None:
        """获取用户对该基金的持仓信息"""
        positions = context.portfolio.get_positions_for_stock(fund_code)
        if not positions:
            return None
        total_qty = sum(p.quantity for p in positions)
        total_cost = sum(p.cost_value for p in positions)
        avg_cost = total_cost / total_qty if total_qty > 0 else 0
        return {
            "quantity": total_qty,
            "avg_cost": avg_cost,
            "total_cost": total_cost,
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建分析提示词"""
        lines = [
            "# 基金分析任务",
            "",
            f"**分析时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        fund_data = data.get("fund_data", [])
        user_stock_count = data.get("user_stock_count", 0)

        lines.append(f"## 用户持仓股票数量: {user_stock_count} 只")
        lines.append("")

        for fd in fund_data:
            if fd.get("error"):
                lines.append(f"### {fd['fund_name']}（{fd['fund_code']}）- 数据获取失败")
                lines.append(f"错误: {fd['error']}")
                lines.append("")
                continue

            lines.append(f"### {fd['fund_name']}（{fd['fund_code']}）")
            lines.append("")

            # 持仓信息
            holding = fd.get("holding_info")
            if holding:
                lines.append(
                    f"**用户持仓**: {holding['quantity']} 份，成本 {holding['avg_cost']:.4f}，总投入 {holding['total_cost']:.0f} 元")
            else:
                lines.append("**用户持仓**: 未持有（仅关注）")
            lines.append("")

            # 业绩表现
            lines.append("**业绩表现**:")
            if fd.get("latest_nav"):
                lines.append(f"- 最新净值: {fd['latest_nav']:.4f}")
            if fd.get("week_return_pct") is not None:
                lines.append(f"- 近一周收益: {fd['week_return_pct']:+.2f}%")
            if fd.get("month_return_pct") is not None:
                lines.append(f"- 近一月收益: {fd['month_return_pct']:+.2f}%")
            if fd.get("since_return_pct") is not None:
                lines.append(f"- 成立以来收益: {fd['since_return_pct']:+.2f}%")
            lines.append("")

            # 重仓股
            holdings = fd.get("holdings", [])
            if holdings:
                lines.append("**前10大重仓股**:")
                for h in holdings:
                    change = h.get("change_pct")
                    change_str = f"{change:+.2f}%" if change is not None else "N/A"
                    weight = h.get("weight")
                    weight_str = f"{weight:.2f}%" if weight is not None else h.get("weight_text", "N/A")
                    lines.append(f"- {h.get('name', 'N/A')}（{h.get('code', 'N/A')}）: 占比 {weight_str}，今日 {change_str}")
                lines.append("")

            # 机构共识（与用户持仓重叠）
            overlap = fd.get("overlap_stocks", [])
            if overlap:
                lines.append(f"**机构共识（与你持仓重叠 {len(overlap)} 只）**:")
                for o in overlap:
                    change = o.get("change_pct")
                    change_str = f"{change:+.2f}%" if change is not None else "N/A"
                    lines.append(f"- {o['name']}（{o['code']}）: 基金占比 {o.get('weight', 0):.2f}%，今日 {change_str}")
                lines.append("")
            else:
                lines.append("**机构共识**: 无重叠（该基金重仓股与你的股票持仓无交集）")
                lines.append("")

            lines.append("---")
            lines.append("")

        # 加载提示词模板
        prompt_template = ""
        if PROMPT_PATH.exists():
            prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        else:
            prompt_template = self._default_prompt()

        system_prompt = "你是一位专业的基金分析师，请严格基于输入数据输出结论。"
        user_content = prompt_template.replace("{{DATA}}", "\n".join(lines))
        return system_prompt, user_content

    def _default_prompt(self) -> str:
        """默认提示词"""
        return """你是一位专业的基金分析师，请分析以下基金数据并给出投资建议。

{{DATA}}

## 输出要求

请输出一份简洁的基金周报式分析，包括：
1. **整体概览**：持仓基金的整体表现
2. **逐只分析**：每只基金的业绩点评和重仓股情况
3. **机构共识**：重点分析重仓股与用户持仓的重叠情况，这代表机构对这些股票的认可
4. **操作建议**：给出继续持有/加仓/减仓/定投的建议

请在最后工整输出如下 JSON：
<!--PANWATCH_JSON-->
{
  "funds": {
    "基金代码": {
      "action": "hold/add/reduce/dca/watch",
      "action_label": "继续持有/考虑加仓/考虑减仓/定投买入/暂时观望",
      "reason": "一句话理由"
    }
  }
}
<!--/PANWATCH_JSON-->"""

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """调用 AI 分析，并保存历史与建议"""
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

        fund_watchlist_symbols = [
            s for s in context.watchlist
            if getattr(s, "market", None) and s.market.value == "FUND"
        ]
        fund_items = [
            f"{(s.name or s.symbol).strip()}({s.symbol})"
            for s in fund_watchlist_symbols
        ]
        stock_names = "、".join(fund_items[:5]) if fund_items else "无基金"
        if len(fund_items) > 5:
            stock_names += f" 等{len(fund_items)}只"

        fund_symbol_str = ",".join(s.symbol for s in fund_watchlist_symbols) if fund_watchlist_symbols else "*"

        title = f"【{self.display_name}】{stock_names}"
        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured} if structured else data,
        )

        # 保存历史分析
        save_analysis(
            agent_name=self.name,
            stock_symbol=fund_symbol_str,
            title=title,
            content=result.content,
            raw_data={
                "timestamp": data.get("timestamp"),
                "fund_data": data.get("fund_data", []),
                "user_stock_count": data.get("user_stock_count", 0),
                "structured": structured,
                "prompt_context": user_content[:12000],
            },
        )

        # 保存建议到建议池
        fund_suggestions = structured.get("funds", {}) if isinstance(structured, dict) else {}
        for fd in data.get("fund_data", []):
            fund_code = fd.get("fund_code")
            if not fund_code:
                continue
            fund_name = fd.get("fund_name") or fund_code
            sug = fund_suggestions.get(fund_code, {}) if isinstance(fund_suggestions, dict) else {}

            action = str(sug.get("action") or "watch").strip().lower()
            action_label = str(sug.get("action_label") or "暂时观望").strip()
            reason = str(sug.get("reason") or "").strip()

            if action not in {"hold", "add", "reduce", "dca", "watch"}:
                action = "watch"
            if not action_label:
                action_label = "暂时观望"

            save_suggestion(
                stock_symbol=fund_code,
                stock_name=fund_name,
                action=action,
                action_label=action_label,
                reason=reason,
                agent_name=self.name,
                agent_label=self.display_name,
                stock_market="FUND",
                prompt_context=user_content,
                ai_response=result.content,
                meta={
                    "source": "fund_holding_analyst",
                },
            )

        return result
