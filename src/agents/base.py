import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from src.core.ai_client import AIClient
from src.core.notifier import NotifierManager
from src.config import AppConfig, StockConfig
from src.models.market import MarketCode
from src.core.notify_dedupe import build_notify_dedupe_key, check_and_mark_notify
from src.core.notify_policy import NotifyPolicy
from src.core.log_context import log_context

logger = logging.getLogger(__name__)


@dataclass
class PositionInfo:
    """单个持仓信息"""

    account_id: int
    account_name: str
    stock_id: int
    symbol: str
    name: str
    market: MarketCode
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str = "swing"  # short: 短线, swing: 波段, long: 长线

    @property
    def cost_value(self) -> float:
        """持仓成本"""
        return self.cost_price * self.quantity


@dataclass
class AccountInfo:
    """账户信息"""

    id: int
    name: str
    available_funds: float
    positions: list[PositionInfo] = field(default_factory=list)

    @property
    def total_cost(self) -> float:
        """账户总持仓成本"""
        return sum(p.cost_value for p in self.positions)


@dataclass
class PortfolioInfo:
    """持仓组合信息"""

    accounts: list[AccountInfo] = field(default_factory=list)

    @property
    def total_available_funds(self) -> float:
        """总可用资金"""
        return sum(a.available_funds for a in self.accounts)

    @property
    def total_cost(self) -> float:
        """总持仓成本"""
        return sum(a.total_cost for a in self.accounts)

    @property
    def all_positions(self) -> list[PositionInfo]:
        """所有持仓列表"""
        result = []
        for acc in self.accounts:
            result.extend(acc.positions)
        return result

    def get_positions_for_stock(self, symbol: str) -> list[PositionInfo]:
        """获取某只股票在各账户的持仓"""
        return [p for p in self.all_positions if p.symbol == symbol]

    def get_aggregated_position(self, symbol: str) -> dict | None:
        """
        获取某只股票的汇总持仓（合并所有账户）
        返回: {"symbol", "name", "total_quantity", "avg_cost", "total_cost", "trading_style", "positions"}
        """
        positions = self.get_positions_for_stock(symbol)
        if not positions:
            return None

        total_quantity = sum(p.quantity for p in positions)
        total_cost = sum(p.cost_value for p in positions)
        avg_cost = total_cost / total_quantity if total_quantity > 0 else 0
        # 取第一个持仓的交易风格（如果同一股票在多个账户有不同风格，优先取短线）
        trading_style = positions[0].trading_style
        for p in positions:
            if p.trading_style == "short":
                trading_style = "short"
                break

        return {
            "symbol": symbol,
            "name": positions[0].name,
            "market": positions[0].market,
            "total_quantity": total_quantity,
            "avg_cost": avg_cost,
            "total_cost": total_cost,
            "trading_style": trading_style,
            "positions": positions,
        }

    def has_position(self, symbol: str) -> bool:
        """是否持有某只股票"""
        return any(p.symbol == symbol for p in self.all_positions)


@dataclass
class AgentContext:
    """Agent 运行时上下文"""

    ai_client: AIClient
    notifier: NotifierManager
    config: AppConfig
    portfolio: PortfolioInfo = field(default_factory=PortfolioInfo)
    model_label: str = ""  # e.g. "智谱/glm-4-flash"
    notify_policy: NotifyPolicy | None = None
    suppress_notify: bool = False

    @property
    def watchlist(self) -> list[StockConfig]:
        return self.config.watchlist


@dataclass
class AnalysisResult:
    """分析结果"""

    agent_name: str
    title: str
    content: str
    raw_data: dict = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)


class BaseAgent(ABC):
    """Agent 抽象基类"""

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    async def collect(self, context: AgentContext) -> dict:
        """采集数据"""
        ...

    @abstractmethod
    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        """
        构建 prompt。

        Returns:
            (system_prompt, user_content)
        """
        ...

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """调用 AI 分析"""
        system_prompt, user_content = self.build_prompt(data, context)
        content = await context.ai_client.chat(system_prompt, user_content)

        # 标题含股票信息
        stock_names = "、".join(s.name for s in context.watchlist[:5])
        if len(context.watchlist) > 5:
            stock_names += f" 等{len(context.watchlist)}只"
        title = f"【{self.display_name}】{stock_names}"

        # 结尾附 AI 模型信息
        if context.model_label:
            content = content.rstrip() + f"\n\n---\nAI: {context.model_label}"

        return AnalysisResult(
            agent_name=self.name,
            title=title,
            content=content,
            raw_data=data,
        )

    async def should_notify(self, result: AnalysisResult) -> bool:
        """是否需要通知，子类可重写"""
        return True

    def _notify_dedupe_ttl_minutes(self, context: AgentContext) -> int:
        """Notification idempotency window (minutes).

        P0 policy: per-agent defaults to avoid duplicate notifications.
        """

        if self.name in ("daily_report", "premarket_outlook"):
            default = 12 * 60
        elif self.name == "news_digest":
            default = 60
        elif self.name == "chart_analyst":
            default = 6 * 60
        # Intraday uses its own per-stock throttle.
        elif self.name == "intraday_monitor":
            default = 30
        else:
            default = 60

        policy = getattr(context, "notify_policy", None)
        if policy:
            try:
                return policy.dedupe_ttl_minutes(self.name, default)
            except Exception:
                return default
        return default

    async def run(self, context: AgentContext) -> AnalysisResult:
        """标准执行流程"""
        logger.info(f"Agent [{self.display_name}] 开始执行")

        try:
            data = await self.collect(context)
            result = await self.analyze(context, data)

            if getattr(context, "suppress_notify", False):
                with log_context(
                    event="notify_skipped",
                    notify_status="skipped",
                    notify_reason="suppressed",
                ):
                    logger.info(f"Agent [{self.display_name}] 本次触发已禁用通知")
                result.raw_data["notified"] = False
                result.raw_data["notify_skipped"] = "suppressed"
                return result

            notified = False
            if await self.should_notify(result):
                # Quiet hours: skip sending without marking as error.
                policy = getattr(context, "notify_policy", None)
                if policy:
                    try:
                        if policy.is_quiet_now():
                            with log_context(
                                event="notify_skipped",
                                notify_status="skipped",
                                notify_reason="quiet_hours",
                            ):
                                logger.info(f"Agent [{self.display_name}] 静默时段跳过通知")
                            result.raw_data["notified"] = False
                            result.raw_data["notify_skipped"] = "quiet_hours"
                            return result
                    except Exception:
                        pass

                # Global notification dedupe (idempotency):
                # avoids repeated pushes when an agent is triggered multiple times.
                ttl = self._notify_dedupe_ttl_minutes(context)
                dedupe_key = build_notify_dedupe_key(
                    self.name, result.title, result.content
                )
                scope = f"__notify__:{dedupe_key}"
                allowed = check_and_mark_notify(
                    agent_name=self.name,
                    scope=scope,
                    ttl_minutes=ttl,
                    mark=False,
                )
                if not allowed:
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason="deduped",
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] 通知去重命中，跳过发送 (ttl={ttl}m)"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = "deduped"
                    return result

                with log_context(event="notify_send", notify_status="attempted"):
                    logger.info(f"Agent [{self.display_name}] 开始发送通知")
                notify_result = await context.notifier.notify_with_result(
                    result.title,
                    result.content,
                    result.images,
                )
                if notify_result.get("skipped"):
                    with log_context(
                        event="notify_skipped",
                        notify_status="skipped",
                        notify_reason=str(notify_result.get("skipped") or ""),
                    ):
                        logger.info(
                            f"Agent [{self.display_name}] 通知已跳过: {notify_result.get('skipped')}"
                        )
                    result.raw_data["notified"] = False
                    result.raw_data["notify_skipped"] = notify_result.get("skipped")
                    return result

                notified = bool(notify_result.get("success"))
                if notified:
                    with log_context(
                        event="notify_sent",
                        notify_status="sent",
                    ):
                        logger.info(f"Agent [{self.display_name}] 通知已发送")
                    # Mark dedupe only after a successful send.
                    check_and_mark_notify(
                        agent_name=self.name,
                        scope=scope,
                        ttl_minutes=ttl,
                        mark=True,
                    )
                else:
                    notify_error = notify_result.get("error") or "未知错误"
                    with log_context(
                        event="notify_failed",
                        notify_status="failed",
                        notify_reason=str(notify_error),
                    ):
                        logger.error(
                            f"Agent [{self.display_name}] 通知发送失败: {notify_error}"
                        )
                    result.raw_data["notify_error"] = notify_error
            else:
                logger.info(f"Agent [{self.display_name}] 无需通知")

            # 记录是否发送了通知
            result.raw_data["notified"] = notified
            return result

        except Exception as e:
            logger.error(f"Agent [{self.display_name}] 执行失败: {e}")
            raise
