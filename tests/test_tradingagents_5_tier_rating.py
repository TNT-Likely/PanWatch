"""上游 5 档评级 → PanWatch 3 档 action 映射回归测试。

根因 bug:上游 PM 用 Buy/Overweight/Hold/Underweight/Sell 五档,我们只识别 3 档,
Overweight/Underweight 被兜底到 hold,导致顶层显示"持有"但 PM 实际给出"减持"。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agents.tradingagents.result_mapper import (
    RATING_ACTION_MAP,
    RATING_LABEL_MAP,
    _parse_rating_from_text,
    _parse_rating_label,
    map_state_to_result,
)


def _stock():
    return SimpleNamespace(symbol="601127", name="赛力斯", market=SimpleNamespace(value="CN"))


def _result(decision_raw: str, final_decision_text: str = "") -> dict:
    """构造一份 ta_result 给 map_state_to_result 用"""
    return {
        "decision": decision_raw,
        "final_state": {
            "final_trade_decision": final_decision_text,
            "trader_investment_plan": "Action: Sell\n\nReasoning: 风险大",
        },
        "cost_usd": 0.05,
    }


# ============================================================
# 5 档评级 → 3 档 action + 中文标签
# ============================================================

def test_buy_rating_maps_to_buy():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Buy"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "买入"


def test_overweight_rating_maps_to_buy_with_zh_label():
    """Overweight(增持) → action=buy,但 label 显示"增持"区分于 buy"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "增持"
    assert r.raw_data["suggestion"]["rating_raw"] == "overweight"


def test_hold_rating_maps_to_hold():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold"))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["action_label"] == "持有"


def test_underweight_rating_maps_to_sell_with_zh_label():
    """关键 bug 回归:Underweight(减持) 之前被错误兜底到 hold,
    现在应该 action=sell + label=减持"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Underweight"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "减持"
    assert r.raw_data["suggestion"]["rating_raw"] == "underweight"
    # 应触发提醒(不是 hold)
    assert r.raw_data["suggestion"]["should_alert"] is True


def test_sell_rating_maps_to_sell():
    r = map_state_to_result(stock=_stock(), ta_result=_result("Sell"))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "卖出"


# ============================================================
# Fallback:propagate() 没返回 5 档,从文本里抽
# ============================================================

def test_decision_text_with_rating_label():
    """final_trade_decision 含 'Rating: Underweight' → 解析出 underweight"""
    text = "After thorough analysis...\n\n**Rating**: Underweight\n\nReason: high leverage"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_chinese_label():
    """中文'评级:减持' → underweight"""
    text = "综合考虑:**评级:减持**,建议降低仓位"
    assert _parse_rating_from_text(text) == "underweight"


def test_decision_text_final_transaction_proposal():
    """'FINAL TRANSACTION PROPOSAL: SELL' → sell"""
    text = "...\n\nFINAL TRANSACTION PROPOSAL: **SELL**"
    assert _parse_rating_from_text(text) == "sell"


def test_decision_text_fallback_to_keyword_scan():
    """没显式标签也能从文本里找到 5 档词"""
    text = "Based on macro headwinds, recommend Overweight position in defensive sectors."
    assert _parse_rating_from_text(text) == "overweight"


def test_decision_empty_falls_back_to_hold():
    """propagate 返回空 + 文本也没评级词 → 默认 hold"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("", final_decision_text=""))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_review_signal_is_preserved_as_manual_review():
    """0.4.0 的 REVIEW 是不可交易的人工复核信号，不能伪装成普通持有。"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="上游无法解析最终评级"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"  # 保持现有前端 3 档 API
    assert suggestion["action_label"] == "待人工复核"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["should_alert"] is True


def test_review_signal_overrides_parseable_pm_rating():
    """上游 REVIEW 优先于正文中的评级词，绝不能转成可交易买入。"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("REVIEW", final_decision_text="评级：买入"),
    )
    suggestion = r.raw_data["suggestion"]
    assert suggestion["action"] == "hold"
    assert suggestion["action_label"] == "待人工复核"
    assert suggestion["rating_raw"] == "review"
    assert suggestion["review_required"] is True


def test_decision_unrecognized_then_text_has_underweight():
    """propagate 返回 'xxxx' 不识别 → 从 final_decision 文本抽 underweight"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("xxxx", final_decision_text="...\n**Rating**: Underweight\n..."),
    )
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "减持"


# ============================================================
# 正文与上游 decision 冲突:正文为准(生产 bug 回归)
# 上游 propagate 二次提炼出 "HOLD",但 PM 正文白纸黑字写"卖出/买入",
# 必须以正文为准。真实中文 PM 正文用全角标点(：),早期正则只认半角(:)
# 导致"最终交易决策：Buy"匹配不到、仍回退到失真的 decision=HOLD 显示"持有"。
# 这里全角/半角都覆盖。
# ============================================================

def test_fullwidth_colon_buy_overrides_hold():
    """生产 case(广汽 601238):decision=HOLD 但正文全角'最终交易决策： Buy' → 必须 buy"""
    text = (
        "尊敬的各位投资决策者,经过对广汽集团(601238)的风险分析师辩论进行综合分析,"
        "以下是我对最终交易决策的建议:\n\n"
        "**最终交易决策： Buy**\n\n**决策依据：** 1. 盈利能力分析..."
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("HOLD", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"
    assert r.raw_data["suggestion"]["action_label"] == "买入"


def test_fullwidth_colon_sell_overrides_hold():
    """全角冒号 + 中文:decision=Hold 但正文'最终交易决策：卖出' → sell"""
    text = "综合风险辩论...\n\n## 最终交易决策：**卖出**\n\n### 评级：**Sell**\n\n核心依据..."
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"
    assert r.raw_data["suggestion"]["action_label"] == "卖出"
    assert r.raw_data["suggestion"]["rating_raw"] == "sell"


def test_halfwidth_colon_still_works():
    """半角冒号也要继续工作:'最终交易决策: 买入' → buy"""
    text = "总之...\n\n最终交易决策: **买入**\n评级: 买入"
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "buy"


def test_parse_rating_label_covers_both_colons():
    """_parse_rating_label 全角(：)半角(:)冒号都能解析"""
    assert _parse_rating_label("最终交易决策：Buy") == "buy"   # 全角
    assert _parse_rating_label("最终交易决策: Buy") == "buy"   # 半角
    assert _parse_rating_label("评级：卖出") == "sell"          # 全角中文
    assert _parse_rating_label("评级: Sell") == "sell"         # 半角
    assert _parse_rating_label("FINAL TRANSACTION PROPOSAL: **BUY**") == "buy"


def test_decision_used_when_text_has_no_label():
    """正文没有显式评级标签 → 回退信任上游 decision(此处 Hold)"""
    text = "市场存在不确定性,建议观察。维持观望立场,等待更明确信号。"
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "hold"
    assert r.raw_data["suggestion"]["rating_raw"] == "hold"


def test_text_label_not_confused_by_distractor_words():
    """正文含干扰词(否决了'买入')但显式标签是'卖出' → 标签优先,sell 而非 buy"""
    text = (
        "我否决了多头分析师的**买入**建议,理由是基本面恶化。\n\n"
        "FINAL TRANSACTION PROPOSAL: **SELL**"
    )
    r = map_state_to_result(stock=_stock(), ta_result=_result("Hold", final_decision_text=text))
    assert r.raw_data["suggestion"]["action"] == "sell"


# ============================================================
# Markdown 渲染:5 档评级标签写进 markdown 头部
# ============================================================

def test_markdown_shows_5_tier_rating_in_header():
    """Markdown 顶部应显示原始 5 档评级,避免"建议卖出但顶部写持有"的歧义"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result=_result("Underweight", final_decision_text="Rating: Underweight\n\nReason: ..."),
    )
    assert "减持" in r.content
    # 既要有 action_label,也要有 rating note
    assert r.content.count("减持") >= 1


# ============================================================
# raw_data 里同时保留 3 档(decision) + 5 档(rating)
# ============================================================

def test_raw_data_has_both_decision_and_rating():
    """前端兼容:既要有 3 档 decision 给老代码,也要有 5 档 rating 给新展示"""
    r = map_state_to_result(stock=_stock(), ta_result=_result("Overweight"))
    assert r.raw_data["decision"] == "buy"  # 3 档
    assert r.raw_data["rating"] == "overweight"  # 5 档


# ============================================================
# 静态 mapping 完整性
# ============================================================

def test_all_5_ratings_have_label():
    for r in ("buy", "overweight", "hold", "underweight", "sell"):
        assert r in RATING_LABEL_MAP
        assert r in RATING_ACTION_MAP


def test_action_map_only_uses_3_actions():
    """3 档 action 只能是 buy/hold/sell(前端类型)"""
    assert set(RATING_ACTION_MAP.values()) == {"buy", "hold", "sell"}


# ============================================================
# Markdown 完整性:9 个 Agent 的产出都体现
# ============================================================

def _full_state():
    return {
        "final_trade_decision": "**Rating: Underweight** 详细决策书...",
        "trader_investment_plan": "Action: Sell\n建议减仓 70%",
        "risk_judge_decision": "风控辩论:激进/保守/中立讨论后,建议谨慎",
        "investment_debate_state": {
            "history": "Bull: ...\nBear: ...\nBull: ...\nBear: ...",
            "judge_decision": "研究主管:综合看多看空双方,倾向谨慎持有",
        },
        "market_report": "技术面:MACD 死叉,空头排列,趋势偏弱..." * 30,
        "social_report": "情绪面:讨论度下降,看空声音增加..." * 20,
        "news_report": "新闻面:公告:Q1 净利润下降..." * 20,
        "fundamentals_report": "基本面:营收增长但毛利下降,ROE 转负..." * 20,
    }


def test_markdown_contains_decision_chain():
    """markdown 主体含决策链(PM/交易员/研究主管/风控);分析师完整报告移到 raw_data 由前端 tab 渲染"""
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Underweight", "final_state": _full_state(), "cost_usd": 0.05},
    )
    content = r.content
    assert "PM 最终决策书" in content
    assert "交易员执行计划" in content
    assert "研究主管裁决" in content
    assert "倾向谨慎持有" in content
    assert "风控辩论裁决" in content
    # 不再把分析师概览塞进主体(早先截 300 字会把财务表格截在表头)
    assert "4 位分析师观点概览" not in content
    # 完整分析师报告在 raw_data,前端 tab 渲染
    reports = r.raw_data["analyst_reports"]
    assert reports["market"] and reports["social"] and reports["news"] and reports["fundamentals"]


def test_analyst_reports_full_not_truncated():
    """分析师报告在 raw_data 里完整保留、不截断(修复财务表格被截在表头)"""
    state = _full_state()
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    # 完整等于原始报告,无任何截断
    assert reports["market"] == state["market_report"]
    assert reports["fundamentals"] == state["fundamentals_report"]
    assert len(reports["market"]) > 300  # 远超旧的 300 字概览上限


def test_empty_analyst_kept_empty_in_raw_data():
    """某位分析师没产出 → raw_data 里为空串(前端 tab 跳过该 tab)"""
    state = _full_state()
    state["social_report"] = ""  # 情绪分析师没跑
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    reports = r.raw_data["analyst_reports"]
    assert reports["social"] == ""
    assert reports["market"] and reports["news"] and reports["fundamentals"]


def test_markdown_skips_judge_when_no_debate():
    """没辩论历史时不渲染'研究主管裁决' section"""
    state = _full_state()
    state["investment_debate_state"] = {"history": "", "judge_decision": ""}
    r = map_state_to_result(
        stock=_stock(),
        ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.05},
    )
    assert "研究主管裁决" not in r.content


# ============================================================
# 情绪分析师字段(上游 sentiment_report) + 通知完整内容
# ============================================================

def test_sentiment_report_maps_to_social():
    """上游情绪字段是 sentiment_report,必须映射到 analyst_reports.social(修复看不到情绪分析师)"""
    state = {"final_trade_decision": "评级：买入", "sentiment_report": "情绪面:讨论度上升,看多增加"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "情绪面:讨论度上升,看多增加"


def test_social_report_fallback_when_no_sentiment():
    """旧字段 social_report 仍兼容(无 sentiment_report 时回退)"""
    state = {"final_trade_decision": "评级：持有", "social_report": "旧情绪字段内容"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["analyst_reports"]["social"] == "旧情绪字段内容"


def test_notify_content_only_final_decision():
    """通知体(notify_content)只放「最终决策」:决策摘要 + PM 最终决策书。
    交易员执行计划 / 研究主管裁决 / 风控辩论 / 四位分析师都不进通知(避免过长被截断),
    只在详情页;content(完整决策链)仍保留供历史/详情。"""
    state = {
        "final_trade_decision": "最终交易决策：买入\n\n核心逻辑:基本面拐点确认,估值修复在即",
        "trader_investment_plan": "交易员计划:分三批建仓,首笔仓位 30%",
        "market_report": "技术分析详细内容" * 100,
        "sentiment_report": "情绪面详细" * 100,
        "investment_debate_state": {"history": "多空辩论历史正文", "judge_decision": "研究主管裁决:倾向看多"},
        "risk_debate_state": {"history": "风控三方辩论正文", "judge_decision": "风控团队结论:仓位可控"},
    }
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    # 通知体单独设置(不再回退 content)
    assert r.notify_content is not None
    nc = r.notify_content
    # 含最终决策核心(决策摘要 + PM 决策书正文)
    assert "最终决策" in nc
    assert "买入" in nc
    assert "基本面拐点确认" in nc
    # 不含交易员计划 / 裁决 / 风控 / 分析师明细的具体内容
    assert "分三批建仓" not in nc
    assert "倾向看多" not in nc
    assert "仓位可控" not in nc
    assert state["market_report"] not in nc
    # content(完整)仍含决策链(供详情页/历史)
    assert "PM 最终决策书" in r.content
    assert "交易员执行计划" in r.content


# ============================================================
# 置信度 A+B:优先抓 PM 显式数字(含全角冒号),抓不到按评级推导
# ============================================================

def test_confidence_extracted_fullwidth_colon():
    """全角'置信度：8.5/10'能抓到真实值(早先只认半角冒号一律回退默认)"""
    state = {"final_trade_decision": "评级：买入\n置信度：8.5/10"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 8.5


def test_confidence_extracted_halfwidth():
    """半角'confidence: 7'仍能抓到"""
    state = {"final_trade_decision": "Rating: Buy\nconfidence: 7"}
    r = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": state, "cost_usd": 0.01})
    assert r.raw_data["suggestion"]["confidence"] == 7.0


def test_confidence_derived_from_rating_when_absent():
    """无显式置信度 → 按评级推导(强方向>中性),不再一律 5.0"""
    buy = map_state_to_result(stock=_stock(), ta_result={"decision": "Buy", "final_state": {"final_trade_decision": "最终交易决策：买入"}, "cost_usd": 0.01})
    hold = map_state_to_result(stock=_stock(), ta_result={"decision": "Hold", "final_state": {"final_trade_decision": "最终交易决策：持有"}, "cost_usd": 0.01})
    sell = map_state_to_result(stock=_stock(), ta_result={"decision": "Sell", "final_state": {"final_trade_decision": "评级：卖出"}, "cost_usd": 0.01})
    assert buy.raw_data["suggestion"]["confidence"] == 7.0
    assert hold.raw_data["suggestion"]["confidence"] == 5.0
    assert sell.raw_data["suggestion"]["confidence"] == 7.0
    assert buy.raw_data["suggestion"]["confidence"] > hold.raw_data["suggestion"]["confidence"]
