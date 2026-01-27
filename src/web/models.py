from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, JSON, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.web.database import Base


class AIService(Base):
    """AI 服务商（base_url + api_key）"""
    __tablename__ = "ai_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "OpenAI", "智谱", "DeepSeek"
    base_url = Column(String, nullable=False)
    api_key = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    models = relationship("AIModel", back_populates="service", cascade="all, delete-orphan")


class AIModel(Base):
    """AI 模型（属于某个服务商）"""
    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # 显示名，如 "GLM-4-Flash"
    service_id = Column(Integer, ForeignKey("ai_services.id", ondelete="CASCADE"), nullable=False)
    model = Column(String, nullable=False)  # 实际模型标识，如 "glm-4-flash"
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())

    service = relationship("AIService", back_populates="models")


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "telegram"
    config = Column(JSON, default={})  # {"bot_token": "...", "chat_id": "..."}
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class Account(Base):
    """交易账户"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # 账户名称，如 "招商证券"、"华泰证券"
    available_funds = Column(Float, default=0)  # 可用资金
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    positions = relationship("Position", back_populates="account", cascade="all, delete-orphan")


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    name = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN / HK / US
    # 以下字段已废弃，持仓信息移至 Position 表
    cost_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    invested_amount = Column(Float, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    agents = relationship("StockAgent", back_populates="stock", cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="stock", cascade="all, delete-orphan")


class Position(Base):
    """持仓记录（多账户多股票）"""
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("account_id", "stock_id", name="uq_account_stock"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    cost_price = Column(Float, nullable=False)  # 成本价
    quantity = Column(Integer, nullable=False)  # 持仓数量
    invested_amount = Column(Float, nullable=True)  # 投入资金（用于盘中监控）
    trading_style = Column(String, default="swing")  # short: 短线, swing: 波段, long: 长线
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="positions")
    stock = relationship("Stock", back_populates="positions")


class StockAgent(Base):
    """多对多: 每只股票可被多个 Agent 监控"""
    __tablename__ = "stock_agents"
    __table_args__ = (UniqueConstraint("stock_id", "agent_name", name="uq_stock_agent"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False)
    agent_name = Column(String, nullable=False)
    schedule = Column(String, default="")
    ai_model_id = Column(Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True)
    notify_channel_ids = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="agents")


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, default="")
    enabled = Column(Boolean, default=True)
    schedule = Column(String, default="")
    # 执行模式: batch=批量(多只股票一起分析发送) / single=单只(逐只分析发送，实时性高)
    execution_mode = Column(String, default="batch")
    ai_model_id = Column(Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True)
    notify_channel_ids = Column(JSON, default=[])
    config = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # success / failed
    result = Column(String, default="")
    error = Column(String, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    level = Column(String, nullable=False)
    logger_name = Column(String, default="")
    message = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, default="")
    description = Column(String, default="")


class DataSource(Base):
    """数据源配置（新闻、K线图、行情）"""
    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)       # "雪球资讯"
    type = Column(String, nullable=False)       # "news" / "chart" / "quote" / "kline" / "capital_flow"
    provider = Column(String, nullable=False)   # "xueqiu" / "eastmoney" / "tencent"
    config = Column(JSON, default={})           # 配置参数
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)       # 越小优先级越高
    supports_batch = Column(Boolean, default=False)  # 是否支持批量查询
    test_symbols = Column(JSON, default=[])     # 测试用股票代码列表
    created_at = Column(DateTime, server_default=func.now())


class NewsCache(Base):
    """新闻缓存（用于去重）"""
    __tablename__ = "news_cache"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_news_source_external"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)      # "cls" / "eastmoney"
    external_id = Column(String, nullable=False) # 来源侧 ID
    title = Column(String, nullable=False)
    content = Column(String, default="")
    publish_time = Column(DateTime, nullable=False)
    symbols = Column(JSON, default=[])           # 关联股票代码列表
    importance = Column(Integer, default=0)      # 0-3 重要性
    created_at = Column(DateTime, server_default=func.now())


class NotifyThrottle(Base):
    """通知节流记录（防止同一股票短时间内重复通知）"""
    __tablename__ = "notify_throttle"
    __table_args__ = (UniqueConstraint("agent_name", "stock_symbol", name="uq_agent_stock_throttle"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    last_notify_at = Column(DateTime, nullable=False)
    notify_count = Column(Integer, default=1)  # 当日通知次数


class AnalysisHistory(Base):
    """分析历史记录（盘后分析、盘前分析等）"""
    __tablename__ = "analysis_history"
    __table_args__ = (
        UniqueConstraint("agent_name", "stock_symbol", "analysis_date", name="uq_agent_stock_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)     # "daily_report" / "premarket_outlook"
    stock_symbol = Column(String, nullable=False)   # 股票代码，"*" 表示全部
    analysis_date = Column(String, nullable=False)  # 分析日期 "YYYY-MM-DD"
    title = Column(String, default="")              # 分析标题
    content = Column(String, nullable=False)        # AI 分析结果
    raw_data = Column(JSON, default={})             # 原始数据快照
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StockSuggestion(Base):
    """股票建议池 - 汇总各 Agent 建议"""
    __tablename__ = "stock_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False, index=True)
    stock_name = Column(String, default="")

    # 建议内容
    action = Column(String, nullable=False)       # buy/add/reduce/sell/hold/watch/alert/avoid
    action_label = Column(String, nullable=False)  # 中文标签：建仓/加仓/减仓/清仓/持有/观望
    signal = Column(String, default="")            # 信号描述
    reason = Column(String, default="")            # 建议理由

    # 来源追踪
    agent_name = Column(String, nullable=False)    # intraday_monitor/daily_report/premarket_outlook
    agent_label = Column(String, default="")       # 盘中监测/盘后日报/盘前分析

    # 上下文信息
    prompt_context = Column(String, default="")    # Prompt 上下文摘要
    ai_response = Column(String, default="")       # AI 原始响应

    # 时间信息
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)   # 建议过期时间

    # 索引：按股票+时间快速查询
    __table_args__ = (
        Index('ix_suggestion_symbol_time', 'stock_symbol', 'created_at'),
    )
