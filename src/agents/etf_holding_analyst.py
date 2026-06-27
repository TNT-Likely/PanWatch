"""场内 ETF 成分股分析 Agent。

借鉴 Pan1Watch fund_holding_analyst 的"重仓股与持仓重叠"思路,但面向场内 ETF:
- 取 ETF 实时行情(IOPV/折溢价/规模) + 成分股
- 计算成分股与用户现有持仓的重叠(机构共识 / 风口暴露)
- AI 输出结构化建议(hold/add/reduce/dca/watch),入 suggestion_pool 与 analysis_history

ETF 视角与个股不同:无个股财报,分析重心在跟踪指数偏离、成分股权重集中度、
与持仓的重叠度,而非 EPS/PE。prompt 显式声明这一点,防 AI 虚构基本面。

仅对 security_type=="etf" 的标的生效,普通股票自动跳过。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from src.agents.base import AgentContext, AnalysisResult, BaseAgent
from src.collectors.etf_collector import get_etf_holdings, get_etf_spot
from src.core.analysis_history import save_analysis
from src.core.suggestion_pool import save_suggestion
from src.core.signals.structured_output import (
    strip_tagged_json,
    try_extract_tagged_json,
)

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "etf_holding_analyst.md"

# ETF 建议的有效期:成分股季报更新慢,建议有效期放长
_SUGGESTION_EXPIRE_HOURS = 48


class EtfHoldingAnalystAgent(BaseAgent):
    """ETF 成分股分析 Agent"""

    name = "etf_holding_analyst"
    display_name = "ETF 成分分析"
    description = "分析场内 ETF 的成分股、折溢价、与持仓重叠,给出结构化建议"

    def __init__(self, top_holdings: int = 15) -> None:
        self.top_holdings = top_holdings

    async def collect(self, context: AgentContext) -> dict:
        """采集 ETF 行情 + 成分股,并标注与用户持仓的重叠。"""
        # 单只触发:watchlist 应只有 1 只 ETF
        etf = next((s for s in context.watchlist if (s.security_type or "stock") == "etf"), None)
        if etf is None:
            # 非ETF标的,跳过(返回空数据,build_prompt 仍会被调用但产出空内容)
            return {
                "symbol": context.watchlist[0].symbol if context.watchlist else "",
                "name": context.watchlist[0].name if context.watchlist else "",
                "spot": None,
                "holdings": [],
                "skipped": "not_etf",
            }

        spot = get_etf_spot(etf.symbol)
        holdings = get_etf_holdings(etf.symbol, top=self.top_holdings)

        # 计算成分股与用户持仓的重叠
        portfolio_symbols = {
            p.symbol for p in context.portfolio.all_positions
        } if context.portfolio else set()
        portfolio_map = {p.symbol: p for p in (context.portfolio.all_positions if context.portfolio else [])}

        enriched = []
        for h in holdings:
            in_portfolio = h["symbol"] in portfolio_symbols
            pos = portfolio_map.get(h["symbol"])
            enriched.append({
                **h,
                "in_portfolio": in_portfolio,
                "portfolio_qty": getattr(pos, "quantity", None) if pos else None,
            })

        return {
            "symbol": etf.symbol,
            "name": etf.name,
            "spot": spot,
            "holdings": enriched,
            "portfolio_overlap_count": sum(1 for h in enriched if h["in_portfolio"]),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """构建 prompt:声明 ETF 视角 + 注入行情/成分股/重叠数据。"""
        system_prompt = _load_prompt()

        symbol = data.get("symbol", "")
        name = data.get("name", "")
        spot = data.get("spot") or {}
        holdings = data.get("holdings") or []
        overlap_count = data.get("portfolio_overlap_count", 0)

        # 行情摘要
        spot_lines = []
        if spot:
            spot_lines.append(f"最新价: {spot.get('price')}")
            spot_lines.append(f"IOPV实时估值: {spot.get('iopv')}")
            spot_lines.append(f"折溢价率: {spot.get('premium_pct')}% (正为溢价)")
            spot_lines.append(f"基金规模: {_fmt_money(spot.get('total_value'))}")
            spot_lines.append(f"成交额: {_fmt_money(spot.get('turnover'))}")
            spot_lines.append(f"涨跌幅: {spot.get('change_pct')}%")
        else:
            spot_lines.append("暂无实时行情数据")

        # 成分股表
        if holdings:
            lines = ["| # | 代码 | 名称 | 占净值 | 在持仓 |", "|---|---|---|---|---|"]
            for i, h in enumerate(holdings, 1):
                mark = "✓" if h.get("in_portfolio") else ""
                lines.append(
                    f"| {i} | {h['symbol']} | {h['name']} | {h['weight_pct']:.2f}% | {mark} |"
                )
            holdings_table = "\n".join(lines)
        else:
            holdings_table = "暂无成分股数据(季报披露后更新)"

        top5_weight = sum(h["weight_pct"] for h in holdings[:5])
        top10_weight = sum(h["weight_pct"] for h in holdings[:10])

        user_content = f"""## 标的
{name}({symbol}) — 场内 ETF

## 实时行情
{chr(10).join(spot_lines)}

## 成分股(前 {len(holdings)} 大)
{holdings_table}

## 集中度
前5大权重合计: {top5_weight:.2f}%
前10大权重合计: {top10_weight:.2f}%

## 与用户持仓重叠
成分股中有 {overlap_count} 只已在用户持仓中。
"""
        return system_prompt, user_content

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """调用 AI + 解析结构化建议 + 落库。"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        structured = try_extract_tagged_json(content) or {}
        display_content = strip_tagged_json(content)

        if context.model_label:
            display_content = display_content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        symbol = data.get("symbol", "")
        name = data.get("name", symbol)
        title = f"【{self.display_name}】{name}({symbol})"

        # 解析建议
        suggestions = self._parse_suggestions(structured, symbol, name)
        result = AnalysisResult(
            agent_name=self.name,
            title=title,
            content=display_content,
            raw_data={**data, "structured": structured, "suggestions": suggestions},
        )

        # 落库:analysis_history + suggestion_pool
        save_analysis(
            agent_name=self.name,
            stock_symbol=symbol,
            content=display_content,
            title=title,
            raw_data=data,
        )
        for sug in suggestions:
            save_suggestion(
                stock_symbol=symbol,
                stock_name=name,
                action=sug["action"],
                action_label=sug["action_label"],
                agent_name=self.name,
                agent_label=self.display_name,
                reason=sug.get("reason", ""),
                expires_hours=_SUGGESTION_EXPIRE_HOURS,
                prompt_context=user_content,
                ai_response=content,
                stock_market="CN",
                meta={
                    "source": "etf_holding_analyst",
                    "premium_pct": (data.get("spot") or {}).get("premium_pct"),
                    "overlap_count": data.get("portfolio_overlap_count"),
                },
            )

        return result

    def _parse_suggestions(
        self, structured: dict, fallback_symbol: str, fallback_name: str
    ) -> list[dict]:
        """从结构化 JSON 解析建议列表(兼容单条/多条)。"""
        items = structured.get("suggestions") or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []

        result = []
        for it in items:
            if not isinstance(it, dict):
                continue
            action = (it.get("action") or "hold").strip().lower()
            if action not in ("hold", "add", "reduce", "sell", "watch", "dca"):
                action = "hold"
            result.append({
                "symbol": it.get("symbol") or fallback_symbol,
                "name": it.get("name") or fallback_name,
                "action": action,
                "action_label": it.get("action_label") or _ACTION_LABELS.get(action, action),
                "reason": it.get("reason", ""),
            })
        return result


_ACTION_LABELS = {
    "hold": "持有",
    "add": "加仓",
    "reduce": "减仓",
    "sell": "卖出",
    "watch": "观察",
    "dca": "定投",
}


def _fmt_money(v) -> str:
    """金额格式化:亿/万。"""
    if v is None:
        return "--"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "--"
    if f >= 1e8:
        return f"{f / 1e8:.2f} 亿"
    if f >= 1e4:
        return f"{f / 1e4:.2f} 万"
    return str(f)


def _load_prompt() -> str:
    """加载 prompt 模板(缓存)。"""
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        # 兜底:文件缺失时用内联最小 prompt
        return (
            "你是场内 ETF 分析师。注意:ETF 无个股财报,不要虚构 EPS/PE 等基本面数据。"
            "分析重心:跟踪指数偏离、成分股集中度、折溢价、与用户持仓的重叠度。"
            "输出 markdown 报告,结尾附 <!--PANWATCH_JSON--> 结构化建议。"
        )
