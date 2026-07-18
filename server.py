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
from src.core.paper_trading_scheduler import PaperTradingScheduler
from src.core.context_scheduler import ContextMaintenanceScheduler
from src.core.agent_runs import record_agent_run
from src.core.log_context import install_log_record_factory, log_context
from src.core.agent_catalog import (
    AGENT_SEED_SPECS,
    AGENT_KIND_WORKFLOW,
)
from src.core.strategy_catalog import ensure_strategy_catalog
from src.agents.base import AgentContext, PortfolioInfo, AccountInfo, PositionInfo
from src.agents.daily_report import DailyReportAgent
from src.agents.news_digest import NewsDigestAgent
from src.agents.chart_analyst import ChartAnalystAgent
from src.agents.intraday_monitor import IntradayMonitorAgent
from src.agents.premarket_outlook import PremarketOutlookAgent
from src.agents.tradingagents import TradingAgentsAgent

logger = logging.getLogger(__name__)

# 全局 scheduler 实例，供 agents API 调用
scheduler: AgentScheduler | None = None
price_alert_scheduler: PriceAlertScheduler | None = None
paper_trading_scheduler: PaperTradingScheduler | None = None
context_maintenance_scheduler: ContextMaintenanceScheduler | None = None


def apply_proxy_env(proxy: str | None) -> None:
    """统一更新进程环境变量代理,让所有 httpx 默认 Client (trust_env=True) 走该代理。

    传空字符串 / None 时清除环境变量(取消代理)。
    NO_PROXY 默认含 localhost / 回环地址,避免本地访问绕一圈。
    """
    p = (proxy or "").strip()
    if p:
        os.environ["HTTP_PROXY"] = p
        os.environ["HTTPS_PROXY"] = p
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        logger.info(f"HTTP/HTTPS 代理已应用: {p}")
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            os.environ.pop(key, None)
        logger.info("HTTP/HTTPS 代理已清除")


def setup_proxy():
    """启动时把已配置的 HTTP 代理桥接到环境变量。

    优先级:
    1. 已存在的 HTTP_PROXY / HTTPS_PROXY 环境变量(用户显式覆盖,不动)
    2. app_settings.http_proxy(UI 配置)
    3. .env 中的 http_proxy(Settings.http_proxy)
    """
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        logger.info(
            f"沿用现有环境变量代理: HTTP_PROXY={os.environ.get('HTTP_PROXY', '')} "
            f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY', '')}"
        )
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,0.0.0.0")
        return

    proxy = ""
    try:
        db = SessionLocal()
        try:
            setting = (
                db.query(AppSettings).filter(AppSettings.key == "http_proxy").first()
            )
            if setting and setting.value:
                proxy = setting.value.strip()
        finally:
            db.close()
    except Exception:
        pass

    if not proxy:
        proxy = (Settings().http_proxy or "").strip()

    if proxy:
        apply_proxy_env(proxy)


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
    """配置日志: 控制台 + 数据库

    分级策略:
    - root logger 始终 DEBUG,所有日志都会传播到 handler
    - 控制台 handler 按 LOG_LEVEL 过滤(默认 INFO),并丢弃 httpx 等三方库的 < WARNING 噪音
    - DB handler 始终 DEBUG 全量收录,UI 日志板永远可以看到包括心跳/httpx 请求在内的完整记录
    """
    console_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    console_level = getattr(logging, console_level_name, logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    install_log_record_factory()

    # reload/server restart 时避免重复 handler 导致日志放大。
    for h in list(root.handlers):
        if isinstance(h, DBLogHandler) or getattr(h, "_panwatch_console", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    # 控制台输出: 按 LOG_LEVEL 过滤,且丢弃三方库的低级别噪音
    console = logging.StreamHandler()
    console._panwatch_console = True  # type: ignore[attr-defined]
    console.setLevel(console_level)
    console.addFilter(_ConsoleNoiseFilter())
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(name)s] %(message)s", datefmt="%H:%M:%S"
        )
    )
    root.addHandler(console)

    # 数据库持久化: 始终全量收录,UI 日志板可查 DEBUG
    db_handler = DBLogHandler(level=logging.DEBUG)
    db_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(db_handler)

    # uvicorn 默认给自己挂了 stderr handler 并且 propagate=False,导致 access log
    # 走自己的链路(`INFO: 127.0.0.1 - "GET /api/..."`)不被我们的 filter 拦截。
    # 改成清空自己的 handler + propagate 到 root,让 _ConsoleNoiseFilter 生效。
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.DEBUG)


class _ConsoleNoiseFilter(logging.Filter):
    """控制台 handler 过滤器: 三方库的 INFO/DEBUG 不进 stdout,WARNING+ 仍然显示。
    DB handler 不挂这个过滤器,UI 日志板能看到完整请求记录。

    uvicorn.access 是每条请求的 access log(`INFO: 127.0.0.1 - "GET /api/..." 200 OK`),
    属于底层心跳;uvicorn / uvicorn.error 是应用级日志(启动、报错),保留。"""

    _NOISY_PREFIXES = ("httpx", "httpcore", "urllib3", "apscheduler", "uvicorn.access")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        name = record.name or ""
        for prefix in self._NOISY_PREFIXES:
            if name == prefix or name.startswith(prefix + "."):
                return False
        return True


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
    for spec in AGENT_SEED_SPECS:
        existing = db.query(AgentConfig).filter(AgentConfig.name == spec.name).first()
        if not existing:
            db.add(
                AgentConfig(
                    name=spec.name,
                    display_name=spec.display_name,
                    description=spec.description,
                    kind=spec.kind,
                    visible=spec.visible,
                    lifecycle_status=spec.lifecycle_status,
                    replaced_by=spec.replaced_by,
                    display_order=spec.display_order,
                    enabled=spec.enabled,
                    schedule=spec.schedule,
                    execution_mode=spec.execution_mode,
                    config=spec.config or {},
                )
            )
        else:
            # 始终同步 execution_mode（确保代码中的定义生效）
            existing.execution_mode = spec.execution_mode or "batch"
            # 同步 display_name 和 description
            existing.display_name = spec.display_name or existing.display_name
            existing.description = spec.description or existing.description
            existing.kind = spec.kind
            existing.visible = bool(spec.visible)
            existing.lifecycle_status = spec.lifecycle_status or "active"
            existing.replaced_by = spec.replaced_by or ""
            existing.display_order = int(spec.display_order or 0)

            # capability 强制不参与调度，避免旧配置继续触发。
            if spec.kind != AGENT_KIND_WORKFLOW:
                existing.enabled = False
                existing.schedule = ""

            # 仅在用户未配置时补齐默认 config
            if spec.config and (not existing.config):
                existing.config = spec.config
            # 对已存在配置做“向前兼容”的字段补齐（不覆盖用户已有值）
            if existing.name == "intraday_monitor":
                cfg = existing.config or {}
                if isinstance(cfg, dict) and "event_only" not in cfg:
                    cfg["event_only"] = True
                    existing.config = cfg

    db.commit()
    db.close()


# 预置数据源种子(供 seed_data_sources / reconcile_data_sources 复用)。
# 只增不删的 upsert 目标;删孤儿的对账逻辑见 reconcile_data_sources。
DATA_SOURCE_SEEDS: list[dict] = [
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
        {
            "name": "东方财富 K线",
            "type": "kline",
            "provider": "eastmoney",
            "config": {"description": "东方财富日线,A股/港股长历史兜底(免 key)。"},
            "enabled": True,
            "priority": 5,   # 腾讯(0)之后、Tushare(10)之前 → CN/HK 兜底
            "supports_batch": False,
            "test_symbols": ["600519", "00700"],
        },
        {
            "name": "Stooq K线",
            "type": "kline",
            "provider": "stooq",
            "config": {"description": "Stooq 美股日线兜底(免 key)。"},
            "enabled": True,
            "priority": 15,  # US 兜底(腾讯 0 之后)
            "supports_batch": False,
            "test_symbols": ["AAPL"],
        },
        {
            "name": "Yahoo K线",
            "type": "kline",
            "provider": "yahoo",
            "config": {
                "description": "Yahoo chart v8 日线(US/HK,免 key 免 crumb)。国内访问通常需代理,"
                "在 config.proxy 填写代理地址后启用,作港股 K线第二源/美股更稳兜底。",
                "proxy": "",
            },
            "enabled": False,  # 需代理,默认关(同 YFinance 口径),用户配好 proxy 再开
            "priority": 20,  # US/HK 最后兜底
            "supports_batch": False,
            "test_symbols": ["AAPL", "00700"],
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
        {
            "name": "新浪资金流",
            "type": "capital_flow",
            "provider": "sina",
            "config": {
                "description": "新浪资金流入趋势(CN,免 key)。作东财之后的第二源,"
                "仅含主力/超大单净额(无大/中/小单细分)。",
            },
            "enabled": True,
            "priority": 5,  # 东财(0)之后的 CN 第二源
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
        {
            "name": "东方财富行情",
            "type": "quote",
            "provider": "eastmoney",
            "config": {"description": "东方财富 push2 实时行情(CN,免 key)。作腾讯之后的 A 股第二源。"},
            "enabled": True,
            "priority": 3,  # 腾讯(0)之后的 CN 第二源(sina/yfinance 不支持 CN)
            "supports_batch": False,  # push2 stock/get 单只查询,逐只
            "test_symbols": ["601127", "600519", "300750"],
        },
        {
            "name": "Sina 行情",
            "type": "quote",
            "provider": "sina",
            "config": {"description": "新浪美股/港股实时行情,免 key 免代理,作腾讯之后的 US/HK 备源。"},
            "enabled": True,
            "priority": 5,   # 腾讯(0)之后
            "supports_batch": True,
            "test_symbols": ["AAPL", "00700"],
        },
        {
            "name": "YFinance 行情",
            "type": "quote",
            "provider": "yfinance",
            "config": {
                "description": "Yahoo Finance,需 pip install yfinance。适用 HK/US,A 股不可用。",
            },
            "enabled": False,
            "priority": 10,
            "supports_batch": True,
            "test_symbols": ["AAPL"],
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
        # 快讯数据源（7×24 电报，市场级，不按 symbols 过滤）
        {
            "name": "财联社快讯",
            "type": "flash_news",
            "provider": "cls",
            "config": {"description": "财联社 7×24 电报(免 key,本地签名)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "新浪7x24快讯",
            "type": "flash_news",
            "provider": "sina",
            "config": {"description": "新浪财经 7×24 直播,带关联个股。"},
            "enabled": True,
            "priority": 5,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "东方财富7x24快讯",
            "type": "flash_news",
            "provider": "eastmoney",
            "config": {"description": "东财 np-weblist 7×24 资讯,与财联社互备。"},
            "enabled": True,
            "priority": 10,
            "supports_batch": False,
            "test_symbols": [],
        },
        # 基本面数据源（按 symbol，估值/股本/财报指标）
        {
            "name": "腾讯基本面",
            "type": "fundamentals",
            "provider": "tencent",
            "config": {"description": "腾讯 qt.gtimg 估值快照(CN,免 key):PE/PB/市值。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["600519", "000001"],
        },
        {
            "name": "东方财富基本面",
            "type": "fundamentals",
            "provider": "eastmoney",
            "config": {
                "description": "东财基本面:CN 股本/市值(push2),US/HK 财报指标(GMAININDICATOR)。"
            },
            "enabled": True,
            "priority": 5,
            "supports_batch": True,
            "test_symbols": ["600519", "AAPL"],
        },
        # 市场资金面数据源（龙虎榜/融资融券/股东户数/分红/北向资金）
        {
            "name": "东财龙虎榜",
            "type": "dragon_tiger",
            "provider": "eastmoney",
            "config": {
                "description": "东财每日龙虎榜(市场级,需配 test_date 测试)。",
                "test_date": "",
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
        },
        {
            "name": "东财融资融券",
            "type": "margin",
            "provider": "eastmoney",
            "config": {"description": "东财个股融资融券明细(按 symbol)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["600519", "000001"],
        },
        {
            "name": "东财股东户数",
            "type": "shareholders",
            "provider": "eastmoney",
            "config": {"description": "东财股东户数变化(按 symbol,季度)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["600519", "000001"],
        },
        {
            "name": "东财分红",
            "type": "dividend",
            "provider": "eastmoney",
            "config": {"description": "东财分红送转历史(按 symbol)。"},
            "enabled": True,
            "priority": 0,
            "supports_batch": True,
            "test_symbols": ["600519", "000001"],
        },
        {
            "name": "同花顺北向资金",
            "type": "northbound",
            "provider": "ths",
            "config": {
                "description": "同花顺北向资金实时(东财已断供;深股通近期不可靠)。"
            },
            "enabled": True,
            "priority": 0,
            "supports_batch": False,
            "test_symbols": [],
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


def seed_data_sources(db=None) -> list[dict]:
    """初始化预置数据源(按 name+provider 只增不删的 upsert)。

    db 为 None 时自建独立 session 并自行 commit/close(兼容旧调用方式);
    传入 db 时复用调用方 session,不 commit/close,交由调用方统一处理
    (供 reconcile_data_sources 在同一事务里接着做删孤儿)。

    返回本次新增(缺失被补齐)的种子记录摘要列表 [{"name","type","provider"}, ...]。
    """
    owns_session = db is None
    if owns_session:
        db = SessionLocal()

    seeded_missing: list[dict] = []
    for source_data in DATA_SOURCE_SEEDS:
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
            seeded_missing.append(
                {
                    "name": source_data["name"],
                    "type": source_data["type"],
                    "provider": source_data["provider"],
                }
            )

    if owns_session:
        db.commit()
        db.close()

    return seeded_missing


def _seed_providers_by_type() -> dict[str, set[str]]:
    """从 DATA_SOURCE_SEEDS 推导每个 type 当前合法的 provider 集合。"""
    result: dict[str, set[str]] = {}
    for source_data in DATA_SOURCE_SEEDS:
        result.setdefault(source_data["type"], set()).add(source_data["provider"])
    return result


def reconcile_data_sources(db) -> dict:
    """数据源表温和对账:补缺失默认 + 删孤儿,保留用户有效自定义/凭证。

    孤儿判定: legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 内该 type 的 provider 集合;
    DB 行 (type, provider) 不在 legal(type) 内即孤儿。news/chart 等非引擎类型(包内集合为空)的合法性完全由 seed 决定。

    只删孤儿行,其余行(含用户改过 config/priority/enabled 的自定义行)原样保留。
    """
    from marketdata import PACKAGE_VENDORS_BY_TYPE

    seeded_missing = seed_data_sources(db)
    seed_providers_by_type = _seed_providers_by_type()

    deleted: list[dict] = []
    for row in db.query(DataSource).all():
        legal = PACKAGE_VENDORS_BY_TYPE.get(row.type, frozenset()) | seed_providers_by_type.get(row.type, set())
        if row.provider not in legal:
            deleted.append(
                {"id": row.id, "type": row.type, "provider": row.provider, "name": row.name}
            )
            db.delete(row)

    if seeded_missing:
        logger.info(f"数据源对账: 补齐缺失默认 {len(seeded_missing)} 条: {seeded_missing}")
    if deleted:
        logger.info(f"数据源对账: 删除孤儿数据源 {len(deleted)} 条: {deleted}")

    db.commit()
    return {"deleted": deleted, "seeded_missing": seeded_missing}


def seed_strategies():
    """初始化策略目录。"""
    ensure_strategy_catalog()
    logger.info("策略目录初始化完成")


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
    "tradingagents": TradingAgentsAgent,
}


def build_scheduler() -> AgentScheduler:
    """构建调度器并注册已启用的 Agent"""
    settings = Settings()
    sched = AgentScheduler(timezone=settings.app_timezone)

    # 设置 context 构建函数（每次执行时动态获取最新配置）
    sched.set_context_builder(build_context)

    db = SessionLocal()
    try:
        agent_configs = (
            db.query(AgentConfig)
            .filter(
                AgentConfig.enabled == True,
                AgentConfig.kind == AGENT_KIND_WORKFLOW,
            )
            .all()
        )
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
    trace_id = f"man-{agent_name}-{int(time.time() * 1000)}"
    agent_cls = AGENT_REGISTRY.get(agent_name)
    if not agent_cls:
        raise ValueError(f"Agent {agent_name} 未注册实际实现")

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent",
        tags={"trigger_source": "manual"},
    ):
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
                    trace_id=trace_id,
                    trigger_source="manual",
                    model_label=context.model_label,
                )
                return msg
            else:
                # 批量模式：所有股票一起分析
                result = await agent.run(context)
                raw = result.raw_data or {}
                record_agent_run(
                    agent_name=agent_name,
                    status="success",
                    result=result.content,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    trace_id=trace_id,
                    trigger_source="manual",
                    notify_attempted=(
                        "notified" in raw
                        or "notify_error" in raw
                        or "notify_skipped" in raw
                    ),
                    notify_sent=bool(raw.get("notified", False)),
                    model_label=context.model_label,
                )
                return result.content
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=context.model_label,
            )
            raise


async def trigger_agent_for_stock(
    agent_name: str,
    stock,
    stock_agent_id: int | None = None,
    bypass_throttle: bool = False,
    bypass_market_hours: bool = False,
    suppress_notify: bool = False,
    trace_id: str | None = None,
    force_refresh: bool = False,
) -> dict:
    """手动触发 Agent 执行（单只股票）"""
    start = time.monotonic()
    trace_id = trace_id or f"man-{agent_name}-{stock.symbol}-{int(time.time() * 1000)}"
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
    # 暴露 trace_id / force_refresh 给 agent(供 TradingAgents 进度反馈 + 缓存控制使用)。
    # AgentContext 不强制声明此字段,通过 setattr 注入,其他 agent 不受影响。
    setattr(context, "_trace_id", trace_id)
    setattr(context, "_force_refresh", force_refresh)

    # 创建 agent，支持手动触发参数。TradingAgents 等新 agent 从 AgentConfig 读 config。
    if agent_name == "intraday_monitor":
        agent = agent_cls(
            bypass_throttle=bypass_throttle,
            bypass_market_hours=bypass_market_hours,
        )
    elif agent_name == "tradingagents":
        # 从 AgentConfig.config 读取实例化参数
        agent_kwargs = get_agent_config(agent_name) or {}
        try:
            agent = agent_cls(**agent_kwargs)
        except TypeError:
            agent = agent_cls()
    else:
        agent = agent_cls()

    with log_context(
        trace_id=trace_id,
        run_id=trace_id,
        agent_name=agent_name,
        event="trigger_agent_for_stock",
        tags={"trigger_source": "manual", "stock_symbol": stock.symbol},
    ):
        try:
            result = await agent.run(context)
            raw = result.raw_data or {}
            record_agent_run(
                agent_name=agent_name,
                status="success",
                result=result.content,
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                notify_attempted=(
                    "notified" in raw
                    or "notify_error" in raw
                    or "notify_skipped" in raw
                ),
                notify_sent=bool(raw.get("notified", False)),
                model_label=model_label,
            )
        except Exception as e:
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=int((time.monotonic() - start) * 1000),
                trace_id=trace_id,
                trigger_source="manual",
                model_label=model_label,
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
    setup_proxy()  # 设置进程 env 代理(HTTP_PROXY/NO_PROXY);所有 httpx(trust_env=True)据此走代理
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
    try:
        db = SessionLocal()
        try:
            reconcile_data_sources(db)
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"数据源对账失败,跳过(不阻断启动): {e}")
    seed_strategies()
    seed_sample_stocks()

    # 启动时回填历史 TradingAgents 决策到建议池(stock_suggestions)
    # 早期 TA 运行没写建议池,这次启动一次性补齐,让「AI 建议」面板能看到。
    # 幂等:已存在不重复写;每次启动重跑代价极低(只查最近 7 天 + dedupe)。
    try:
        from src.agents.tradingagents.backfill import backfill_tradingagents_suggestions
        backfill_tradingagents_suggestions(days=7)
    except Exception as e:
        logger.warning(f"TradingAgents 建议回填失败,跳过: {e}")

    # 后台刷新股票列表缓存
    import threading
    from src.web.stock_list import get_stock_list, refresh_stock_list

    def refresh_stock_cache():
        stocks = get_stock_list()
        if not stocks or len([s for s in stocks if s["market"] == "CN"]) == 0:
            logger.info("股票列表缓存为空或缺少 A 股，后台刷新中...")
            refresh_stock_list()

    threading.Thread(target=refresh_stock_cache, daemon=True).start()

    global scheduler, price_alert_scheduler, paper_trading_scheduler, context_maintenance_scheduler
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
    try:
        settings = Settings()
        paper_trading_scheduler = PaperTradingScheduler(
            timezone=settings.app_timezone,
            interval_seconds=60,
        )
        paper_trading_scheduler.start()
        logger.info("模拟盘调度器已启动")
    except Exception as e:
        logger.error(f"模拟盘调度器启动失败: {e}")
    try:
        settings = Settings()
        context_maintenance_scheduler = ContextMaintenanceScheduler(
            timezone=settings.app_timezone,
            eval_interval_hours=6,
            snapshot_retention_days=180,
            outcome_retention_days=365,
        )
        context_maintenance_scheduler.start()
        logger.info("上下文维护调度器已启动")
    except Exception as e:
        logger.error(f"上下文维护调度器启动失败: {e}")
    yield
    if scheduler:
        scheduler.shutdown()
        logger.info("Agent 调度器已关闭")
    if price_alert_scheduler:
        price_alert_scheduler.shutdown()
        logger.info("价格提醒调度器已关闭")
    if paper_trading_scheduler:
        paper_trading_scheduler.shutdown()
        logger.info("模拟盘调度器已关闭")
    if context_maintenance_scheduler:
        context_maintenance_scheduler.shutdown()
        logger.info("上下文维护调度器已关闭")


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
    # 生产(Docker `python server.py`)不应开 reload:uvicorn 文件监听会多起一个 reloader
    # 子进程、浪费资源,且监听 data/ 写入易误触发重启。本地热重载用 `make dev-api`
    # (uvicorn --reload),或显式设 DEV_RELOAD=1。
    _dev_reload = os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=_dev_reload,
        reload_dirs=["src", "."] if _dev_reload else None,
        reload_excludes=["data/*", "frontend/*", ".claude/*"] if _dev_reload else None,
    )
