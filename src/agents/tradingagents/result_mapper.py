"""TradingAgents 输出 → PanWatch AnalysisResult 映射。

TradingAgents 的 `final_state` 是 LangGraph 累积的 dict,关键字段(摘自上游):
- market_report / social_report / news_report / fundamentals_report: 4 个分析师报告
- investment_debate_state: 看多看空辩论历史 {history, current_response, judge_decision}
- trader_investment_plan: 交易员意见
- risk_judge_decision: 风控判定
- final_trade_decision: PM 整合后的最终决策书
- (processed_signal): "BUY" / "HOLD" / "SELL"
"""

from __future__ import annotations

import re
from typing import Any

from src.agents.base import AnalysisResult


# 上游 5 档评级 → PanWatch 显示标签
RATING_LABEL_MAP = {
    "buy": "买入",
    "overweight": "增持",
    "hold": "持有",
    "underweight": "减持",
    "sell": "卖出",
}

# 5 档 → 3 档(给 action 字段;前端 'buy' | 'hold' | 'sell')
RATING_ACTION_MAP = {
    "buy": "buy",
    "overweight": "buy",
    "hold": "hold",
    "underweight": "sell",
    "sell": "sell",
}

# 0.4.0 起,上游无法解析 PM 评级时返回 REVIEW。它不是可交易的 Hold:
# 在不扩展前端 3 档 action API 的前提下,以 hold 阻止自动交易,并保留原始状态供展示/提醒。
REVIEW_RATING = "review"
REVIEW_LABEL = "待人工复核"

# 旧字段名兼容:某些下游代码可能 import DECISION_LABEL_MAP
DECISION_LABEL_MAP = RATING_LABEL_MAP


def map_state_to_result(
    *,
    stock: Any,
    ta_result: dict[str, Any],
    model_label: str = "",
) -> AnalysisResult:
    """主入口:把 TradingAgents 的 final_state 映射成 AnalysisResult。

    Args:
        stock: PanWatch StockConfig(symbol/name/market)
        ta_result: {"decision": str, "final_state": dict, "cost_usd": float}
        model_label: 形如 "deepseek/deepseek-chat",写到 markdown 末尾
    """
    state = ta_result.get("final_state") or {}
    cost_usd = float(ta_result.get("cost_usd", 0.0) or 0.0)

    # 评级以 PM 正文(final_trade_decision,用户实际看到的最终决策书)为权威来源:
    # 上游 propagate() 第二个返回的 decision 是对正文的二次提炼,会失真(正文写"卖出"
    # 却返回 "HOLD"),所以优先解析正文里的显式评级标签。
    # 优先级:正文显式标签 > 上游 decision > 正文模糊扫描兜底。
    final_text = state.get("final_trade_decision") or ""
    rating_raw = _parse_rating_label(final_text)
    if rating_raw not in RATING_LABEL_MAP:
        upstream_rating = (ta_result.get("decision") or "").strip().lower()
        rating_raw = upstream_rating
    if rating_raw not in RATING_LABEL_MAP and rating_raw != REVIEW_RATING:
        rating_raw = _parse_rating_from_text(final_text)

    review_required = rating_raw == REVIEW_RATING
    action = RATING_ACTION_MAP.get(rating_raw, "hold")
    action_label = REVIEW_LABEL if review_required else RATING_LABEL_MAP.get(rating_raw, "持有")

    confidence = _extract_confidence(state, rating_raw)
    short_reason = _short_reason(state)

    suggestion = {
        "action": action,
        "action_label": action_label,
        "rating_raw": rating_raw or "hold",  # 保留原始 5 档,前端/历史可查
        "review_required": review_required,
        "signal": _truncate(state.get("trader_investment_plan", ""), 200),
        "reason": state.get("final_trade_decision") or short_reason,
        "should_alert": review_required or rating_raw in ("buy", "overweight", "underweight", "sell"),
        "agent_name": "tradingagents",
        "agent_label": "TradingAgents 深度",
        "confidence": confidence,
    }

    content = _render_markdown(state, suggestion, model_label, cost_usd)
    # 详情页可点击链接(配了 panwatch_base_url 才出现)
    from datetime import date as _date
    from src.core.analysis_link import analysis_detail_markdown
    _link = analysis_detail_markdown(stock.symbol, _date.today().isoformat())
    if _link:
        content = content.rstrip() + f"\n\n---\n{_link}"
    # 通知体只放「最终决策」(决策摘要 + PM 决策书) + 详情链接;
    # 交易员/研究主管/风控辩论/四分析师等完整内容都在详情页,避免通知过长被截断。
    notify_content = _render_notify(state, suggestion, cost_usd, _link)

    return AnalysisResult(
        agent_name="tradingagents",
        title=f"【深度】{stock.name}({stock.symbol}):{suggestion['action_label']}",
        content=content,
        notify_content=notify_content,
        raw_data={
            "suggestion": suggestion,
            "cost_usd": cost_usd,
            "should_alert": suggestion["should_alert"],
            "decision": action,           # 兼容旧字段(3 档)
            "rating": rating_raw or "hold",  # 新字段(5 档原始)
            "confidence": confidence,
            "debate_history": _extract_debate(state),
            "risk_judgment": _risk_judgment(state),
            "risk_debate": _extract_risk_debate(state),
            "analyst_reports": {
                "market": state.get("market_report") or "",
                # 上游情绪分析师字段是 sentiment_report;兼容旧 social_report
                "social": state.get("sentiment_report") or state.get("social_report") or "",
                "news": state.get("news_report") or "",
                "fundamentals": state.get("fundamentals_report") or "",
            },
            "final_decision": state.get("final_trade_decision") or "",
            "trader_plan": state.get("trader_investment_plan") or "",
        },
    )


# ---- helpers ----


# 分隔符字符类同时覆盖半角(: -)与全角(： －)标点 —— 真实中文 PM 正文用全角冒号"：",
# 早期只认半角":"导致"最终交易决策：Buy"匹配不到、回退到上游失真的 decision。
_RATING_TEXT_RE = re.compile(
    r"(?:Rating|评级|最终交易决策|Final\s+(?:Trade\s+)?Decision|FINAL\s+TRANSACTION\s+PROPOSAL)"
    r"[\s\*:：\-—－]+(\*\*)?\s*(Buy|Overweight|Hold|Underweight|Sell|买入|增持|持有|减持|卖出)",
    re.I,
)
_RATING_ZH_TO_EN = {
    "买入": "buy", "增持": "overweight", "持有": "hold",
    "减持": "underweight", "卖出": "sell",
}


def _parse_rating_label(text: str) -> str:
    """只解析 PM 正文里的**显式评级标签**(最终交易决策/评级/FINAL TRANSACTION PROPOSAL: X)。

    不做模糊关键词扫描 —— 避免正文里"否决了之前的买入建议"这类干扰词被误判。
    用作评级提取的首选,确保展示与用户可见的最终决策书一致。
    """
    if not text:
        return ""
    m = _RATING_TEXT_RE.search(text)
    if m:
        word = m.group(2).lower()
        if word in _RATING_ZH_TO_EN:
            word = _RATING_ZH_TO_EN[word]
        if word in RATING_LABEL_MAP:
            return word
    return ""


def _parse_rating_from_text(text: str) -> str:
    """从文本里抽 5 档评级。优先 'Rating: X' 标签,然后第一个 5 档词。"""
    if not text:
        return ""
    label = _parse_rating_label(text)
    if label:
        return label
    # 兜底:扫描整段文本里第一个出现的 5 档英文/中文词
    text_low = text.lower()
    for word in ("overweight", "underweight", "buy", "sell", "hold"):
        if word in text_low:
            return word
    for zh, en in _RATING_ZH_TO_EN.items():
        if zh in text:
            return en
    return ""


# 置信度正则:冒号同时认半角(:)与全角(：)——PM 中文输出常用全角,
# 早先只认半角导致永远抓不到、一律回退默认值。覆盖 "置信度: 8"、"置信度：8/10"、"信心 7"。
_CONFIDENCE_PATTERNS = [
    re.compile(r"confidence[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"置信度[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
    re.compile(r"信心(?:度)?[:：\s]+(\d+(?:\.\d+)?)\s*(?:/\s*10)?", re.I),
]

# 抓不到显式数字时按 5 档评级推导基础置信度(B 方案),而非死的 5.0:
# 强方向(买入/卖出)信心更高,中性(持有)居中。
_RATING_CONFIDENCE_FALLBACK = {
    "buy": 7.0,
    "sell": 7.0,
    "overweight": 6.0,
    "underweight": 6.0,
    "hold": 5.0,
}


def _extract_confidence(state: dict, rating_raw: str = "") -> float:
    """置信度(0-10):优先抓 PM/风控/交易员文本里的显式数字(A 方案);
    抓不到则按评级推导(B 方案),不再一律返回 5.0。"""
    candidates = [
        state.get("final_trade_decision", ""),
        _risk_judgment(state),
        state.get("trader_investment_plan", ""),
    ]
    for text in candidates:
        if not text:
            continue
        for pat in _CONFIDENCE_PATTERNS:
            m = pat.search(text)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 10:  # 百分制转 0-10
                        v = v / 10
                    return max(0.0, min(10.0, v))
                except (ValueError, IndexError):
                    continue
    # B 兜底:按评级推导(无可识别评级才回退 5.0)
    return _RATING_CONFIDENCE_FALLBACK.get(rating_raw, 5.0)


def _short_reason(state: dict, limit: int = 120) -> str:
    """取一段精炼理由,优先 final_trade_decision 前 120 字。"""
    candidates = [
        state.get("final_trade_decision") or "",
        state.get("trader_investment_plan") or "",
        _risk_judgment(state),
    ]
    for text in candidates:
        text = text.strip()
        if text:
            return _truncate(text, limit)
    return ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _extract_debate(state: dict) -> dict:
    """提取辩论历史。上游 investment_debate_state 大致结构:
    {
        "history": "...",      # 全量辩论文本
        "current_response": ...,
        "judge_decision": ...,
    }
    """
    debate = state.get("investment_debate_state") or {}
    if not isinstance(debate, dict):
        return {}
    return {
        "history": debate.get("history", ""),
        "current_response": debate.get("current_response", ""),
        "judge_decision": debate.get("judge_decision", ""),
    }


def _risk_judgment(state: dict) -> str:
    """风控团队裁决:上游在 risk_debate_state.judge_decision(激进/中立/保守辩论后的结论);
    上游根本没有顶层 risk_judge_decision 字段,早先读它导致风控裁决一直空白。"""
    rds = state.get("risk_debate_state")
    if isinstance(rds, dict):
        jd = (rds.get("judge_decision") or "").strip()
        if jd:
            return jd
    return (state.get("risk_judge_decision") or "").strip()  # 兼容兜底


def _extract_risk_debate(state: dict) -> dict:
    """风控团队辩论(激进/中立/保守 + 裁决),结构对称 _extract_debate。
    上游 risk_debate_state.history 是三方交替的完整辩论文本。"""
    rds = state.get("risk_debate_state")
    if not isinstance(rds, dict):
        return {}
    return {
        "history": rds.get("history", ""),
        "judge_decision": rds.get("judge_decision", ""),
    }


def _render_notify(
    state: dict, suggestion: dict, cost_usd: float, link_md: str = ""
) -> str:
    """通知体:只展示「最终决策」(决策摘要 + PM 最终决策书) + 详情链接。

    交易员执行计划 / 研究主管裁决 / 风控辩论 / 四位分析师报告等完整内容都在详情页,
    不进通知 —— 既符合"通知只看最终决策"的诉求,也避免推送过长被各渠道截断。
    """
    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(评级:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts = [
        f"## 最终决策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    ]
    final_text = (state.get("final_trade_decision") or "").strip()
    if final_text:
        parts.append(final_text + "\n")
    parts.append(
        f"\n_成本 ${cost_usd:.4f} · 交易员 / 研究主管 / 风控辩论 / 四分析师完整内容见详情_"
    )
    if link_md:
        parts.append(f"\n\n{link_md}")
    return "\n".join(parts)


def _render_markdown(
    state: dict, suggestion: dict, model_label: str, cost_usd: float
) -> str:
    parts = []

    rating_raw = suggestion.get("rating_raw") or ""
    rating_note = (
        f"(评级:{RATING_LABEL_MAP.get(rating_raw, '持有')})"
        if rating_raw in RATING_LABEL_MAP else ""
    )
    parts.append(
        f"## 最终决策\n\n"
        f"**{suggestion['action_label']}** {rating_note} · 置信度 {suggestion['confidence']:.1f}/10\n"
    )

    # 9 个 Agent 链路:PM(决策书) → Trader → 研究主管 → 风控 → 4 位分析师摘要
    if state.get("final_trade_decision"):
        parts.append(f"### 🎯 PM 最终决策书\n\n{state['final_trade_decision']}\n")

    if state.get("trader_investment_plan"):
        parts.append(f"### 💼 交易员执行计划\n\n{state['trader_investment_plan']}\n")

    # 研究主管裁决 — 看多/看空辩论后的结论,之前只在折叠的辩论 section 末尾
    debate = state.get("investment_debate_state") or {}
    judge_decision = ""
    if isinstance(debate, dict):
        judge_decision = (debate.get("judge_decision") or "").strip()
    if judge_decision:
        parts.append(f"### ⚖️ 研究主管裁决(看多 vs 看空)\n\n{judge_decision}\n")

    risk_jd = _risk_judgment(state)
    if risk_jd:
        parts.append(f"### 🛡️ 风控辩论裁决\n\n{risk_jd}\n")

    # 4 位分析师完整报告不再塞进主体 markdown(早先截 300 字会把财务表格截在表头)。
    # 完整内容在 raw_data.analyst_reports,由前端 tab 完整渲染(含 GFM 表格)。

    parts.append(
        "\n---\n"
        f"_本分析由 TradingAgents 9-Agent 框架生成(技术/情绪/新闻/基本面 → 看多看空辩论 "
        f"→ 研究主管 → 交易员 → 风控辩论 → PM)。仅供学习研究参考,不构成投资建议。_\n"
        f"\n成本:${cost_usd:.4f}"
    )
    if model_label:
        parts.append(f" · AI:{model_label}")

    return "\n".join(parts)
