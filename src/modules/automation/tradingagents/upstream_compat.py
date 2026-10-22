"""Feature-detected TradingAgents import boundaries: official 0.5 and fork 0.6."""
from importlib import import_module
from types import ModuleType


def upstream_module(legacy: str, modern: str) -> ModuleType:
    """Do not mask missing dependencies inside an otherwise present module."""
    try:
        return import_module(legacy)
    except ModuleNotFoundError as exc:
        if exc.name and not (legacy == exc.name or legacy.startswith(exc.name + ".")):
            raise
        return import_module(modern)


def vendor_router() -> ModuleType:
    return upstream_module("tradingagents.dataflows.interface", "tradingagents.dataflows.router")


def ohlcv_module() -> ModuleType:
    return upstream_module("tradingagents.dataflows.stockstats_utils", "tradingagents.dataflows.vendors.yahoo.ohlcv")


def snapshot_module() -> ModuleType:
    return upstream_module("tradingagents.dataflows.market_data_validator", "tradingagents.dataflows.vendors.yahoo.snapshot")


def validate_upstream_contract() -> None:
    """Reject incompatible custom builds before starting an analysis."""
    from inspect import signature
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    if not callable(getattr(TradingAgentsGraph, "_get_provider_kwargs", None)):
        _modern_timeout_boundary(TradingAgentsGraph)
        _modern_portfolio_boundary(TradingAgentsGraph)

    checks = (
        (vendor_router(), "route_to_vendor", ("get_stock_data", "SYMBOL"), {"vendor": "yfinance"}),
        (ohlcv_module(), "load_ohlcv", ("SYMBOL", "2026-01-01"), {"fill_gaps": False}),
        (snapshot_module(), "build_verified_market_snapshot", ("SYMBOL", "2026-01-01"), {"indicators": None}),
    )
    for owner, name, args, kwargs in checks:
        try:
            signature(getattr(owner, name)).bind(*args, **kwargs)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported TradingAgents API: {name}; install a compatible source (see docs/tradingagents-source.md)") from exc
    for owner, name, parameter in (
        (TradingAgentsGraph, "propagate", "portfolio"),
        (Propagator, "create_initial_state", "portfolio_context"),
        (Propagator, "get_graph_args", "callbacks"),
    ):
        try:
            if parameter not in signature(getattr(owner, name)).parameters:
                raise TypeError("missing required parameter")
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported TradingAgents API: {name}; install a compatible source (see docs/tradingagents-source.md)") from exc


def _function_with_globals(function, **bindings):
    """Copy an extension boundary without modifying another graph's globals."""
    from types import FunctionType

    copied = FunctionType(function.__code__, {**function.__globals__, **bindings},
                          function.__name__, function.__defaults__, function.__closure__)
    copied.__kwdefaults__ = function.__kwdefaults__
    return copied


def _modern_timeout_boundary(graph_cls):
    """Recognize the narrow config -> tier -> kwargs extension contract."""
    from inspect import signature
    from types import FunctionType

    try:
        constructor = graph_cls.__init__
        tier = constructor.__globals__["create_tier_client"]
        builder = tier.__globals__["build_llm_kwargs"]
        if not all(isinstance(fn, FunctionType) for fn in (constructor, tier, builder)):
            raise TypeError("factory must be a Python extension boundary")
        if "create_tier_client" not in constructor.__code__.co_names or "build_llm_kwargs" not in tier.__code__.co_names:
            raise TypeError("constructor does not use the supported factory")
        signature(tier).bind({}, "deep")
        signature(builder).bind({})
        return constructor, tier, builder
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Unsupported TradingAgents API: LLM timeout boundary; install a compatible source (see docs/tradingagents-source.md)") from exc


def _modern_portfolio_boundary(graph_cls):
    from inspect import signature

    try:
        signature(graph_cls.create_run_state).bind(None, "SYMBOL", "2026-01-01", "stock", None)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("Unsupported TradingAgents API: portfolio state boundary; install a compatible source (see docs/tradingagents-source.md)") from exc


def _modern_portfolio(portfolio, ticker, config):
    """Use this run's verified CN identity, never exchange-prefix guesses.

    Market-tagged lots are a PanWatch-only opt-in. Adapt a new book after
    upstream identity resolution; the input book, graph globals and other
    concurrently running graphs remain untouched.
    """
    import re
    from .data_context import _aggregate_portfolio_positions

    if portfolio is None or not all(hasattr(p, "market") for p in portfolio.positions):
        return portfolio
    identity = config.get("_ashare_identity")
    resolved_cn = (isinstance(identity, dict)
                   and identity.get("status") == "resolved"
                   and identity.get("security_type") == "A-share"
                   and identity.get("confidence") == "verified"
                   and identity.get("canonical_symbol") == ticker
                   and re.fullmatch(r"[0-9]{6}\.(?:SS|SZ|BJ)", ticker))
    bare = ticker.split(".", 1)[0]
    lots = [(ticker if resolved_cn and p.market == "CN" and p.ticker == bare else p.ticker,
             p.market, p.quantity, p.average_price) for p in portfolio.positions]
    return portfolio.model_copy(update={
        "positions": _aggregate_portfolio_positions(
            lots, preserve_markets=True,
            position_type=type(portfolio.positions[0]) if portfolio.positions else None,
        ),
    })


def bounded_graph_class(graph_cls):
    """Forward timeout through legacy or modern factories, isolated per graph.

    Modern upstream has no overridable method. Copy only its Python function
    namespaces at construction, replacing the factory binding locally. Never
    patch module globals or publish request config across concurrent graphs.
    """
    from .toolkit_adapter import _cancellation_boundary

    class CancellableGraph(graph_cls):
        # These methods execute outside LangGraph callbacks. The supported SDKs
        # write their final report before recording a future memory decision.
        # Guard _log_state as well so an in-flight report cannot be followed by
        # a new memory write if cancellation arrived while writing that report.
        @_cancellation_boundary
        def propagate(self, *args, **kwargs):
            return super().propagate(*args, **kwargs)

        @_cancellation_boundary
        def create_run_state(self, *args, **kwargs):
            return super().create_run_state(*args, **kwargs)

        @_cancellation_boundary
        def record_decision(self, *args, **kwargs):
            return super().record_decision(*args, **kwargs)

        @_cancellation_boundary
        def _log_state(self, *args, **kwargs):
            return super()._log_state(*args, **kwargs)

    if callable(getattr(graph_cls, "_get_provider_kwargs", None)):
        class BoundedTradingAgentsGraph(CancellableGraph):
            def _get_provider_kwargs(self):
                kwargs = dict(super()._get_provider_kwargs())
                timeout = self.config.get("llm_timeout_seconds")
                if timeout is not None and timeout != "":
                    kwargs["timeout"] = float(timeout)
                return kwargs
    else:
        _modern_timeout_boundary(graph_cls)
        _modern_portfolio_boundary(graph_cls)

        class BoundedTradingAgentsGraph(CancellableGraph):
            _panwatch_preserve_portfolio_markets = True

            def create_run_state(self, company_name, trade_date, asset_type="stock", portfolio=None):
                state = super().create_run_state(company_name, trade_date, asset_type, portfolio)
                if asset_type == "stock":
                    ticker = state["company_of_interest"]
                    adapted = _modern_portfolio(portfolio, ticker, self.config)
                    if adapted is not portfolio:
                        state["portfolio_context"] = adapted.render(ticker)
                return state

            def __init__(self, *args, **kwargs):
                constructor, tier, builder = _modern_timeout_boundary(graph_cls)

                def bounded_kwargs(config):
                    result = dict(builder(config))
                    timeout = config.get("llm_timeout_seconds")
                    if timeout is not None and timeout != "":
                        result["timeout"] = float(timeout)
                    return result

                bounded_tier = _function_with_globals(tier, build_llm_kwargs=bounded_kwargs)
                bounded_init = _function_with_globals(constructor, create_tier_client=bounded_tier)
                bounded_init(self, *args, **kwargs)

    BoundedTradingAgentsGraph.__name__ = f"Bounded{getattr(graph_cls, '__name__', 'TradingAgentsGraph')}"
    return BoundedTradingAgentsGraph
