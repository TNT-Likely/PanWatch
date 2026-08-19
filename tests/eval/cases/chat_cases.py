"""chat 工具循环 golden set。

覆盖：5 个工具各若干场景、多工具组合、不该调工具的闲聊/概念题、
工具失败降级。有据性断言的关键值全部来自 mock 数据（模型没见过就写不出）。

维护约定：每个线上 bad case 修复后固化一条新用例。
"""

from tests.eval.framework import ChatEvalCase

# ──────────────── mock 工具数据 ────────────────
# 数值刻意取"模型编不出来"的非整值，answer_must_contain 据此验证有据性

MOCK_PORTFOLIO = (
    "实盘持仓：\n"
    "- 贵州茅台(CN:600519) 100股 成本1503.2 风格波段\n\n"
    "模拟盘持仓：\n"
    "- 宁德时代(CN:300750) 200股 入场价211.4 止损196.0 浮盈1284.0"
)
MOCK_PORTFOLIO_EMPTY = "用户暂无持仓。"
MOCK_QUOTE_600519 = "实时行情：贵州茅台（CN:600519）价格 1712.5，涨跌幅 1.35%，成交量 28143"
MOCK_QUOTE_00700 = "实时行情：腾讯控股（HK:00700）价格 402.8，涨跌幅 -0.62%，成交量 1834万"
MOCK_QUOTE_TSLA = "实时行情：Tesla（US:TSLA）价格 251.37，涨跌幅 2.14%，成交量 9812万"
MOCK_TA_600519 = "技术面：趋势 上行，MACD 金叉，RSI 58.2，支撑位 1651.0，压力位 1783.0"
MOCK_TA_300750 = "技术面：趋势 震荡，MACD 死叉，RSI 44.1，支撑位 198.3，压力位 226.5"
MOCK_TA_000858 = "技术面：趋势 下行，MACD 死叉，RSI 38.5，支撑位 128.6，压力位 145.2"
MOCK_SUGGESTIONS_600519 = (
    "最近 AI 建议：\n"
    "- [收盘复盘] 减仓: 高位滞涨，量能持续萎缩\n"
    "- [盘前分析] 持有: 均线多头排列，等待放量"
)
MOCK_WATCHLIST = (
    "自选股列表：\n"
    "- 贵州茅台(CN:600519)\n"
    "- 宁德时代(CN:300750)\n"
    "- 腾讯控股(HK:00700)"
)
TOOL_FAIL_TIMEOUT = "工具执行出错: 数据源请求超时"

# 失败场景通用：答案里应有"如实说明失败"类表述
FAIL_PHRASES = ("失败", "无法", "未能", "暂时", "出错", "稍后", "获取不到", "拿不到")


CHAT_CASES: list[ChatEvalCase] = [
    # ──────── get_portfolio ────────
    ChatEvalCase(
        id="portfolio-1",
        question="我的持仓怎么样？帮我看看健康度",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        answer_must_contain=("茅台",),
        notes="持仓健康类问题应主动查持仓，且答案引用真实持仓",
    ),
    ChatEvalCase(
        id="portfolio-2",
        question="我现在模拟盘浮盈多少？",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        answer_must_contain=("1284",),
        notes="浮盈数字必须来自工具返回，验证有据性",
    ),
    ChatEvalCase(
        id="portfolio-3",
        question="帮我看看该不该调仓",
        tool_data={"get_portfolio": MOCK_PORTFOLIO},
        expected_tools=("get_portfolio",),
        notes="调仓建议前必须先获取持仓",
    ),
    # ──────── get_stock_quote ────────
    ChatEvalCase(
        id="quote-1",
        question="600519 现在多少钱？",
        tool_data={"get_stock_quote": MOCK_QUOTE_600519},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "600519"}},
        answer_must_contain=("1712.5",),
        notes="价格必须引用工具返回值",
    ),
    ChatEvalCase(
        id="quote-2",
        question="港股腾讯（00700）现在股价如何？",
        tool_data={"get_stock_quote": MOCK_QUOTE_00700},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "00700", "market": "HK"}},
        answer_must_contain=("402.8",),
        notes="市场参数应正确传 HK",
    ),
    ChatEvalCase(
        id="quote-3",
        question="美股特斯拉 TSLA 今天涨了吗？",
        tool_data={"get_stock_quote": MOCK_QUOTE_TSLA},
        expected_tools=("get_stock_quote",),
        param_checks={"get_stock_quote": {"symbol": "TSLA", "market": "US"}},
        answer_must_contain=("2.14",),
        notes="涨跌幅必须引用工具返回值",
    ),
    # ──────── get_technical_analysis ────────
    ChatEvalCase(
        id="ta-1",
        question="600519 技术面怎么样？",
        tool_data={"get_technical_analysis": MOCK_TA_600519},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "600519"}},
        answer_must_contain=("金叉",),
        notes="技术面结论应引用工具数据",
    ),
    ChatEvalCase(
        id="ta-2",
        question="帮我看下 300750 的支撑位和压力位",
        tool_data={"get_technical_analysis": MOCK_TA_300750},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "300750"}},
        answer_must_contain=("198.3", "226.5"),
        notes="支撑/压力位数值必须来自工具返回",
    ),
    ChatEvalCase(
        id="ta-3",
        question="从 MACD 和 RSI 看，000858 现在是买点吗？",
        tool_data={"get_technical_analysis": MOCK_TA_000858},
        expected_tools=("get_technical_analysis",),
        param_checks={"get_technical_analysis": {"symbol": "000858"}},
        answer_must_contain=("38.5",),
        notes="RSI 数值必须来自工具返回",
    ),
    # ──────── get_stock_suggestions ────────
    ChatEvalCase(
        id="sugg-1",
        question="最近系统对 600519 给过什么 AI 建议？",
        tool_data={"get_stock_suggestions": MOCK_SUGGESTIONS_600519},
        expected_tools=("get_stock_suggestions",),
        param_checks={"get_stock_suggestions": {"symbol": "600519"}},
        answer_must_contain=("减仓",),
        notes="历史建议必须引用工具返回",
    ),
    ChatEvalCase(
        id="sugg-2",
        question="之前的分析报告怎么评价 600519 的？",
        tool_data={"get_stock_suggestions": MOCK_SUGGESTIONS_600519},
        expected_tools=("get_stock_suggestions",),
        notes="历史分析类问题应查建议库而非编造",
    ),
    # ──────── get_watchlist ────────
    ChatEvalCase(
        id="watch-1",
        question="我的自选股有哪些？",
        tool_data={"get_watchlist": MOCK_WATCHLIST},
        expected_tools=("get_watchlist",),
        answer_must_contain=("宁德时代",),
        notes="自选列表必须来自工具返回",
    ),
    ChatEvalCase(
        id="watch-2",
        question="帮我看看自选里有没有港股",
        tool_data={"get_watchlist": MOCK_WATCHLIST},
        expected_tools=("get_watchlist",),
        answer_must_contain=("腾讯",),
        notes="需要基于自选列表判断",
    ),
    # ──────── 多工具组合 ────────
    ChatEvalCase(
        id="multi-1",
        question="结合实时行情和技术面，帮我分析下 600519",
        tool_data={
            "get_stock_quote": MOCK_QUOTE_600519,
            "get_technical_analysis": MOCK_TA_600519,
        },
        expected_tools=("get_stock_quote", "get_technical_analysis"),
        answer_must_contain=("1712.5",),
        notes="组合分析应同时调两个工具",
    ),
    ChatEvalCase(
        id="multi-2",
        question="我持仓里的茅台现在该止盈吗？先看下现价",
        tool_data={
            "get_portfolio": MOCK_PORTFOLIO,
            "get_stock_quote": MOCK_QUOTE_600519,
        },
        expected_tools=("get_portfolio", "get_stock_quote"),
        notes="止盈判断需要持仓成本 + 现价",
    ),
    ChatEvalCase(
        id="multi-3",
        question="把我自选股里的茅台行情报一下",
        tool_data={
            "get_watchlist": MOCK_WATCHLIST,
            "get_stock_quote": MOCK_QUOTE_600519,
        },
        expected_tools=("get_stock_quote",),
        answer_must_contain=("1712.5",),
        notes="至少要查行情；查不查自选列表均可接受",
    ),
    # ──────── 不该调工具的场景 ────────
    ChatEvalCase(
        id="chitchat-1",
        question="你好",
        expect_no_tools=True,
        notes="寒暄不该触发任何工具",
    ),
    ChatEvalCase(
        id="chitchat-2",
        question="你是谁？你能帮我做什么？",
        expect_no_tools=True,
        notes="自我介绍不该触发工具",
    ),
    ChatEvalCase(
        id="chitchat-3",
        question="好的，谢谢你，再见",
        expect_no_tools=True,
        notes="致谢收尾不该触发工具",
    ),
    ChatEvalCase(
        id="concept-1",
        question="什么是市盈率？通俗解释一下",
        expect_no_tools=True,
        notes="纯概念解释不需要实时数据",
    ),
    ChatEvalCase(
        id="concept-2",
        question="MACD 金叉是什么意思？",
        expect_no_tools=True,
        notes="指标科普不需要调工具",
    ),
    # ──────── 工具失败降级 ────────
    ChatEvalCase(
        id="fail-1",
        question="600519 现在多少钱？",
        tool_data={"get_stock_quote": TOOL_FAIL_TIMEOUT},
        expected_tools=("get_stock_quote",),
        answer_must_contain_any=FAIL_PHRASES,
        answer_must_not_contain=("1712.5",),
        notes="行情工具失败：如实说明，不编造价格",
    ),
    ChatEvalCase(
        id="fail-2",
        question="000858 技术面如何？",
        tool_data={"get_technical_analysis": "未能获取 CN:000858 的技术面数据。"},
        expected_tools=("get_technical_analysis",),
        answer_must_contain_any=FAIL_PHRASES,
        answer_must_not_contain=("38.5",),
        notes="技术面数据缺失：不编造指标数值",
    ),
    ChatEvalCase(
        id="fail-3",
        question="我的持仓怎么样？",
        tool_data={"get_portfolio": MOCK_PORTFOLIO_EMPTY},
        expected_tools=("get_portfolio",),
        answer_must_contain_any=("暂无", "没有持仓", "无持仓", "空仓", "还没有"),
        answer_must_not_contain=("茅台",),
        notes="空持仓：如实告知，不编造持仓",
    ),
]
