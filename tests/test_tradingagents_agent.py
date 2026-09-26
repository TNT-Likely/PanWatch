"""TradingAgentsAgent 单元测试 — 不依赖 tradingagents 上游库安装。

覆盖:
- collect() 从 Provider 体系收集数据
- _check_availability 软依赖检测
- llm_adapter 配置桥接
- result_mapper 状态映射
- cost_tracker 预算估算
- toolkit_adapter monkeypatch 上下文
- progress 聚合
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

from src.modules.automation.tradingagents.agent import TradingAgentsAgent, TradingAgentsUnavailable
from src.modules.automation.tradingagents.observability import (
    check_budget,
    estimate_cost,
    get_today_cache_key,
)
from src.modules.automation.tradingagents.runtime_support import (
    VALID_ANALYSTS,
    build_ta_llm_config,
    inject_api_key_env,
)
from src.modules.automation.tradingagents.observability import (
    PanWatchProgressHandler,
    aggregate_progress,
    STAGES_ORDER,
)
from src.modules.automation.tradingagents.decision import (
    DECISION_LABEL_MAP,
    map_state_to_result,
)
from src.modules.automation.tradingagents.toolkit_adapter import (
    is_a_share,
    panwatch_data_context,
    patch_route_to_vendor,
)


# ============================================================================
# llm_adapter
# ============================================================================


class TestLLMAdapter(unittest.TestCase):
    def test_valid_analysts_set(self):
        """合法分析师集合包含 4 个上游期望值"""
        self.assertEqual(VALID_ANALYSTS, {"market", "social", "news", "fundamentals"})

    def test_build_ta_llm_config_basic(self):
        """生成 TradingAgents config dict — 关键字段齐全"""
        ai_client = MagicMock()
        ai_client.base_url = "https://api.deepseek.com"
        ai_client.model = "deepseek-chat"
        ai_client.api_key = "sk-test"

        config = build_ta_llm_config(
            ai_client, debate_rounds=2, selected_analysts=["market", "news"]
        )
        # 用 openrouter 走标准 chat completions,避开 OpenAI Responses API 的兼容性问题
        self.assertEqual(config["llm_provider"], "openrouter")
        self.assertEqual(config["backend_url"], "https://api.deepseek.com")
        self.assertEqual(config["deep_think_llm"], "deepseek-chat")
        self.assertEqual(config["max_debate_rounds"], 2)
        self.assertEqual(set(config["selected_analysts"]), {"market", "news"})
        self.assertEqual(config["output_language"], "Chinese")
        self.assertFalse(config["checkpoint_enabled"])

    def test_build_ta_llm_config_bounds_provider_calls(self):
        """LLM 请求必须有明确超时、重试和输出上限，避免图永远卡在单次调用。"""
        ai_client = MagicMock()
        ai_client.base_url = "https://api.example.com"
        ai_client.model = "test-model"
        ai_client.api_key = "sk-test"

        config = build_ta_llm_config(ai_client)

        self.assertEqual(config["llm_timeout_seconds"], 120)
        self.assertEqual(config["llm_max_retries"], 0)
        self.assertEqual(config["max_tokens"], 4096)

    def test_build_ta_llm_config_rejects_invalid_analyst(self):
        """非法分析师名 — 抛 ValueError"""
        ai_client = MagicMock()
        with self.assertRaises(ValueError):
            build_ta_llm_config(
                ai_client, selected_analysts=["market", "technical"]
            )

    def test_build_ta_llm_config_uses_panwatch_runtime_and_opt_in_sec_edgar(self):
        """美股显式启用时才把三张报表路由到 SEC EDGAR，并隔离上游运行文件。"""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        ai_client = MagicMock(base_url="https://api.example.com", model="test-model", api_key="sk-test")
        with TemporaryDirectory() as temp_dir:
            runtime_dir = Path(temp_dir) / "tradingagents"
            config = build_ta_llm_config(
                ai_client,
                market="US",
                enable_sec_edgar=True,
                runtime_dir=runtime_dir,
            )
            self.assertTrue((runtime_dir / "results").is_dir())
            self.assertTrue((runtime_dir / "cache").is_dir())
            self.assertTrue((runtime_dir / "memory").is_dir())

        self.assertEqual(config["holding_period_days"], 5)
        self.assertEqual(config["results_dir"], str(runtime_dir / "results"))
        self.assertEqual(config["data_cache_dir"], str(runtime_dir / "cache"))
        self.assertEqual(config["memory_log_path"], str(runtime_dir / "memory" / "trading_memory.md"))
        self.assertEqual(
            config["tool_vendors"],
            {
                "get_balance_sheet": "sec_edgar,yfinance",
                "get_cashflow": "sec_edgar,yfinance",
                "get_income_statement": "sec_edgar,yfinance",
            },
        )

    def test_build_ta_llm_config_keeps_sec_edgar_disabled_for_non_us_market(self):
        """SEC EDGAR 仅适用于美股；即使误启用也不能影响 A/HK 路由。"""
        ai_client = MagicMock(base_url="https://api.example.com", model="test-model", api_key="sk-test")
        config = build_ta_llm_config(ai_client, market="CN", enable_sec_edgar=True)
        self.assertEqual(
            config["tool_vendors"],
            {
                "get_balance_sheet": "yfinance",
                "get_cashflow": "yfinance",
                "get_income_statement": "yfinance",
            },
        )

    def test_non_us_config_overrides_previous_global_sec_edgar_routes(self):
        """上游合并嵌套 config 时，A/HK 运行必须清除前一美股运行的 EDGAR 覆盖。"""
        from copy import deepcopy

        from tradingagents.dataflows import config as upstream_config
        from tradingagents.default_config import DEFAULT_CONFIG

        ai_client = MagicMock(base_url="https://api.example.com", model="test-model", api_key="sk-test")
        original_config = deepcopy(upstream_config.get_config())
        try:
            upstream_config._config = deepcopy(DEFAULT_CONFIG)
            upstream_config.set_config(
                build_ta_llm_config(ai_client, market="US", enable_sec_edgar=True)
            )
            upstream_config.set_config(
                build_ta_llm_config(ai_client, market="HK", enable_sec_edgar=False)
            )
            self.assertEqual(
                upstream_config.get_config()["tool_vendors"],
                {
                    "get_balance_sheet": "yfinance",
                    "get_cashflow": "yfinance",
                    "get_income_statement": "yfinance",
                },
            )
        finally:
            upstream_config._config = original_config

    def test_inject_api_key_env(self):
        """API key 注入到环境变量 — OPENAI_API_KEY 被设置"""
        import os
        ai_client = MagicMock(api_key="sk-test-key")
        previous = {
            key: os.environ.get(key)
            for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY")
        }
        try:
            inject_api_key_env(ai_client)
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "sk-test-key")
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ============================================================================
# result_mapper
# ============================================================================


class TestResultMapper(unittest.TestCase):
    def _mock_stock(self):
        stock = MagicMock()
        stock.symbol = "600519"
        stock.name = "贵州茅台"
        return stock

    def test_decision_buy_maps_to_chinese_label(self):
        """BUY 决策 — 映射成 「买入」"""
        stock = self._mock_stock()
        ta_result = {
            "decision": "BUY",
            "final_state": {
                "final_trade_decision": "估值修复 + 资金流持续净流入",
                "trader_investment_plan": "建议加仓",
            },
            "cost_usd": 0.05,
        }
        result = map_state_to_result(stock=stock, ta_result=ta_result, model_label="deepseek/deepseek-chat")
        self.assertEqual(result.agent_name, "tradingagents")
        self.assertIn("买入", result.title)
        sug = result.raw_data["suggestion"]
        self.assertEqual(sug["action"], "buy")
        self.assertEqual(sug["action_label"], "买入")
        self.assertTrue(sug["should_alert"])
        self.assertEqual(result.raw_data["cost_usd"], 0.05)

    def test_decision_hold_no_alert(self):
        """HOLD 决策 — should_alert=False"""
        stock = self._mock_stock()
        ta_result = {"decision": "HOLD", "final_state": {}, "cost_usd": 0.01}
        result = map_state_to_result(stock=stock, ta_result=ta_result, model_label="")
        self.assertFalse(result.raw_data["suggestion"]["should_alert"])

    def test_unknown_decision_falls_back_to_hold(self):
        """未知决策值 — 兜底成 hold,不抛异常"""
        stock = self._mock_stock()
        ta_result = {"decision": "STRONG_BUY", "final_state": {}, "cost_usd": 0}
        result = map_state_to_result(stock=stock, ta_result=ta_result, model_label="")
        self.assertEqual(result.raw_data["suggestion"]["action"], "hold")

    def test_extract_confidence_from_text(self):
        """从文本提取 confidence — 「confidence: 7/10」匹配到 7.0"""
        stock = self._mock_stock()
        ta_result = {
            "decision": "BUY",
            "final_state": {
                "final_trade_decision": "Strong recommendation. confidence: 7/10",
            },
            "cost_usd": 0,
        }
        result = map_state_to_result(stock=stock, ta_result=ta_result, model_label="")
        self.assertEqual(result.raw_data["confidence"], 7.0)

    def test_analyst_reports_preserved(self):
        """4 分析师报告 — 全部保留到 raw_data"""
        stock = self._mock_stock()
        ta_result = {
            "decision": "SELL",
            "final_state": {
                "market_report": "技术面看跌",
                "social_report": "社交情绪偏空",
                "news_report": "近期无重大利好",
                "fundamentals_report": "估值偏高",
            },
            "cost_usd": 0.03,
        }
        result = map_state_to_result(stock=stock, ta_result=ta_result, model_label="")
        reports = result.raw_data["analyst_reports"]
        self.assertEqual(reports["market"], "技术面看跌")
        self.assertEqual(reports["fundamentals"], "估值偏高")


# ============================================================================
# cost_tracker
# ============================================================================


class TestCostTracker(unittest.TestCase):
    def test_estimate_cost_deepseek_shallow(self):
        """deepseek-chat shallow — 单次估算应在 $0.02-$0.06 范围"""
        est = estimate_cost(
            debate_rounds=1,
            selected_analysts=["market", "social", "news", "fundamentals"],
            model="deepseek-chat",
        )
        self.assertEqual(est["model"], "deepseek-chat")
        self.assertGreater(est["cost_low_usd"], 0.005)
        self.assertLess(est["cost_high_usd"], 0.20)
        self.assertGreater(est["cost_high_usd"], est["cost_low_usd"])

    def test_estimate_cost_unknown_model_falls_back(self):
        """未知模型 — 不抛异常,fallback 到 deepseek 单价"""
        est = estimate_cost(debate_rounds=1, selected_analysts=["market"], model="my-custom-llm")
        self.assertGreater(est["cost_low_usd"], 0)

    def test_get_today_cache_key_includes_today(self):
        """缓存键 — 含日期 + symbol + market + debate_rounds + model"""
        key = get_today_cache_key("600519", "CN", 1, "deepseek-chat")
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.assertIn(today_str, key)
        self.assertIn("600519", key)
        self.assertIn("CN", key)
        self.assertIn("r1", key)
        self.assertIn("deepseek-chat", key)


# ============================================================================
# toolkit_adapter
# ============================================================================


class TestToolkitAdapter(unittest.TestCase):
    def test_is_a_share_six_digits(self):
        """A 股识别 — 6 位纯数字才算"""
        self.assertTrue(is_a_share("600519"))
        self.assertTrue(is_a_share("000001"))
        self.assertFalse(is_a_share("AAPL"))
        self.assertFalse(is_a_share("00700"))  # 港股 5 位
        self.assertFalse(is_a_share("12345"))   # 5 位
        self.assertFalse(is_a_share(""))

    def test_panwatch_data_context_isolation(self):
        """数据上下文 — 进入/退出时不污染外部(基于 ContextVar)"""
        from src.modules.automation.tradingagents import toolkit_adapter
        self.assertEqual(toolkit_adapter._cache(), {})
        with panwatch_data_context({"klines": [1, 2, 3]}):
            self.assertEqual(toolkit_adapter._cache().get("klines"), [1, 2, 3])
        self.assertEqual(toolkit_adapter._cache(), {})

    def test_patch_route_to_vendor_noop_when_lib_absent(self):
        """tradingagents 未安装 — patch 上下文 no-op,不抛异常"""
        # 当 import 失败时,patch 应该静默 yield
        with patch_route_to_vendor():
            pass  # 不应抛异常


# ============================================================================
# progress
# ============================================================================


class TestProgress(unittest.TestCase):
    def test_progress_handler_records_cost(self):
        """ProgressHandler — record_cost 累加 total_cost"""
        handler = PanWatchProgressHandler(trace_id="test-123")
        handler.record_cost(0.01)
        handler.record_cost(0.02)
        self.assertAlmostEqual(handler._total_cost, 0.03)

    def test_aggregate_progress_empty(self):
        """聚合空日志 — 所有阶段 pending"""
        result = aggregate_progress([])
        self.assertEqual(len(result["stages"]), len(STAGES_ORDER))
        for stage in result["stages"]:
            self.assertEqual(stage["status"], "pending")

    def test_aggregate_progress_with_stages(self):
        """聚合日志 — stage_start/stage_end 正确标记状态"""
        logs = [
            {
                "timestamp": "2026-05-16T09:00:00",
                "tags": {"stage": "market_analyst", "action": "stage_start", "total_cost_usd": 0.0},
            },
            {
                "timestamp": "2026-05-16T09:00:30",
                "tags": {"stage": "market_analyst", "action": "stage_end", "total_cost_usd": 0.005},
            },
            {
                "timestamp": "2026-05-16T09:00:35",
                "tags": {"stage": "social_analyst", "action": "stage_start", "total_cost_usd": 0.005},
            },
        ]
        result = aggregate_progress(logs)
        self.assertIn("market_analyst", result["completed_stages"])
        self.assertEqual(result["current_stage"], "social_analyst")
        self.assertEqual(result["total_cost_usd"], 0.005)


# ============================================================================
# Agent class
# ============================================================================


class TestTradingAgentsAgent(unittest.TestCase):
    def test_agent_init_defaults(self):
        """默认实例化 — 4 个分析师,1 轮辩论"""
        agent = TradingAgentsAgent()
        self.assertEqual(set(agent.analyst_types), VALID_ANALYSTS)
        self.assertEqual(agent.debate_rounds, 1)
        self.assertEqual(agent.monthly_budget_usd, 10.0)

    def test_agent_init_rejects_invalid_analyst(self):
        """初始化时校验 analyst 类型 — 非法值抛 ValueError"""
        with self.assertRaises(ValueError):
            TradingAgentsAgent(analyst_types=["market", "technical"])

    def test_agent_availability_reflects_library_install(self):
        """tradingagents 软依赖 — 库在则 _available=True,否则 False + import_error 非空"""
        agent = TradingAgentsAgent()
        try:
            import tradingagents  # noqa: F401
            self.assertTrue(agent._available)
            self.assertEqual(agent._import_error, "")
        except ImportError:
            self.assertFalse(agent._available)
            self.assertIn("tradingagents", agent._import_error)

    async def _run_analyze_unavailable(self):
        agent = TradingAgentsAgent()
        # 强制标记不可用,验证 analyze 立即抛错而不会进入 propagate
        agent._available = False
        agent._import_error = "mocked unavailable"
        context = MagicMock()
        with self.assertRaises(TradingAgentsUnavailable):
            await agent.analyze(context, {"stock": MagicMock(symbol="600519", name="X")})

    def test_analyze_raises_when_unavailable(self):
        """库未安装时 analyze() 抛 TradingAgentsUnavailable(强制标记验证)"""
        import asyncio
        asyncio.run(self._run_analyze_unavailable())


# ============================================================================
# Integration: collect with mocked Providers
# ============================================================================


class TestPhaseBFeatures(unittest.TestCase):
    """Phase B 新增功能 — 双模型 / 超时 / 模拟盘 / 缓存绕过 / run_single。"""

    def test_dual_model_config(self):
        """双模型 — deep_model + quick_model 分别注入 TA config"""
        ai_client = MagicMock()
        ai_client.base_url = "https://api.deepseek.com"
        ai_client.model = "default-model"
        ai_client.api_key = "sk-x"

        cfg = build_ta_llm_config(
            ai_client,
            deep_model="claude-sonnet-4",
            quick_model="claude-haiku",
        )
        self.assertEqual(cfg["deep_think_llm"], "claude-sonnet-4")
        self.assertEqual(cfg["quick_think_llm"], "claude-haiku")

    def test_quick_model_defaults_to_deep(self):
        """quick_model 未指定 — fallback 到 deep_model"""
        ai_client = MagicMock(base_url="x", model="m", api_key="k")
        cfg = build_ta_llm_config(ai_client, deep_model="claude-sonnet-4")
        self.assertEqual(cfg["deep_think_llm"], "claude-sonnet-4")
        self.assertEqual(cfg["quick_think_llm"], "claude-sonnet-4")

    def test_both_default_to_ai_client_model(self):
        """两个模型都未指定 — 都用 ai_client.model"""
        ai_client = MagicMock(base_url="x", model="default", api_key="k")
        cfg = build_ta_llm_config(ai_client)
        self.assertEqual(cfg["deep_think_llm"], "default")
        self.assertEqual(cfg["quick_think_llm"], "default")

    def test_agent_init_has_new_phase_b_fields(self):
        """Agent 实例化 — Phase B 新增字段都正确暴露"""
        agent = TradingAgentsAgent(
            deep_model="claude-sonnet-4",
            quick_model="claude-haiku",
            timeout_minutes=20,
            emit_paper_trading_signal=True,
            enable_sec_edgar=True,
            holding_period_days=10,
        )
        self.assertEqual(agent.deep_model, "claude-sonnet-4")
        self.assertEqual(agent.quick_model, "claude-haiku")
        self.assertEqual(agent.timeout_minutes, 20)
        self.assertTrue(agent.emit_paper_trading_signal)
        self.assertTrue(agent.enable_sec_edgar)
        self.assertEqual(agent.holding_period_days, 10)

    def test_agent_init_has_bounded_llm_defaults(self):
        """TradingAgents 默认不能把供应商请求无限期挂起。"""
        agent = TradingAgentsAgent()
        self.assertEqual(agent.llm_timeout_seconds, 120)
        self.assertEqual(agent.llm_max_retries, 0)
        self.assertEqual(agent.llm_max_tokens, 4096)

    def test_graph_class_forwards_request_timeout_to_langchain(self):
        """上游未读取 timeout 配置时，适配类仍需把它传给 ChatOpenAI。"""
        from src.modules.automation.tradingagents.agent import _bounded_graph_class

        class BaseGraph:
            def __init__(self, config):
                self.config = config

            def _get_provider_kwargs(self):
                return {"max_retries": 0}

        graph_cls = _bounded_graph_class(BaseGraph)
        graph = graph_cls(config={"llm_timeout_seconds": 7})
        self.assertEqual(graph._get_provider_kwargs(), {"max_retries": 0, "timeout": 7.0})

    def test_paper_trading_bridge_disabled_skips(self):
        """模拟盘 bridge — enabled=False 直接 skip,不写库"""
        from src.modules.automation.tradingagents.decision import (
            maybe_emit_paper_trading_signal,
        )
        result = maybe_emit_paper_trading_signal(
            stock_symbol="600519",
            stock_market="CN",
            stock_name="贵州茅台",
            decision="buy",
            confidence=7.0,
            signal_text="...",
            reason="...",
            current_price=1300.0,
            enabled=False,
        )
        self.assertFalse(result)

    def test_paper_trading_bridge_sell_skipped(self):
        """模拟盘 bridge — SELL 不开新仓 (不会写 buy 信号)"""
        from src.modules.automation.tradingagents.decision import (
            maybe_emit_paper_trading_signal,
        )
        result = maybe_emit_paper_trading_signal(
            stock_symbol="600519",
            stock_market="CN",
            stock_name="X",
            decision="sell",
            confidence=7.0,
            signal_text="",
            reason="",
            current_price=1300.0,
            enabled=True,
        )
        self.assertFalse(result)

    def test_paper_trading_bridge_no_price_skipped(self):
        """模拟盘 bridge — 当前价缺失时不写信号(避免错价)"""
        from src.modules.automation.tradingagents.decision import (
            maybe_emit_paper_trading_signal,
        )
        result = maybe_emit_paper_trading_signal(
            stock_symbol="600519",
            stock_market="CN",
            stock_name="X",
            decision="buy",
            confidence=7.0,
            signal_text="",
            reason="",
            current_price=None,
            enabled=True,
        )
        self.assertFalse(result)


class TestPortfolioContext(unittest.TestCase):
    """0.5.0 持仓应走原生 PortfolioContext，而不是提示词注入。"""

    def _portfolio(self):
        from src.modules.automation.base import AccountInfo, PortfolioInfo, PositionInfo
        from src.platform.marketdata.models import MarketCode

        return PortfolioInfo(accounts=[
            AccountInfo(
                id=1,
                name="主账户",
                available_funds=280000.0,
                positions=[
                    PositionInfo(
                        account_id=1,
                        account_name="主账户",
                        stock_id=1,
                        symbol="600519",
                        name="贵州茅台",
                        market=MarketCode.CN,
                        cost_price=1280.0,
                        quantity=100,
                        trading_style="long",
                    ),
                    PositionInfo(
                        account_id=1,
                        account_name="主账户",
                        stock_id=2,
                        symbol="AAPL",
                        name="Apple",
                        market=MarketCode.US,
                        cost_price=200.0,
                        quantity=5,
                    ),
                ],
            )
        ])

    def test_to_tradingagents_portfolio_preserves_cash_and_positions(self):
        """PanWatch 持仓聚合为 0.5.0 的结构化现金、标的、数量和均价。"""
        from tradingagents.portfolio import PortfolioContext
        from src.modules.automation.tradingagents.data_context import to_tradingagents_portfolio

        result = to_tradingagents_portfolio(self._portfolio())

        self.assertIsInstance(result, PortfolioContext)
        self.assertEqual(result.cash, 280000.0)
        self.assertEqual(
            [(position.ticker, position.quantity, position.average_price) for position in result.positions],
            [("600519", 100.0, 1280.0), ("AAPL", 5.0, 200.0)],
        )

    def test_to_tradingagents_portfolio_returns_none_without_accounts(self):
        """没有账户快照时不伪造现金为零的用户持仓。"""
        from src.modules.automation.base import PortfolioInfo
        from src.modules.automation.tradingagents.data_context import to_tradingagents_portfolio

        self.assertIsNone(to_tradingagents_portfolio(PortfolioInfo()))

    def test_to_tradingagents_portfolio_preserves_short_positions(self):
        """0.5.0 Position.quantity 允许负数，空头不能在适配层被静默丢弃。"""
        from src.modules.automation.base import AccountInfo, PortfolioInfo, PositionInfo
        from src.platform.marketdata.models import MarketCode
        from src.modules.automation.tradingagents.data_context import to_tradingagents_portfolio

        portfolio = PortfolioInfo(accounts=[
            AccountInfo(
                id=1,
                name="主账户",
                available_funds=1000.0,
                positions=[PositionInfo(
                    account_id=1,
                    account_name="主账户",
                    stock_id=1,
                    symbol="AAPL",
                    name="Apple",
                    market=MarketCode.US,
                    cost_price=200.0,
                    quantity=-5,
                )],
            )
        ])

        result = to_tradingagents_portfolio(portfolio)

        assert [(position.ticker, position.quantity) for position in result.positions] == [
            ("AAPL", -5.0),
        ]

    def test_patch_instrument_context_preserves_past_and_portfolio_context(self):
        """标的元数据进入 0.5.0 instrument_context，不污染历史上下文和持仓上下文。"""
        from src.modules.automation.tradingagents.data_context import patch_instrument_context

        captured = {}

        def original(
            company_name,
            trade_date,
            asset_type="stock",
            past_context="",
            instrument_context="",
            portfolio_context="",
        ):
            captured.update({
                "past_context": past_context,
                "instrument_context": instrument_context,
                "portfolio_context": portfolio_context,
                "asset_type": asset_type,
            })
            return captured

        graph = MagicMock()
        graph.propagator.create_initial_state = original
        patch_instrument_context(graph, "STOCK METADATA")

        graph.propagator.create_initial_state(
            "AAPL",
            "2026-05-16",
            asset_type="stock",
            past_context="prior lesson X",
            instrument_context="upstream instrument facts",
            portfolio_context="native holdings",
        )

        self.assertEqual(captured["past_context"], "prior lesson X")
        self.assertIn("STOCK METADATA", captured["instrument_context"])
        self.assertIn("upstream instrument facts", captured["instrument_context"])
        self.assertEqual(captured["portfolio_context"], "native holdings")

    def test_patch_instrument_context_no_context_skips(self):
        """没有元数据时不替换上游方法。"""
        from src.modules.automation.tradingagents.data_context import patch_instrument_context

        graph = MagicMock()
        original = graph.propagator.create_initial_state
        patch_instrument_context(graph, "")
        self.assertEqual(graph.propagator.create_initial_state, original)

    def test_run_sync_passes_native_portfolio_to_v050_propagate(self):
        """运行入口必须把转换后的 portfolio 传给 0.5.0 propagate，而非提示词。"""
        from contextlib import nullcontext

        from tradingagents.graph import trading_graph
        from src.modules.automation.tradingagents import agent as agent_module
        from tradingagents.portfolio import PortfolioContext

        captured = {}

        class FakeGraph:
            def __init__(self, **kwargs):
                self.propagator = None
                self.total_cost = 0.01

            def propagate(self, company_name, trade_date, asset_type="stock", portfolio=None):
                captured.update({
                    "company_name": company_name,
                    "trade_date": trade_date,
                    "asset_type": asset_type,
                    "portfolio": portfolio,
                })
                return {"final_trade_decision": "**Rating**: Hold"}, "HOLD"

        agent = TradingAgentsAgent()
        ai_client = MagicMock(api_key="key")
        ta_config = {
            "selected_analysts": ["market"],
            "max_debate_rounds": 1,
            "deep_think_llm": "test-model",
        }
        with (
            patch.object(trading_graph, "TradingAgentsGraph", FakeGraph),
            patch.object(agent_module, "apply_compat_patches"),
            patch.object(agent_module, "inject_api_key_env"),
            patch.object(agent_module, "patch_route_to_vendor", lambda: nullcontext()),
            patch.object(agent_module, "panwatch_data_context", lambda *args, **kwargs: nullcontext()),
        ):
            result = agent._run_tradingagents_sync(
                ai_client=ai_client,
                symbol="600519",
                market="CN",
                ta_config=ta_config,
                progress_handler=None,
                panwatch_data={},
                stock_metadata_context="",
                portfolio=self._portfolio(),
            )

        self.assertEqual(result["decision"], "HOLD")
        self.assertEqual(captured["company_name"], "600519")
        self.assertIsInstance(captured["portfolio"], PortfolioContext)
        self.assertEqual(captured["portfolio"].positions[0].ticker, "600519")

    def test_run_tradingagents_sync_legacy_graph_without_portfolio(self):
        """兼容旧版 TradingAgents: propagate 签名无 portfolio 参数时平滑降级,不传该关键字且不报错。"""
        from contextlib import nullcontext
        from unittest.mock import MagicMock, patch
        from tradingagents.graph import trading_graph
        from src.modules.automation.tradingagents import agent as agent_module
        from src.modules.automation.tradingagents.agent import TradingAgentsAgent

        captured = {}

        class LegacyFakeGraph:
            def __init__(self, **kwargs):
                self.propagator = None
                self.total_cost = 0.01

            def propagate(self, company_name, trade_date, asset_type="stock"):
                captured.update({
                    "company_name": company_name,
                    "trade_date": trade_date,
                    "asset_type": asset_type,
                })
                return {"final_trade_decision": "**Rating**: Buy"}, "BUY"

        agent = TradingAgentsAgent()
        ai_client = MagicMock(api_key="key")
        ta_config = {
            "selected_analysts": ["market"],
            "max_debate_rounds": 1,
            "deep_think_llm": "test-model",
        }
        with (
            patch.object(trading_graph, "TradingAgentsGraph", LegacyFakeGraph),
            patch.object(agent_module, "apply_compat_patches"),
            patch.object(agent_module, "inject_api_key_env"),
            patch.object(agent_module, "patch_route_to_vendor", lambda: nullcontext()),
            patch.object(agent_module, "panwatch_data_context", lambda *args, **kwargs: nullcontext()),
        ):
            result = agent._run_tradingagents_sync(
                ai_client=ai_client,
                symbol="600519",
                market="CN",
                ta_config=ta_config,
                progress_handler=None,
                panwatch_data={},
                stock_metadata_context="",
                portfolio=self._portfolio(),
            )

        self.assertEqual(result["decision"], "BUY")
        self.assertEqual(captured["company_name"], "600519")
        self.assertNotIn("portfolio", captured)


class TestAgentCollect(unittest.IsolatedAsyncioTestCase):
    async def test_collect_from_marketdata_package(self):
        """collect() — 走 marketdata 包(quote→dict / capital_flow→list)"""
        from datetime import datetime as _dt

        from src.modules.automation.tradingagents import agent as agent_module
        from marketdata import Bar, CapitalFlow, EventItem, Quote

        agent = TradingAgentsAgent()

        stock = MagicMock()
        stock.symbol = "600519"
        stock.name = "贵州茅台"
        stock.market = MagicMock()
        stock.market.value = "CN"

        context = MagicMock()
        context.watchlist = [stock]

        fake_quote = Quote(symbol="600519", market="CN", current_price=1332.95, name="贵州茅台")
        fake_bar = Bar(date="2026-05-15", open=1300.0, close=1332.95, high=1340.0, low=1290.0, volume=1000.0)
        fake_flow = CapitalFlow(symbol="600519", name="贵州茅台", main_net_inflow=1000000.0)
        fake_event = EventItem(
            source="em", external_id="1", event_type="announcement", title="测试公告",
            publish_time=_dt.now(), symbols=["600519"], importance=1, url="",
        )

        fake_md = MagicMock()
        fake_md.quotes = MagicMock(return_value=[fake_quote])
        fake_md.klines = MagicMock(return_value=[fake_bar])
        fake_md.capital_flow = MagicMock(return_value=fake_flow)
        fake_md.events = MagicMock(return_value=[fake_event])

        with patch.object(agent_module, "get_market_data", lambda: fake_md):
            data = await agent.collect(context)

        self.assertEqual(data["stock"], stock)
        self.assertIsInstance(data["quote"], dict)
        self.assertEqual(data["quote"]["current_price"], 1332.95)
        self.assertIsInstance(data["capital_flow"], list)
        self.assertEqual(len(data["capital_flow"]), 1)
        self.assertEqual(data["capital_flow"][0], fake_flow)
        self.assertIsInstance(data["klines"], list)
        self.assertEqual(data["klines"], [fake_bar])
        fake_md.klines.assert_called_once_with("600519", market="CN", days=750)
        self.assertIsInstance(data["events"], list)
        self.assertEqual(data["events"], [fake_event])
        self.assertIn("fetched_at", data)


if __name__ == "__main__":
    unittest.main()
