"""PanWatch 统一服务入口 - Web 后台 + Agent 调度"""

import logging
import os
import time
from contextlib import asynccontextmanager

import uvicorn

from src.web.database import init_db, SessionLocal
from src.web.models import (
    AgentConfig,
    Stock,
    StockAgent,
    AIService,
    AIModel,
    NotifyChannel,
    AppSettings,
    DataSource,
)
from src.web.log_handler import DBLogHandler
from src.config import Settings, AppConfig, StockConfig
from src.models.market import MarketCode
from src.core.ai_client import AIClient
from src.core.notifier import NotifierManager
from src.core.scheduler import AgentScheduler
from src.core.price_alert_scheduler import PriceAlertScheduler
from src.core.agent_runs import record_agent_run
from src.agents.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
from src.agents.daily_report import DailyReportAgent
from src.agents.news_digest import NewsDigestAgent
from src.agents.chart_analyst import ChartAnalystAgent
from src.agents.intraday_monitor import IntradayMonitorAgent
from src.agents.premarket_outlook import PremarketOutlookAgent

logger = logging.getLogger(__name__)

# 全局 scheduler 实例，供 agents API 调用
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None


def setup_ssl():
    """设置 SSL 证书环境（企业代理环境）"""
    settings = Settings()
    ca_cert = settings.ca_cert_file
    if not ca_cert or not os.path.exists(ca_cert):
        return

    import certifi

    bundle_path = os.path.join(os.path.dirname(__file__), "data", "ca-bundle.pem")
    os.makedirs(os.path.dirname(bundle_path), exist_ok=True)

    need_rebuild = not os.path.exists(bundle_path) or os.path.getmtime(
        ca_cert
    ) > os.path.getmtime(bundle_path)

    if need_rebuild:
        with open(bundle_path, "w") as out:
            with open(certifi.where(), "r") as f:
                out.write(f.read())
            out.write("\n")
            with open(ca_cert, "r") as f:
                out.write(f.read())

    os.environ["SSL_CERT_FILE"] = bundle_path
    os.environ["REQUESTS_CA_BUNDLE"] = bundle_path
    logger.info(f"SSL 证书已加载: {bundle_path}")


def setup_logging():
    """配置日志: 控制台 + 数据库"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # 控制台输出
    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # 数据库持久化
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)


def setup_playwright():
    """检查并安装 Playwright 浏览器

    本地开发时使用系统安装的 Playwright，Docker 环境下安装到 data 目录。
    通过 DOCKER 环境变量或显式设置的 PLAYWRIGHT_BROWSERS_PATH 来判断。
    """
    import subprocess

    # 允许通过环境变量跳过首次安装（例如不需要截图功能时）
    if os.environ.get("PLAYWRIGHT_SKIP_BROWSER_INSTALL") == "1":
        logger.info(
            "已设置 PLAYWRIGHT_SKIP_BROWSER_INSTALL=1，跳过 Playwright 浏览器安装"
        )
        return

    # 如果用户已显式设置 PLAYWRIGHT_BROWSERS_PATH，尊重该设置
    if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
        browser_dir = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        logger.info(f"使用自定义 Playwright 路径: {browser_dir}")
    # Docker 环境下安装到 data 目录
    elif os.environ.get("DOCKER") == "1":
        data_dir = os.environ.get("DATA_DIR", "./data")
        browser_dir = os.path.join(data_dir, "playwright")
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browser_dir
        logger.info(f"Docker 环境，Playwright 路径: {browser_dir}")
    else:
        # 本地开发，使用系统默认路径，不做任何安装
        logger.info("本地开发环境，使用系统 Playwright")
        return

    # 检查是否已安装
    if os.path.exists(browser_dir):
        try:
            dirs = os.listdir(browser_dir)
            if any(
                d.startswith("chromium")
                for d in dirs
                if os.path.isdir(os.path.join(browser_dir, d))
            ):
                logger.info(f"Playwright 浏览器已就绪: {browser_dir}")
                return
        except Exception:
            pass

    # 首次安装
    logger.info("首次启动，正在安装 Playwright 浏览器（可能需要几分钟）...")
    os.makedirs(browser_dir, exist_ok=True)

    try:
        result = subprocess.run(
            ["playwright", "install", "chromium"],
            env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browser_dir},
            capture_output=True,
            text=True,
            timeout=600,  # 10 分钟超时
        )
        if result.returncode == 0:
            logger.info("Playwright 浏览器安装完成")
        else:
            logger.error(f"Playwright 安装失败: {result.stderr}")
    except subprocess.TimeoutExpired:
        logger.error("Playwright 安装超时（网络问题？）")
    except FileNotFoundError:
        logger.warning("Playwright 命令不可用，K线截图功能不可用")
    except Exception as e:
        logger.error(f"Playwright 安装失败: {e}")


def seed_sample_stocks():
    """首次启动时添加示例股票"""
    db = SessionLocal()
    try:
        # 只在没有任何股票时才添加示例
        if db.query(Stock).count() > 0:
            return

        samples = [
            {"symbol": "600519", "name": "贵州茅台", "market": "CN"},
            {"symbol": "002594", "name": "比亚迪", "market": "CN"},
            {"symbol": "300750", "name": "宁德时代", "market": "CN"},
            {"symbol": "00700", "name": "腾讯控股", "market": "HK"},
            {"symbol": "AAPL", "name": "苹果", "market": "US"},
        ]
        for s in samples:
            db.add(Stock(**s))
        db.commit()
        logger.info("已添加 5 只示例股票（首次启动）")
    finally:
        db.close()


def seed_agents():
    """初始化内置 Agent 配置"""
    db = SessionLocal()
    agents = [
        {
            "name": "daily_report",
            "display_name": "盘后日报",
            "description": "每日收盘后生成自选股日报，包含大盘概览、个股分析和明日关注",
            "enabled": True,
            "schedule": "30 15 * * 1-5",
            "execution_mode": "batch",  # 批量模式：多只股票一起分析
        },
        {
            "name": "intraday_monitor",
            "display_name": "盘中监测",
            "description": "交易时段实时监控，AI 智能判断是否有值得关注的信号",
            "enabled": False,
            "schedule": "*/5 9-15 * * 1-5",  # 每5分钟扫描一次
            "execution_mode": "single",  # 单只模式：逐只分析，实时发送
            "config": {
                "event_only": True,
                "price_alert_threshold": 3.0,  # 涨跌幅超过3%触发
                "volume_alert_ratio": 2.0,  # 量比超过2倍触发
                "stop_loss_warning": -5.0,  # 亏损超过5%预警
                "take_profit_warning": 10.0,  # 盈利超过10%提醒
                "throttle_minutes": 30,  # 同一股票30分钟内不重复通知
            },
        },
        {
            "name": "news_digest",
            "display_name": "新闻速递",
            "description": "定时抓取与持仓相关的新闻资讯并推送摘要",
            "enabled": False,
            "schedule": "0 9-18/2 * * 1-5",
            "execution_mode": "batch",
            "config": {
                "since_hours": 12,
                "fallback_since_hours": 24,
            },
        },
        {
            "name": "premarket_outlook",
            "display_name": "盘前分析",
            "description": "开盘前综合昨日分析和隔夜信息，展望今日走势",
            "enabled": False,
            "schedule": "0 9 * * 1-5",
            "execution_mode": "batch",
        },
        {
            "name": "chart_analyst",
            "display_name": "技术分析",
            "description": "截取 K 线图并使用多模态 AI 进行技术分析",
            "enabled": False,
            "schedule": "0 15 * * 1-5",
            "execution_mode": "single",
        },
    ]

    for agent_data in agents:
        existing = (
            db.query(AgentConfig).filter(AgentConfig.name == agent_data["name"]).first()
        )
        if not existing:
            db.add(AgentConfig(**agent_data))
        else:
            # 始终同步 execution_mode（确保代码中的定义生效）
            existing.execution_mode = agent_data.get("execution_mode", "batch")
            # 同步 display_name 和 description
            existing.display_name = agent_data.get(
                "display_name", existing.display_name
            )
            existing.description = agent_data.get("description", existing.description)
            # 仅在用户未配置时补齐默认 config
            if agent_data.get("config") and (not existing.config):
                existing.config = agent_data.get("config")
            # 对已存在配置做“向前兼容”的字段补齐（不覆盖用户已有值）
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


def seed_data_sources():
    """初始化预置数据源"""
    db = SessionLocal()
    sources = [
        # 新闻类数据源
        {
            "name": "雪球资讯",
            "type": "news",
            "provider": "xueqiu",
            "config": {
                "cookies": "",
                "description": "雪球个股新闻聚合，需要登录 cookie",
            },
            "enabled": False,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["601127", "600519"],
        },
        {
            "name": "东方财富资讯",
            "type": "news",
            "provider": "eastmoney_news",
            "config": {},
            "enabled": True,
            "priority": 1,
            "supports_batch": False,  # 每只股票单独请求
            "test_symbols": ["601127", "600519"],
        },
        {
            "name": "东方财富公告",
            "type": "news",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 2,
            "supports_batch": True,  # 支持批量查询
            "test_symbols": ["601127", "600519"],
        },
        # K线数据源
        {
            "name": "腾讯K线",
            "type": "kline",
            "provider": "tencent",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["601127", "600519", "300750"],
        },
        # 资金流向数据源
        {
            "name": "东方财富资金流",
            "type": "capital_flow",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["601127", "600519"],
        },
        # 实时行情数据源
        {
            "name": "腾讯行情",
            "type": "quote",
            "provider": "tencent",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["601127", "600519", "300750"],
        },
        # 事件日历数据源（基于公告结构化）
        {
            "name": "东方财富事件日历",
            "type": "events",
            "provider": "eastmoney",
            "config": {},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["601127", "600519"],
        },
        # K线截图数据源
        {
            "name": "雪球K线截图",
            "type": "chart",
            "provider": "xueqiu",
            "config": {
                "viewport": {"width": 1280, "height": 900},
                "extra_wait_ms": 3000,
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": ["601127"],
        },
        {
            "name": "东方财富K线截图",
            "type": "chart",
            "provider": "eastmoney",
            "config": {
                "viewport": {"width": 1280, "height": 900},
                "extra_wait_ms": 2000,
            },
            "enabled": False,
            "priority": 1,
            "supports_batch": False,
            "test_symbols": ["601127"],
        },
    ]

    for source_data in sources:
        existing = (
            db.query(DataSource)
            .filter(
                DataSource.name == source_data["name"],
                DataSource.provider == source_data["provider"],
            )
            .first()
        )
        if existing:
            # 更新已存在记录的新字段（保留用户可能修改的配置）
            if existing.supports_batch != source_data.get("supports_batch", False):
                existing.supports_batch = source_data.get("supports_batch", False)
            if not existing.test_symbols:  # 只在空时更新
                existing.test_symbols = source_data.get("test_symbols", [])
        else:
            db.add(DataSource(**source_data))

    db.commit()
    db.close()
    logger.info("预置数据源初始化完成")


def load_watchlist_for_agent(agent_name: str) -> list[StockConfig]:
    """从数据库加载某个 Agent 关联的自选股"""
    db = SessionLocal()
    try:
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = [sa.stock_id for sa in stock_agents]
        if not stock_ids:
            return []

        # 绑定优先：只要绑定了 Agent，就纳入执行范围
        stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all()
        result = []
        for s in stocks:
            try:
                market = MarketCode(s.market)
            except ValueError:
                market = MarketCode.CN
            result.append(
                StockConfig(
                    symbol=s.symbol,
                    name=s.name,
                    market=market,
                )
            )
        return result
    finally:
        db.close()


def load_portfolio_for_agent(agent_name: str) -> PortfolioInfo:
    """从数据库加载某个 Agent 关联股票的持仓信息（包括多账户）"""
    from src.web.models import Account, Position

    db = SessionLocal()
    try:
        # 获取 Agent 关联的股票 ID
        stock_agents = (
            db.query(StockAgent).filter(StockAgent.agent_name == agent_name).all()
        )
        stock_ids = set(sa.stock_id for sa in stock_agents)
        if not stock_ids:
            return PortfolioInfo()

        # 获取所有启用的账户
        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            # 获取该账户中属于关联股票的持仓
            positions = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id.in_(stock_ids),
                )
                .all()
            )

            position_infos = []
            for pos in positions:
                stock = pos.stock
                if not stock:
                    continue
                try:
                    market = MarketCode(stock.market)
                except ValueError:
                    market = MarketCode.CN

                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def load_portfolio_for_stock(stock_id: int) -> PortfolioInfo:
    """从数据库加载单只股票的持仓信息"""
    from src.web.models import Account, Position

    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return PortfolioInfo()

        try:
            market = MarketCode(stock.market)
        except ValueError:
            market = MarketCode.CN

        accounts = db.query(Account).filter(Account.enabled == True).all()

        account_infos = []
        for acc in accounts:
            pos = (
                db.query(Position)
                .filter(
                    Position.account_id == acc.id,
                    Position.stock_id == stock_id,
                )
                .first()
            )

            position_infos = []
            if pos:
                position_infos.append(
                    PositionInfo(
                        account_id=acc.id,
                        account_name=acc.name,
                        stock_id=stock.id,
                        symbol=stock.symbol,
                        name=stock.name,
                        market=market,
                        cost_price=pos.cost_price,
                        quantity=pos.quantity,
                        invested_amount=pos.invested_amount,
                        trading_style=pos.trading_style or "swing",
                    )
                )

            account_infos.append(
                AccountInfo(
                    id=acc.id,
                    name=acc.name,
                    available_funds=acc.available_funds,
                    positions=position_infos,
                )
            )

        return PortfolioInfo(accounts=account_infos)
    finally:
        db.close()


def _get_proxy() -> str:
    """从 app_settings 获取 http_proxy"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def _get_app_setting(key: str) -> str:
    """从 app_settings 获取配置（不存在返回空字符串）"""
    db = SessionLocal()
    try:
        setting = db.query(AppSettings).filter(AppSettings.key == key).first()
        return setting.value if setting and setting.value else ""
    finally:
        db.close()


def resolve_ai_model(
    agent_name: str, stock_agent_id: int | None = None
) -> tuple[AIModel | None, AIService | None]:
    """解析 AI 模型: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)
    返回 (model, service) 元组"""
    db = SessionLocal()
    try:
        model_id = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.ai_model_id:
                model_id = sa.ai_model_id

        # 2. agent 级别默认
        if not model_id:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.ai_model_id:
                model_id = agent.ai_model_id

        # 3. 系统默认
        if not model_id:
            default_model = db.query(AIModel).filter(AIModel.is_default == True).first()
            if default_model:
                model_id = default_model.id

        # 4. 回退：取第一个
        if not model_id:
            first_model = db.query(AIModel).first()
            if first_model:
                model_id = first_model.id

        if not model_id:
            return None, None

        model = db.query(AIModel).filter(AIModel.id == model_id).first()
        if not model:
            return None, None

        service = db.query(AIService).filter(AIService.id == model.service_id).first()
        if model:
            db.expunge(model)
        if service:
            db.expunge(service)
        return model, service
    finally:
        db.close()


def resolve_notify_channels(
    agent_name: str, stock_agent_id: int | None = None
) -> list[NotifyChannel]:
    """解析通知渠道: stock_agent 覆盖 → agent 默认 → 系统默认(is_default=True)"""
    db = SessionLocal()
    try:
        channel_ids = None

        # 1. stock_agent 级别覆盖
        if stock_agent_id:
            sa = db.query(StockAgent).filter(StockAgent.id == stock_agent_id).first()
            if sa and sa.notify_channel_ids:
                channel_ids = sa.notify_channel_ids

        # 2. agent 级别默认
        if channel_ids is None:
            agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
            if agent and agent.notify_channel_ids:
                channel_ids = agent.notify_channel_ids

        # 3. 按 id 列表查询或取系统默认
        if channel_ids:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.id.in_(channel_ids),
                    NotifyChannel.enabled == True,
                )
                .all()
            )
        else:
            channels = (
                db.query(NotifyChannel)
                .filter(
                    NotifyChannel.is_default == True,
                    NotifyChannel.enabled == True,
                )
                .all()
            )

        for ch in channels:
            db.expunge(ch)
        return channels
    finally:
        db.close()


def _build_notifier(channels: list[NotifyChannel]) -> NotifierManager:
    """根据解析后的渠道列表构建 NotifierManager"""
    settings = Settings()
    # allow UI override via app_settings
    quiet_hours = _get_app_setting("notify_quiet_hours") or settings.notify_quiet_hours
    retry_attempts_raw = _get_app_setting("notify_retry_attempts")
    backoff_raw = _get_app_setting("notify_retry_backoff_seconds")
    overrides_raw = (
        _get_app_setting("notify_dedupe_ttl_overrides")
        or settings.notify_dedupe_ttl_overrides
    )

    try:
        retry_attempts = (
            int(retry_attempts_raw)
            if retry_attempts_raw
            else settings.notify_retry_attempts
        )
    except Exception:
        retry_attempts = settings.notify_retry_attempts
    try:
        retry_backoff_seconds = (
            float(backoff_raw) if backoff_raw else settings.notify_retry_backoff_seconds
        )
    except Exception:
        retry_backoff_seconds = settings.notify_retry_backoff_seconds

    from src.core.notify_policy import NotifyPolicy, parse_dedupe_overrides

    policy = NotifyPolicy(
        timezone=settings.app_timezone,
        quiet_hours=quiet_hours,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        dedupe_ttl_overrides=parse_dedupe_overrides(overrides_raw),
    )

    notifier = NotifierManager(policy=policy)
    for ch in channels:
        notifier.add_channel(ch.type, ch.config or {})
    return notifier


def _build_ai_client(
    model: AIModel | None, service: AIService | None, proxy: str
) -> AIClient:
    """根据解析后的 model+service 构建 AIClient"""
    if model and service:
        return AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
            proxy=proxy,
        )
    # 回退到环境变量配置
    settings = Settings()
    return AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        proxy=proxy,
    )


def build_context(agent_name: str, stock_agent_id: int | None = None) -> AgentContext:
    """为指定 Agent 构建运行上下文"""
    settings = Settings()
    watchlist = load_watchlist_for_agent(agent_name)
    portfolio = load_portfolio_for_agent(agent_name)
    proxy = _get_proxy() or settings.http_proxy

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    ai_client = _build_ai_client(model, service, proxy)
    channels = resolve_notify_channels(agent_name, stock_agent_id)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=watchlist)
    return AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        notify_policy=getattr(notifier, "policy", None),
    )


# Agent 注册表
AGENT_REGISTRY: dict[str, type] = {
    "daily_report": DailyReportAgent,
    "premarket_outlook": PremarketOutlookAgent,
    "news_digest": NewsDigestAgent,
    "chart_analyst": ChartAnalystAgent,
    "intraday_monitor": IntradayMonitorAgent,
}


def build_scheduler() -> AgentScheduler:
    """构建调度器并注册已启用的 Agent"""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # 设置 context 构建函数（每次执行时动态获取最新配置）
    sched.set_context_builder(build_context)

    db = SessionLocal()
    try:
        agent_configs = db.query(AgentConfig).filter(AgentConfig.enabled == True).all()
        for cfg in agent_configs:
            agent_cls = AGENT_REGISTRY.get(cfg.name)
            if not agent_cls:
                logger.warning(f"Agent {cfg.name} 未在 AGENT_REGISTRY 中注册")
                continue
            if not cfg.schedule:
                logger.info(f"Agent {cfg.name} 未设置调度计划，跳过")
                continue

            agent_kwargs = cfg.config or {}
            try:
                agent_instance = (
                    agent_cls(**agent_kwargs) if agent_kwargs else agent_cls()
                )
            except TypeError:
                agent_instance = agent_cls()
            sched.register(
                agent_instance,
                schedule=cfg.schedule,
                execution_mode=cfg.execution_mode or "batch",
            )
    finally:
        db.close()

    return sched


def reload_scheduler() -> bool:
    """重载调度器（用于配置导入/批量修改后立即生效）"""
    global scheduler
    try:
        current = globals().get("scheduler")
        if current:
            try:
                current.shutdown()
            except Exception:
                pass
        scheduler = build_scheduler()
        scheduler.start()
        logger.info("Agent 调度器已重载")
        return True
    except Exception as e:
        logger.error(f"Agent 调度器重载失败: {e}")
        return False


def _log_trigger_info(
    agent_name: str,
    stocks: list,
    model: AIModel | None,
    service: AIService | None,
    channels: list[NotifyChannel],
):
    """打印 Agent 触发时的上下文信息"""
    stock_names = ", ".join(
        f"{s.name}({s.symbol})" if hasattr(s, "symbol") else str(s) for s in stocks
    )
    ai_info = f"{service.name}/{model.model}" if model and service else "未配置"
    channel_info = ", ".join(ch.name for ch in channels) if channels else "无"
    logger.info(
        f"[触发] Agent={agent_name} | 股票=[{stock_names}] | AI={ai_info} | 通知=[{channel_info}]"
    )


def get_agent_execution_mode(agent_name: str) -> str:
    """获取 Agent 的执行模式"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.execution_mode if agent and agent.execution_mode else "batch"
    finally:
        db.close()


def get_agent_config(agent_name: str) -> dict:
    """获取 Agent 的配置参数"""
    db = SessionLocal()
    try:
        agent = db.query(AgentConfig).filter(AgentConfig.name == agent_name).first()
        return agent.config if agent and agent.config else {}
    finally:
        db.close()


async def trigger_agent(agent_name: str) -> str:
    """手动触发 Agent 执行（根据执行模式处理）"""
    start = time.monotonic()
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未注册实际实现")

    watchlist = load_watchlist_for_agent(agent_name)
    logger.info(
        f"[watchlist] Agent={agent_name} count={len(watchlist)} symbols={[s.symbol for s in watchlist]}"
    )
    if not watchlist:
        return f"Agent {agent_name} 没有关联的自选股"

    model, service = resolve_ai_model(agent_name)
    channels = resolve_notify_channels(agent_name)
    _log_trigger_info(agent_name, watchlist, model, service, channels)

    context = build_context(agent_name)
    execution_mode = get_agent_execution_mode(agent_name)
    agent_config = get_agent_config(agent_name)

    # 根据配置初始化 Agent
    if agent_config:
        agent = agent_cls(**agent_config)
    else:
        agent = agent_cls()

    try:
        if execution_mode == "single" and hasattr(agent, "run_single"):
            # 单只模式：逐只股票分析
            results = []
            for stock in watchlist:
                result = await agent.run_single(context, stock.symbol)
                if result:
                    results.append(f"{stock.name}: {result.content[:100]}...")
            msg = "\n\n".join(results) if results else "无异动"
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=msg,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            return msg
        else:
            # 批量模式：所有股票一起分析
            result = await agent.run(context)
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=result.content,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            return result.content
    except Exception as e:
        record_agent_run(
            agent_name=agent_name,
            status="failed",
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        raise


async def trigger_agent_for_stock(
    agent_name: str,
    stock,
    stock_agent_id: int | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    suppress_notify: bool = False,
) -> dict:
    """手动触发 Agent 执行（单只股票）"""
    start = time.monotonic()
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未注册实际实现")

    settings = Settings()
    proxy = _get_proxy() or settings.http_proxy

    try:
        market = MarketCode(stock.market)
    except ValueError:
        market = MarketCode.CN

    stock_config = StockConfig(
        symbol=stock.symbol,
        name=stock.name,
        market=market,
    )

    # 加载该股票的持仓信息
    portfolio = load_portfolio_for_stock(stock.id)

    model, service = resolve_ai_model(agent_name, stock_agent_id)
    channels = [] if suppress_notify else resolve_notify_channels(agent_name, stock_agent_id)
    _log_trigger_info(agent_name, [stock], model, service, channels)

    ai_client = _build_ai_client(model, service, proxy)
    notifier = _build_notifier(channels)

    model_label = f"{service.name}/{model.model}" if model and service else ""
    config = AppConfig(settings=settings, watchlist=[stock_config])
    context = AgentContext(
        ai_client=ai_client,
        notifier=notifier,
        config=config,
        portfolio=portfolio,
        model_label=model_label,
        suppress_notify=suppress_notify,
    )

    # 创建 agent，支持 intraday_monitor 的手动触发参数
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    else:
        agent = agent_cls()

    try:
        result = await agent.run(context)
        record_agent_run(
            agent_name=agent_name,
            status="success",
            result=result.content,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as e:
        record_agent_run(
            agent_name=agent_name,
            status="failed",
            error=str(e),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        raise

    # 返回详细结果
    skipped = bool(result.raw_data.get("skipped", False))
    should_alert = bool(
        result.raw_data.get("should_alert", False if skipped else True)
    )
    return {
        "code": 0 if not skipped else 1001001,
        "success": not skipped,
        "message": result.content if skipped else "ok",
        "title": result.title,
        "content": result.content,
        "should_alert": should_alert,
        "notified": result.raw_data.get("notified", False),
        "skipped": skipped,
    }


@asynccontextmanager
async def lifespan(app):
    """应用生命周期: 初始化 + 启动调度器"""
    init_db()
    setup_logging()
    setup_ssl()
    setup_playwright()

    # 从环境变量初始化认证（Docker 部署用）
    from src.web.api.auth import init_auth_from_env

    db = SessionLocal()
    try:
        if init_auth_from_env(db):
            logger.info("已从环境变量初始化认证账号")
    finally:
        db.close()

    seed_agents()
    seed_data_sources()
    seed_sample_stocks()

    # 后台刷新股票列表缓存
    import threading
    from src.web.stock_list import get_stock_list, refresh_stock_list

    def refresh_stock_cache():
        stocks = get_stock_list()
        if not stocks or len([s for s in stocks if s["market"] == "CN"]) == 0:
            logger.info("股票列表缓存为空或缺少 A 股，后台刷新中...")
            refresh_stock_list()

    threading.Thread(target=refresh_stock_cache, daemon=True).start()

    global scheduler, price_alert_scheduler
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Agent 调度器已启动")
    try:
        settings = Settings()
        price_alert_scheduler = PriceAlertScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        price_alert_scheduler.start()
        logger.info("价格提醒调度器已启动")
    except Exception as e:
        logger.error(f"价格提醒调度器启动失败: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent 调度器已关闭")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("价格提醒调度器已关闭")


# 模块级 app 实例，供 uvicorn reload 使用
from src.web.app import app  # noqa: E402

app.router.lifespan_context = lifespan

# 生产环境静态文件服务
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    # SPA 路由：所有非 API 请求返回 index.html
    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = os.path.join(static_dir, path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))

    logger.info(f"静态文件服务已启用: {static_dir}")


if __name__ == "__main__":
    print("盯盘侠启动: http://127.0.0.1:8000")
    print("API 文档: http://127.0.0.1:8000/docs")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["src", "."],
        reload_excludes=["data/*", "frontend/*", ".claude/*"],
    )
