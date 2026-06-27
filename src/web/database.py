import json
import logging
import os
import shutil
from datetime import datetime
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import NullPool

from src.web.migrations import has_pending_migrations, run_versioned_migrations

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "panwatch.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={
        "timeout": 30,
        "check_same_thread": False,
    },
    poolclass=NullPool,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate(engine)
    _migrate_old_providers(engine)
    _migrate_settings_to_models(engine)
    _migrate_positions_to_accounts(engine)
    _migrate_remove_stock_enabled(engine)
    if has_pending_migrations(engine):
        _backup_db_before_migration()
    run_versioned_migrations(engine)


def _has_column(conn, table: str, column: str) -> bool:
    try:
        conn.execute(text(f"SELECT {column} FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _has_table(conn, table: str) -> bool:
    try:
        conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))
        return True
    except Exception:
        return False


def _drop_dangling_ai_provider_fk(conn, table: str) -> None:
    """删掉指向已不存在的 ai_providers 表的悬空外键列。

    背景: _migrate_old_providers 把 ai_providers 表删了,但
    agent_configs.ai_provider_id / stock_agents.ai_provider_id 这两列上的 FK 没清。
    SQLite 默认 PRAGMA foreign_keys 不开,所以历史 INSERT 没事;但某些路径下
    (比如 INSERT ... RETURNING + SQLAlchemy 校验)会报 "no such table: ai_providers"。

    SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN,直接 drop 即可。
    """
    if not _has_column(conn, table, "ai_provider_id"):
        return
    # ai_providers 还存在的话先不动(让 _migrate_old_providers 先迁移)
    if _has_table(conn, "ai_providers"):
        return
    try:
        conn.execute(text(f"ALTER TABLE {table} DROP COLUMN ai_provider_id"))
        conn.commit()
        logger.info(f"已清理 {table}.ai_provider_id 悬空外键列")
    except Exception as e:
        # 老 SQLite 不支持 DROP COLUMN — fallback 留 schema 不动,改用 PRAGMA foreign_keys=OFF
        # (本进程级别,不影响其他业务,因为 ai_providers 不存在,FK 永远无效)
        logger.warning(
            f"DROP COLUMN {table}.ai_provider_id 失败 (SQLite < 3.35?): {e}; "
            f"将通过 PRAGMA foreign_keys=OFF 绕开"
        )
        try:
            conn.execute(text("PRAGMA foreign_keys = OFF"))
            conn.commit()
        except Exception:
            pass


def _backup_db_before_migration() -> None:
    """Create a timestamped sqlite backup before versioned migrations."""
    if not os.path.exists(DB_PATH):
        return
    try:
        size = os.path.getsize(DB_PATH)
        if size <= 0:
            return
    except Exception:
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{DB_PATH}.bak.{ts}"
    try:
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"数据库迁移前备份已创建: {backup_path}")
    except Exception as e:
        logger.warning(f"数据库迁移前备份失败: {e}")


def _migrate(engine):
    """增量 schema 迁移（SQLite ALTER TABLE ADD COLUMN）"""
    migrations = [
        # Phase 1(模拟盘求真):持仓期最高价,移动止损用
        (
            "paper_trading_positions",
            "highest_price",
            "ALTER TABLE paper_trading_positions ADD COLUMN highest_price REAL",
        ),
        (
            "stock_agents",
            "schedule",
            "ALTER TABLE stock_agents ADD COLUMN schedule TEXT DEFAULT ''",
        ),
        (
            "agent_configs",
            "ai_model_id",
            "ALTER TABLE agent_configs ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "agent_configs",
            "notify_channel_ids",
            "ALTER TABLE agent_configs ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        (
            "stock_agents",
            "ai_model_id",
            "ALTER TABLE stock_agents ADD COLUMN ai_model_id INTEGER REFERENCES ai_models(id) ON DELETE SET NULL",
        ),
        (
            "stock_agents",
            "notify_channel_ids",
            "ALTER TABLE stock_agents ADD COLUMN notify_channel_ids TEXT DEFAULT '[]'",
        ),
        # Phase 3: 持仓增强
        (
            "stocks",
            "invested_amount",
            "ALTER TABLE stocks ADD COLUMN invested_amount REAL",
        ),
        # Phase 4: Agent 执行模式
        (
            "agent_configs",
            "execution_mode",
            "ALTER TABLE agent_configs ADD COLUMN execution_mode TEXT DEFAULT 'batch'",
        ),
        # Phase 4: 持仓交易风格
        (
            "positions",
            "trading_style",
            "ALTER TABLE positions ADD COLUMN trading_style TEXT DEFAULT 'short'",
        ),
        # 排序字段：关注列表/持仓拖拽排序
        (
            "stocks",
            "sort_order",
            "ALTER TABLE stocks ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        (
            "positions",
            "sort_order",
            "ALTER TABLE positions ADD COLUMN sort_order INTEGER DEFAULT 0",
        ),
        # 数据源增强
        (
            "data_sources",
            "supports_batch",
            "ALTER TABLE data_sources ADD COLUMN supports_batch INTEGER DEFAULT 0",
        ),
        (
            "data_sources",
            "test_symbols",
            "ALTER TABLE data_sources ADD COLUMN test_symbols TEXT DEFAULT '[]'",
        ),
        # Phase 5: 建议池元数据
        (
            "stock_suggestions",
            "meta",
            "ALTER TABLE stock_suggestions ADD COLUMN meta TEXT DEFAULT '{}'",
        ),
        (
            "stocks",
            "concept_tags_auto",
            "ALTER TABLE stocks ADD COLUMN concept_tags_auto TEXT DEFAULT '[]'",
        ),
        (
            "stocks",
            "concept_tags_manual",
            "ALTER TABLE stocks ADD COLUMN concept_tags_manual TEXT DEFAULT '[]'",
        ),
        (
            "stocks",
            "concept_tags_updated_at",
            "ALTER TABLE stocks ADD COLUMN concept_tags_updated_at DATETIME",
        ),
        (
            "stocks",
            "is_featured",
            "ALTER TABLE stocks ADD COLUMN is_featured INTEGER DEFAULT 0",
        ),
        (
            "stocks",
            "industry_chain_auto",
            "ALTER TABLE stocks ADD COLUMN industry_chain_auto TEXT DEFAULT '{}'",
        ),
        (
            "stocks",
            "industry_chain_updated_at",
            "ALTER TABLE stocks ADD COLUMN industry_chain_updated_at DATETIME",
        ),
        (
            "stocks",
            "industry_chain_manual",
            "ALTER TABLE stocks ADD COLUMN industry_chain_manual TEXT DEFAULT '{}'",
        ),
        (
            "accounts",
            "other_funds",
            "ALTER TABLE accounts ADD COLUMN other_funds REAL DEFAULT 0",
        ),
        (
            "accounts",
            "base_currency",
            "ALTER TABLE accounts ADD COLUMN base_currency TEXT DEFAULT 'CNY'",
        ),
        (
            "accounts",
            "initial_funds",
            "ALTER TABLE accounts ADD COLUMN initial_funds REAL DEFAULT 0",
        ),
        (
            "accounts",
            "other_fund_items",
            "ALTER TABLE accounts ADD COLUMN other_fund_items TEXT DEFAULT '[]'",
        ),
    ]
    with engine.connect() as conn:
        for table, column, sql in migrations:
            if not _has_column(conn, table, column):
                conn.execute(text(sql))
                conn.commit()

        # 清理 legacy 悬空外键:agent_configs.ai_provider_id / stock_agents.ai_provider_id
        # 这两列原本 REFERENCES ai_providers(id),但 _migrate_old_providers 已经把
        # ai_providers 表删了。如果保留 FK,新 INSERT 会触发 SQLite "no such table" 错误
        # (因为 SQLite 在 INSERT 时校验 FK 引用的表是否存在)。
        _drop_dangling_ai_provider_fk(conn, "agent_configs")
        _drop_dangling_ai_provider_fk(conn, "stock_agents")

        if _has_column(conn, "accounts", "other_fund_items") and _has_column(
            conn, "accounts", "other_funds"
        ):
            import json

            rows = conn.execute(
                text(
                    "SELECT id, other_funds, other_fund_items FROM accounts "
                    "WHERE other_funds IS NOT NULL AND other_funds > 0"
                )
            ).fetchall()
            for row in rows:
                raw_items = row[2]
                if raw_items and raw_items not in ("[]", "null", None):
                    continue
                items = json.dumps(
                    [{"label": "其他", "amount": float(row[1] or 0)}],
                    ensure_ascii=False,
                )
                conn.execute(
                    text("UPDATE accounts SET other_fund_items = :items WHERE id = :id"),
                    {"items": items, "id": row[0]},
                )
            conn.commit()

        # 初始化排序字段（仅对未初始化数据）
        if _has_column(conn, "stocks", "sort_order"):
            conn.execute(text("UPDATE stocks SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"))
            conn.commit()
        if _has_column(conn, "positions", "sort_order"):
            conn.execute(text("UPDATE positions SET sort_order = id WHERE sort_order IS NULL OR sort_order = 0"))
            conn.commit()

        # Create new tables if missing (SQLite)
        if not _has_table(conn, "suggestion_feedback"):
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS suggestion_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  suggestion_id INTEGER NOT NULL REFERENCES stock_suggestions(id) ON DELETE CASCADE,
  useful INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_suggestion_id ON suggestion_feedback(suggestion_id);"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_feedback_created_at ON suggestion_feedback(created_at);"
                )
            )
            conn.commit()


def _migrate_old_providers(engine):
    """如果存在旧的 ai_providers 表，迁移数据到 ai_services + ai_models"""
    with engine.connect() as conn:
        if not _has_table(conn, "ai_providers"):
            return
        # Check if it has the old schema (has base_url column)
        if not _has_column(conn, "ai_providers", "base_url"):
            return

        rows = conn.execute(
            text(
                "SELECT id, name, base_url, api_key, model, is_default FROM ai_providers"
            )
        ).fetchall()
        if not rows:
            conn.execute(text("DROP TABLE IF EXISTS ai_providers"))
            conn.commit()
            return

        # Group by base_url+api_key to create services
        service_map = {}  # (base_url, api_key) -> service_id
        for row in rows:
            old_id, name, base_url, api_key, model, is_default = row
            key = (base_url, api_key)
            if key not in service_map:
                # Create service
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": name, "base_url": base_url, "api_key": api_key},
                )
                result = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                service_map[key] = result

            service_id = service_map[key]
            conn.execute(
                text(
                    "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, :is_default)"
                ),
                {
                    "name": name,
                    "service_id": service_id,
                    "model": model,
                    "is_default": is_default,
                },
            )
            new_model_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

            # Update references: agent_configs.ai_provider_id → ai_model_id
            if _has_column(conn, "agent_configs", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE agent_configs SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )
            # stock_agents.ai_provider_id → ai_model_id
            if _has_column(conn, "stock_agents", "ai_provider_id"):
                conn.execute(
                    text(
                        "UPDATE stock_agents SET ai_model_id = :new_id WHERE ai_provider_id = :old_id"
                    ),
                    {"new_id": new_model_id, "old_id": old_id},
                )

        conn.execute(text("DROP TABLE ai_providers"))
        conn.commit()
        logger.info(
            f"已迁移 {len(rows)} 条旧 AI Provider 数据到 ai_services + ai_models"
        )


def _migrate_settings_to_models(engine):
    """将旧的 app_settings 中的 AI/通知配置迁移为 AIService+AIModel / NotifyChannel 记录"""
    with engine.connect() as conn:
        if not _has_table(conn, "app_settings"):
            return

        rows = conn.execute(text("SELECT key, value FROM app_settings")).fetchall()
        settings_map = {row[0]: row[1] for row in rows}

        ai_base_url = settings_map.get("ai_base_url", "")
        ai_api_key = settings_map.get("ai_api_key", "")
        ai_model = settings_map.get("ai_model", "")

        # Migrate AI settings if present and no services exist yet
        if ai_base_url and ai_model:
            existing = conn.execute(text("SELECT COUNT(*) FROM ai_services")).scalar()
            if existing == 0:
                conn.execute(
                    text(
                        "INSERT INTO ai_services (name, base_url, api_key) VALUES (:name, :base_url, :api_key)"
                    ),
                    {"name": ai_model, "base_url": ai_base_url, "api_key": ai_api_key},
                )
                service_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
                conn.execute(
                    text(
                        "INSERT INTO ai_models (name, service_id, model, is_default) VALUES (:name, :service_id, :model, 1)"
                    ),
                    {"name": ai_model, "service_id": service_id, "model": ai_model},
                )
                logger.info(f"已迁移 AI 配置: {ai_model}")

        # Migrate Telegram settings if present and no channels exist yet
        bot_token = settings_map.get("notify_telegram_bot_token", "")
        chat_id = settings_map.get("notify_telegram_chat_id", "")

        if bot_token:
            existing = conn.execute(
                text("SELECT COUNT(*) FROM notify_channels")
            ).scalar()
            if existing == 0:
                config_json = json.dumps({"bot_token": bot_token, "chat_id": chat_id})
                conn.execute(
                    text(
                        "INSERT INTO notify_channels (name, type, config, enabled, is_default) VALUES (:name, :type, :config, 1, 1)"
                    ),
                    {"name": "Telegram", "type": "telegram", "config": config_json},
                )
                logger.info("已迁移 Telegram 配置为 NotifyChannel")

        # Remove old settings keys
        old_keys = [
            "ai_base_url",
            "ai_api_key",
            "ai_model",
            "notify_telegram_bot_token",
            "notify_telegram_chat_id",
        ]
        for key in old_keys:
            if key in settings_map:
                conn.execute(
                    text("DELETE FROM app_settings WHERE key = :key"), {"key": key}
                )

        conn.commit()


def _migrate_positions_to_accounts(engine):
    """
    将旧的 stocks 表中的持仓数据迁移到 accounts + positions 表
    创建一个默认账户，并将有持仓的股票数据迁移过去
    """
    with engine.connect() as conn:
        # 检查是否已有账户数据（避免重复迁移）
        if not _has_table(conn, "accounts"):
            return

        existing_accounts = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        if existing_accounts > 0:
            return

        # 检查 stocks 表是否有持仓数据需要迁移
        if not _has_column(conn, "stocks", "cost_price"):
            return

        stocks_with_position = conn.execute(
            text(
                "SELECT id, cost_price, quantity, invested_amount FROM stocks "
                "WHERE cost_price IS NOT NULL AND quantity IS NOT NULL"
            )
        ).fetchall()

        if not stocks_with_position:
            # 没有持仓数据，创建一个空的默认账户
            conn.execute(
                text(
                    "INSERT INTO accounts (name, available_funds, enabled) VALUES ('默认账户', 0, 1)"
                )
            )
            conn.commit()
            logger.info("已创建默认账户")
            return

        # 创建默认账户
        # 先获取旧的 available_funds 设置
        old_funds = conn.execute(
            text("SELECT value FROM app_settings WHERE key = 'available_funds'")
        ).scalar()
        available_funds = float(old_funds) if old_funds else 0

        conn.execute(
            text(
                "INSERT INTO accounts (name, available_funds, enabled) VALUES (:name, :funds, 1)"
            ),
            {"name": "默认账户", "funds": available_funds},
        )
        account_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

        # 迁移持仓数据
        for row in stocks_with_position:
            stock_id, cost_price, quantity, invested_amount = row
            conn.execute(
                text(
                    "INSERT INTO positions (account_id, stock_id, cost_price, quantity, invested_amount) "
                    "VALUES (:account_id, :stock_id, :cost_price, :quantity, :invested_amount)"
                ),
                {
                    "account_id": account_id,
                    "stock_id": stock_id,
                    "cost_price": cost_price,
                    "quantity": quantity,
                    "invested_amount": invested_amount,
                },
            )

        # 删除旧的 available_funds 设置
        conn.execute(text("DELETE FROM app_settings WHERE key = 'available_funds'"))

        conn.commit()
        logger.info(f"已迁移 {len(stocks_with_position)} 条持仓数据到默认账户")


def _migrate_remove_stock_enabled(engine):
    """移除历史 stocks.enabled 软删除字段并清理残留数据。"""
    with engine.connect() as conn:
        if not _has_table(conn, "stocks") or not _has_column(conn, "stocks", "enabled"):
            return

        # 历史软删除数据：无任何关联则直接删除；有关联则恢复为有效股票。
        conn.execute(
            text(
                """
DELETE FROM stocks
WHERE COALESCE(enabled, 1) = 0
  AND id NOT IN (SELECT DISTINCT stock_id FROM positions)
  AND id NOT IN (SELECT DISTINCT stock_id FROM stock_agents)
  AND id NOT IN (SELECT DISTINCT stock_id FROM price_alert_rules)
"""
            )
        )
        conn.execute(text("UPDATE stocks SET enabled = 1 WHERE COALESCE(enabled, 1) = 0"))
        conn.commit()

        # 优先直接删列；旧版 SQLite 不支持时，重建表以确保物理移除。
        try:
            conn.execute(text("ALTER TABLE stocks DROP COLUMN enabled"))
            conn.commit()
            logger.info("已移除 stocks.enabled 列")
        except Exception:
            conn.rollback()
            logger.info("当前 SQLite 不支持 DROP COLUMN，改为重建 stocks 表移除 enabled")
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    """
CREATE TABLE IF NOT EXISTS stocks__new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  market VARCHAR NOT NULL,
  cost_price FLOAT,
  quantity INTEGER,
  invested_amount FLOAT,
  sort_order INTEGER DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
                )
            )
            conn.execute(
                text(
                    """
INSERT INTO stocks__new (
  id, symbol, name, market, cost_price, quantity, invested_amount, sort_order, created_at, updated_at
)
SELECT
  id, symbol, name, market, cost_price, quantity, invested_amount, COALESCE(sort_order, 0), created_at, updated_at
FROM stocks
"""
                )
            )
            conn.execute(text("DROP TABLE stocks"))
            conn.execute(text("ALTER TABLE stocks__new RENAME TO stocks"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
            logger.info("已通过重建表移除 stocks.enabled 列")
