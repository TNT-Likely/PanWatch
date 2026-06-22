"""TradingAgentsAgent — PanWatch 的 BaseAgent 子类,集成 TauricResearch/TradingAgents。

设计要点(详见 .docs/tradingagents/02-technical-design.md):
1. collect() 走 PanWatch Provider Orchestrator,4 类数据并发拉
2. analyze() 重写,不走单次 ai_client.chat,而是调 TradingAgentsGraph
3. monkeypatch route_to_vendor 让 TradingAgents 拿到 PanWatch 数据(A 股专用)
4. progress callback + cost tracker + 月度预算 + 同日缓存
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

from src.agents.base import AgentContext, AnalysisResult, BaseAgent
from src.agents.tradingagents.cost_tracker import (
    check_budget,
    estimate_cost,
    get_today_cache_key,
)
from src.agents.tradingagents.langchain_compat import apply_compat_patches
from src.agents.tradingagents.llm_adapter import (
    VALID_ANALYSTS,
    build_ta_llm_config,
    inject_api_key_env,
)
from src.agents.tradingagents.portfolio_context import (
    build_portfolio_context,
    build_stock_metadata_context,
    patch_propagator,
)
from src.agents.tradingagents.progress import PanWatchProgressHandler
from src.agents.tradingagents.result_mapper import map_state_to_result
from src.agents.tradingagents.toolkit_adapter import (
    panwatch_data_context,
    patch_route_to_vendor,
)
from src.core.analysis_history import get_analysis, save_analysis

logger = logging.getLogger(__name__)


class TradingAgentsUnavailable(RuntimeError):
    """tradingagents 库未安装或上游 API 变更导致不可用。"""


class TradingAgentsAgent(BaseAgent):
    name = "tradingagents"
    display_name = "TradingAgents 深度分析"
    description = "多 Agent 投资决策框架,3-5 分钟,~$0.05/次 (deepseek-chat)"

    def __init__(
        self,
        analyst_types: list[str] | None = None,
        debate_rounds: int = 1,
        monthly_budget_usd: float = 10.0,
        over_budget_action: str = "reject",  # reject / warn / continue
        cache_ttl_hours: int = 12,
        output_language: str = "Chinese",
        deep_model: str | None = None,    # 推理/辩论/PM 用的强模型 (留空走默认)
        quick_model: str | None = None,   # 分析师工具调用用的快模型 (留空 = deep_model)
        timeout_minutes: int = 30,        # 整个流程硬超时;0.3.0 工具链更重,默认提到 30 min
        emit_paper_trading_signal: bool = False,  # 是否把 BUY 决策写入 StrategySignalRun 驱动模拟盘
    ):
        # 校验分析师配置
        analysts = list(analyst_types or sorted(VALID_ANALYSTS))
        invalid = [a for a in analysts if a not in VALID_ANALYSTS]
        if invalid:
            raise ValueError(
                f"非法 analyst 名: {invalid}; "
                f"合法值: {sorted(VALID_ANALYSTS)}"
            )

        self.analyst_types = analysts
        self.debate_rounds = max(1, int(debate_rounds))
        self.monthly_budget_usd = float(monthly_budget_usd)
        self.over_budget_action = over_budget_action
        self.cache_ttl_hours = max(0, int(cache_ttl_hours))
        self.output_language = output_language
        self.deep_model = (deep_model or "").strip() or None
        self.quick_model = (quick_model or "").strip() or None
        self.timeout_minutes = max(1, int(timeout_minutes))
        self.emit_paper_trading_signal = bool(emit_paper_trading_signal)

        # 软依赖检测
        self._available, self._import_error = self._check_availability()

    # ---- BaseAgent 抽象方法 ----

    async def collect(self, context: AgentContext) -> dict:
        """从 PanWatch Provider 体系收集数据,并发拉 4 类。"""
        from src.core.providers import (
            ProviderRequest,
            get_capital_flow_orchestrator,
            get_events_orchestrator,
            get_kline_orchestrator,
            get_quote_orchestrator,
        )

        if not context.watchlist:
            raise ValueError("TradingAgents 需要至少 1 只股票")
        # 单只标的为粒度;若 watchlist 多只,取第一只
        stock = context.watchlist[0]
        req = ProviderRequest(symbols=(stock.symbol,), market=stock.market.value)

        kline_req = ProviderRequest(
            symbols=(stock.symbol,),
            market=stock.market.value,
            extra=(("days", 120),),
        )

        # 事件/公告:深度分析需要更宽窗口(默认 since_hours=12 太短,只有当天才有)。
        # 拉 30 天给 TradingAgents 的新闻分析师当个股新闻使用。
        events_req = ProviderRequest(
            symbols=(stock.symbol,),
            market=stock.market.value,
            since_hours=24 * 30,
        )

        quotes_t = get_quote_orchestrator().fetch(req)
        klines_t = get_kline_orchestrator().fetch(kline_req)
        capital_t = get_capital_flow_orchestrator().fetch(req)
        events_t = get_events_orchestrator().fetch(events_req)

        try:
            quotes, klines, capital, events = await asyncio.gather(
                quotes_t, klines_t, capital_t, events_t
            )
        except Exception as e:
            logger.warning(f"[TA] 数据收集部分失败: {e}")
            quotes = klines = capital = events = None

        def _data(resp, default):
            return resp.data if resp and resp.success else default

        # A 股 fetch 真实财报(akshare),非 A 股留空
        financial: dict | None = None
        if stock.market.value == "CN" and stock.symbol.isdigit() and len(stock.symbol) == 6:
            try:
                from src.agents.tradingagents.financial_data import fetch_financial_abstract
                financial = await asyncio.to_thread(fetch_financial_abstract, stock.symbol)
            except Exception as e:
                logger.warning(f"[TA] 拉财报失败: {e}")
                financial = None

        # 预算技术指标(MA/MACD/RSI/KDJ/BOLL),给 get_indicators 工具用
        technical = None
        try:
            from src.collectors.kline_collector import KlineCollector
            technical = await asyncio.to_thread(
                KlineCollector(stock.market).get_technical_indicators, stock.symbol
            )
        except Exception as e:
            logger.debug(f"[TA] 技术指标预算失败,LLM 仍可从 K线 CSV 自行计算: {e}")

        return {
            "stock": stock,
            "quote": (_data(quotes, []) or [{}])[0] if _data(quotes, []) else {},
            "klines": _data(klines, []) or [],
            "capital_flow": _data(capital, []) or [],
            "events": _data(events, []) or [],
            "financial": financial,
            "technical": technical,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        # BaseAgent 抽象要求,但本 agent 不走单次 prompt
        return "", ""

    async def run_single(self, context: AgentContext, symbol: str) -> AnalysisResult:
        """单只股票模式入口 — 供 AgentScheduler 调度时按股票迭代调用。

        典型场景:盘前自动跑用户绑定到 tradingagents 的核心仓位股票。
        实现:过滤 watchlist 到指定 symbol,然后走标准 run() 流程。
        """
        # 找到目标 stock
        targets = [s for s in context.watchlist if s.symbol == symbol]
        if not targets:
            raise ValueError(
                f"run_single: symbol={symbol} 不在 watchlist 中,跳过"
            )

        # 浅克隆 context.config 让 watchlist 只剩目标股票,其他字段不变
        from copy import copy
        from src.config import AppConfig

        narrow_config = AppConfig(
            settings=context.config.settings,
            watchlist=targets,
        )
        narrow_context = copy(context)
        narrow_context.config = narrow_config
        return await self.run(narrow_context)

    # ---- 重写 analyze:走 TradingAgents 多 Agent 流 ----

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        if not self._available:
            raise TradingAgentsUnavailable(self._import_error)

        stock = data["stock"]
        trace_id = getattr(context, "_trace_id", "") or self._make_trace_id(stock.symbol)
        force_refresh = bool(getattr(context, "_force_refresh", False))

        # 0) 同日缓存命中(force_refresh=True 时跳过)
        if not force_refresh:
            cached = self._try_cache_hit(stock)
            if cached is not None:
                logger.info(
                    f"[TA] 命中同日缓存 (agent=tradingagents symbol={stock.symbol})"
                )
                cached.raw_data["from_cache"] = True
                return cached

        # 1) 预算检查
        budget = check_budget(self.monthly_budget_usd, self.name)
        if budget["exceeded"]:
            if self.over_budget_action == "reject":
                raise RuntimeError(
                    f"本月 TradingAgents 预算已用尽 "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f})。"
                    f"如需继续使用,请在「设置」中调高预算上限。"
                )
            elif self.over_budget_action == "warn":
                logger.warning(
                    f"[TA] 预算已超,但策略=warn,继续执行 "
                    f"(${budget['used']:.2f} / ${self.monthly_budget_usd:.2f})"
                )

        # 2) 构造 TradingAgents config (支持 deep / quick 双模型)
        ta_config = build_ta_llm_config(
            context.ai_client,
            debate_rounds=self.debate_rounds,
            selected_analysts=self.analyst_types,
            output_language=self.output_language,
            deep_model=self.deep_model,
            quick_model=self.quick_model,
        )

        # 3) 进度回调
        progress_handler = PanWatchProgressHandler(trace_id, self.name)

        # 4) 渲染上下文(标的元信息 + 用户持仓)注入到 TA 的 past_context 通道
        current_price = (data.get("quote") or {}).get("current_price")
        cur_price_num = current_price if isinstance(current_price, (int, float)) else None
        quote_data = data.get("quote") or {}

        meta_context = build_stock_metadata_context(
            stock_symbol=stock.symbol,
            stock_name=stock.name or "",
            market=stock.market.value,
            current_price=cur_price_num,
            industry=quote_data.get("industry", "") if isinstance(quote_data, dict) else "",
        )
        portfolio_part = build_portfolio_context(
            getattr(context, "portfolio", None),
            stock_symbol=stock.symbol,
            current_price=cur_price_num,
        )
        # 标的元信息永远放最前(即使没有持仓也注入)
        portfolio_context_text = (
            f"{meta_context}\n\n{portfolio_part}" if portfolio_part else meta_context
        )

        # 5) 同步阻塞,丢到线程池;加硬超时防卡死
        try:
            ta_result = await asyncio.wait_for(
                asyncio.to_thread(
                    self._run_tradingagents_sync,
                    ai_client=context.ai_client,
                    symbol=stock.symbol,
                    market=stock.market.value,
                    ta_config=ta_config,
                    progress_handler=progress_handler,
                    panwatch_data=data,
                    portfolio_context_text=portfolio_context_text,
                ),
                timeout=self.timeout_minutes * 60,
            )
        except asyncio.TimeoutError:
            # 超时:尝试落库部分进度供后续查看
            partial_cost = getattr(progress_handler, "_total_cost", 0.0)
            partial_stages = list(getattr(progress_handler, "_completed_stages", set()))
            logger.warning(
                f"[TA] 执行超时 (>{self.timeout_minutes} 分钟). "
                f"已完成阶段: {partial_stages}, 累计成本 ${partial_cost:.4f}"
            )
            partial_msg = (
                f"分析超时(>{self.timeout_minutes} 分钟)。"
                f"已完成 {len(partial_stages)} 个阶段,累计成本 ${partial_cost:.4f}。"
                f"建议:① 缩短 debate_rounds;② 换更快的模型(如 deepseek-chat);"
                f"③ 调高 timeout_minutes。"
            )
            raise RuntimeError(partial_msg)

        # 5) 映射成 AnalysisResult
        result = map_state_to_result(
            stock=stock,
            ta_result=ta_result,
            model_label=context.model_label,
        )

        # 存分析时实时价 → 历史决策表"分析价"立即显示(不必等当日 K线收盘回填)
        _quote = data.get("quote") or {}
        _cp = _quote.get("current_price")
        if isinstance(_cp, (int, float)):
            result.raw_data["price_at_analysis"] = float(_cp)

        # 5b) 把本次 trace_id 的 toolkit 诊断聚合,持久化进 raw_data,
        # 让历史报告 DoneView 也能展示数据注入情况。
        try:
            tid = getattr(progress_handler, "trace_id", "") if progress_handler else ""
            if tid:
                result.raw_data["toolkit_diagnostic"] = self._collect_toolkit_diagnostic(tid)
        except Exception as e:
            logger.warning(f"[TA] 收集 toolkit 诊断失败,忽略: {e}")

        # 6) 落库到 AnalysisHistory:供 UI 查最近一次结果 (DeepAnalysisModal 弹窗) +
        # 月度成本预算聚合。同标的同日复跑会覆盖 (analysis_history.save_analysis 语义)。
        try:
            save_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                content=result.content,
                title=result.title,
                raw_data=result.raw_data,
            )
        except Exception as e:
            logger.warning(f"[TA] save_analysis 失败,不影响主流程: {e}")

        # 6b) 落库到 StockSuggestion(建议池) — 让持仓页/关注列表上的建议徽章
        # 显示 TradingAgents 的 BUY/HOLD/SELL 决策(跟「盘前分析」「收盘复盘」并列)。
        try:
            from src.core.suggestion_pool import save_suggestion

            sug = result.raw_data.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "持有"
            signal_text = (sug.get("signal") or "")[:500]
            reason_text = (sug.get("reason") or "")[:1000]
            confidence = sug.get("confidence")
            confidence_text = (
                f" (置信度 {confidence:.1f}/10)" if isinstance(confidence, (int, float)) else ""
            )

            save_suggestion(
                stock_symbol=stock.symbol,
                stock_name=stock.name,
                stock_market=stock.market.value,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name=self.name,
                agent_label="TradingAgents 深度",
                signal=signal_text,
                reason=reason_text,
                expires_hours=24,  # 深度分析结果 24 小时内有效
                ai_response=result.content[:2000],
                meta={
                    "cost_usd": result.raw_data.get("cost_usd", 0),
                    "decision": result.raw_data.get("decision", "HOLD"),
                    "confidence": confidence,
                },
            )
        except Exception as e:
            logger.warning(f"[TA] save_suggestion 失败,不影响主流程: {e}")

        # 7) 可选:把 BUY/SELL 决策写入 StrategySignalRun 驱动模拟盘
        if self.emit_paper_trading_signal:
            try:
                from src.agents.tradingagents.paper_trading_bridge import (
                    maybe_emit_paper_trading_signal,
                )
                quote = data.get("quote") or {}
                current_price = quote.get("current_price")
                sug = result.raw_data.get("suggestion") or {}
                maybe_emit_paper_trading_signal(
                    stock_symbol=stock.symbol,
                    stock_market=stock.market.value,
                    stock_name=stock.name,
                    decision=str(sug.get("action") or ""),
                    confidence=float(sug.get("confidence") or 5.0),
                    signal_text=str(sug.get("signal") or ""),
                    reason=str(sug.get("reason") or ""),
                    current_price=current_price,
                    enabled=True,
                )
            except Exception as e:
                logger.warning(f"[TA] 写模拟盘信号失败,不影响主流程: {e}")

        return result

    # ---- 私有方法 ----

    def _check_availability(self) -> tuple[bool, str]:
        """检测 tradingagents 是否可用。"""
        try:
            import tradingagents  # noqa: F401
            from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
        except ImportError as e:
            return False, (
                "tradingagents 未安装。运行 `pip install -r requirements.txt` "
                "(或单独 `pip install \"tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git\"`)。"
                "公司代理下若失败,临时 `env -u HTTP_PROXY -u HTTPS_PROXY pip install -r requirements.txt`。"
                f"原始错误: {e}"
            )
        except Exception as e:
            return False, f"tradingagents 加载失败: {e}"
        return True, ""

    def _make_trace_id(self, symbol: str) -> str:
        return f"ta-{symbol}-{int(datetime.now().timestamp())}"

    def _try_cache_hit(self, stock) -> AnalysisResult | None:
        """同标的同日是否已分析过 → 返回缓存的 AnalysisResult。"""
        if self.cache_ttl_hours <= 0:
            return None
        try:
            history = get_analysis(
                agent_name=self.name,
                stock_symbol=stock.symbol,
                analysis_date=date.today(),
            )
        except Exception:
            return None
        if not history or not history.raw_data:
            return None
        return AnalysisResult(
            agent_name=self.name,
            title=history.title or f"【深度·缓存】{stock.name}({stock.symbol})",
            content=history.content,
            raw_data=dict(history.raw_data),
        )

    def _run_tradingagents_sync(
        self,
        *,
        ai_client,
        symbol: str,
        market: str,
        ta_config: dict,
        progress_handler,
        panwatch_data: dict,
        portfolio_context_text: str = "",
    ) -> dict[str, Any]:
        """在 worker 线程跑同步 TradingAgents 流程。

        步骤:
        1. inject_api_key_env 注入 API key 到环境变量
        2. patch_route_to_vendor 让 A 股请求路由到 PanWatch 数据
        3. TradingAgentsGraph.propagate 跑 3-5 分钟
        4. 返回 decision + final_state + cost_usd
        """
        # 关键依赖延迟 import,确保 _check_availability 失败时这里不被调用
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        # 应用 LangChain 兼容性补丁:让小模型 (Qwen 7B 等) 返回的
        # tool_calls.args 字符串被自动转 dict。
        apply_compat_patches()
        inject_api_key_env(ai_client)

        # patch + 数据上下文,确保 TradingAgents 调 route_to_vendor 时拿到 PanWatch 数据
        trace_id_for_ctx = getattr(progress_handler, "trace_id", "") if progress_handler else ""
        with patch_route_to_vendor(), panwatch_data_context(panwatch_data, trace_id=trace_id_for_ctx):
            graph = TradingAgentsGraph(
                selected_analysts=ta_config["selected_analysts"],
                debug=False,
                config=ta_config,
                # callbacks 接受 langchain BaseCallbackHandler 列表;LLM 级别用
                callbacks=[progress_handler] if progress_handler else None,
            )

            # 注入 LangGraph 节点级 callbacks(propagator.get_graph_args 默认 callbacks=None,
            # 不会触发 on_chain_start/end → 进度条永远卡 pending)
            if progress_handler is not None:
                self._inject_graph_callbacks(graph, progress_handler)

            # 注入用户持仓上下文到 past_context(上游官方扩展通道,PM 节点会读)
            if portfolio_context_text:
                patch_propagator(graph, portfolio_context_text)

            date_str = datetime.now().strftime("%Y-%m-%d")
            try:
                final_state, decision = graph.propagate(symbol, date_str)
            except TypeError:
                # 上游版本可能签名不同(propagate(symbol, date) vs propagate(company_name, trade_date))
                final_state, decision = graph.propagate(
                    company_name=symbol, trade_date=date_str
                )

        # 成本提取(TradingAgents 内部 token 统计;若上游未暴露,fallback 用 estimate)
        cost_usd = self._extract_cost_from_graph(graph) or self._fallback_cost_estimate(
            ta_config
        )

        return {
            "decision": str(decision or "HOLD").upper(),
            "final_state": dict(final_state) if final_state else {},
            "cost_usd": float(cost_usd or 0.0),
        }

    @staticmethod
    def _inject_graph_callbacks(graph, handler):
        """Monkey-patch graph.propagator.get_graph_args 让 LangGraph 节点级 callbacks 也注入。

        否则只有 on_llm_start/end 会触发,on_chain_start/end (节点切换) 不会,进度条卡死。
        """
        try:
            propagator = getattr(graph, "propagator", None)
            if propagator is None or not hasattr(propagator, "get_graph_args"):
                return
            original = propagator.get_graph_args

            def _patched(callbacks=None):
                cbs = list(callbacks or [])
                if handler not in cbs:
                    cbs.append(handler)
                return original(callbacks=cbs)

            propagator.get_graph_args = _patched  # type: ignore[method-assign]
        except Exception as e:
            logger.warning(f"[TA] 注入 LangGraph callbacks 失败: {e}")

    @staticmethod
    def _collect_toolkit_diagnostic(trace_id: str) -> dict:
        """查同 trace_id 的 ta_toolkit 日志,聚合成 {summary, recent}。"""
        from src.web.database import SessionLocal
        from src.web.models import LogEntry

        db = SessionLocal()
        try:
            rows = (
                db.query(LogEntry)
                .filter(LogEntry.trace_id == trace_id, LogEntry.event == "ta_toolkit")
                .order_by(LogEntry.id.asc())
                .all()
            )
        finally:
            db.close()

        summary = {"hit": 0, "miss": 0, "passthrough": 0, "fallthrough": 0, "error": 0}
        recent = []
        for r in rows:
            tags = r.tags or {}
            action = (tags.get("action") or "").lower()
            if action in summary:
                summary[action] += 1
            recent.append({
                "action": tags.get("action"),
                "method": tags.get("method"),
                "symbol": tags.get("symbol"),
                "chars": tags.get("chars"),
                "snippet": tags.get("snippet"),
                "source": tags.get("source"),
                "reason": tags.get("reason"),
            })
        return {"summary": summary, "recent": recent[-50:]}

    @staticmethod
    def _extract_cost_from_graph(graph) -> float:
        """尝试从 TradingAgentsGraph 实例提取累计成本。上游未必暴露字段,容错。"""
        for attr in ("total_cost", "total_cost_usd", "_total_cost"):
            v = getattr(graph, attr, None)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0

    def _fallback_cost_estimate(self, ta_config: dict) -> float:
        """fallback 用 estimate 平均值。"""
        est = estimate_cost(
            debate_rounds=ta_config.get("max_debate_rounds", 1),
            selected_analysts=ta_config.get("selected_analysts", []),
            model=ta_config.get("deep_think_llm", "deepseek-chat"),
        )
        return (est["cost_low_usd"] + est["cost_high_usd"]) / 2
