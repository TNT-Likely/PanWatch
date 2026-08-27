"""Versioned database migrations for PanWatch."""

from __future__ import annotations

import hashlib
import inspect
import logging
import json
from dataclasses import dataclass
from datetime import date
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    runner: Callable[[Connection], None]

    @property
    def checksum(self) -> str:
        try:
            body = inspect.getsource(self.runner)
        except Exception:
            body = self.name
        raw = f"{self.version}:{self.name}:{body}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


def _has_table(conn: Connection, table: str) -> bool:
    row = conn.execute(
        text(
            """
SELECT name
FROM sqlite_master
WHERE type='table' AND name=:table
LIMIT 1
"""
        ),
        {"table": table},
    ).first()
    return bool(row)


def _has_column(conn: Connection, table: str, column: str) -> bool:
    if not _has_table(conn, table):
        return False
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    for r in rows:
        # PRAGMA table_info schema: cid, name, type, notnull, dflt_value, pk
        if len(r) > 1 and str(r[1]) == column:
            return True
    return False


def _add_column_if_missing(conn: Connection, table: str, column: str, sql: str) -> None:
    if not _has_table(conn, table):
        return
    if not _has_column(conn, table, column):
        conn.execute(text(sql))


def _create_index_if_missing(conn: Connection, name: str, sql: str) -> None:
    row = conn.execute(
        text(
            """
SELECT name
FROM sqlite_master
WHERE type='index' AND name=:name
LIMIT 1
"""
        ),
        {"name": name},
    ).first()
    if not row:
        conn.execute(text(sql))


def _ensure_schema_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum TEXT NOT NULL,
  applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  success INTEGER NOT NULL DEFAULT 0,
  error TEXT DEFAULT ''
)
"""
        )
    )


def _m101_agent_config_kind(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "agent_configs",
        "kind",
        "ALTER TABLE agent_configs ADD COLUMN kind TEXT DEFAULT 'workflow'",
    )
    _add_column_if_missing(
        conn,
        "agent_configs",
        "visible",
        "ALTER TABLE agent_configs ADD COLUMN visible INTEGER DEFAULT 1",
    )
    _add_column_if_missing(
        conn,
        "agent_configs",
        "lifecycle_status",
        "ALTER TABLE agent_configs ADD COLUMN lifecycle_status TEXT DEFAULT 'active'",
    )
    _add_column_if_missing(
        conn,
        "agent_configs",
        "replaced_by",
        "ALTER TABLE agent_configs ADD COLUMN replaced_by TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "agent_configs",
        "display_order",
        "ALTER TABLE agent_configs ADD COLUMN display_order INTEGER DEFAULT 0",
    )


def _m102_backfill_agent_kind(conn: Connection) -> None:
    if not _has_table(conn, "agent_configs"):
        return

    conn.execute(
        text(
            """
UPDATE agent_configs
SET kind = 'workflow'
WHERE kind IS NULL OR TRIM(kind) = ''
"""
        )
    )
    conn.execute(
        text(
            """
UPDATE agent_configs
SET kind = 'capability',
    visible = 0,
    lifecycle_status = 'deprecated',
    replaced_by = CASE
      WHEN name = 'news_digest' THEN 'premarket_outlook,daily_report,intraday_monitor'
      WHEN name = 'chart_analyst' THEN 'intraday_monitor,daily_report,premarket_outlook'
      ELSE replaced_by
    END,
    enabled = 0,
    schedule = ''
WHERE name IN ('news_digest', 'chart_analyst')
"""
        )
    )
    conn.execute(
        text(
            """
UPDATE agent_configs
SET kind = 'workflow',
    visible = 1,
    lifecycle_status = 'active',
    replaced_by = ''
WHERE name IN ('premarket_outlook', 'intraday_monitor', 'daily_report')
"""
        )
    )
    conn.execute(
        text(
            """
UPDATE agent_configs
SET display_name = '收盘复盘'
WHERE name = 'daily_report'
  AND (display_name IS NULL OR TRIM(display_name) = '' OR display_name = '盘后日报')
"""
        )
    )
    conn.execute(
        text(
            """
UPDATE agent_configs
SET display_order = CASE name
  WHEN 'premarket_outlook' THEN 10
  WHEN 'intraday_monitor' THEN 20
  WHEN 'daily_report' THEN 30
  WHEN 'news_digest' THEN 110
  WHEN 'chart_analyst' THEN 120
  ELSE display_order
END
"""
        )
    )


def _m103_agent_run_observability(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "agent_runs",
        "trace_id",
        "ALTER TABLE agent_runs ADD COLUMN trace_id TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "agent_runs",
        "trigger_source",
        "ALTER TABLE agent_runs ADD COLUMN trigger_source TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "agent_runs",
        "notify_attempted",
        "ALTER TABLE agent_runs ADD COLUMN notify_attempted INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "agent_runs",
        "notify_sent",
        "ALTER TABLE agent_runs ADD COLUMN notify_sent INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "agent_runs",
        "context_chars",
        "ALTER TABLE agent_runs ADD COLUMN context_chars INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "agent_runs",
        "model_label",
        "ALTER TABLE agent_runs ADD COLUMN model_label TEXT DEFAULT ''",
    )


def _m104_history_kind_snapshot(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "analysis_history",
        "agent_kind_snapshot",
        "ALTER TABLE analysis_history ADD COLUMN agent_kind_snapshot TEXT DEFAULT ''",
    )

    if not _has_table(conn, "analysis_history"):
        return

    conn.execute(
        text(
            """
UPDATE analysis_history
SET agent_kind_snapshot = CASE
  WHEN agent_name IN ('news_digest', 'chart_analyst') THEN 'capability'
  ELSE 'workflow'
END
WHERE agent_kind_snapshot IS NULL OR TRIM(agent_kind_snapshot) = ''
"""
        )
    )


def _m105_indexes(conn: Connection) -> None:
    if _has_table(conn, "agent_configs"):
        _create_index_if_missing(
            conn,
            "ix_agent_configs_kind_visible",
            "CREATE INDEX ix_agent_configs_kind_visible ON agent_configs(kind, visible)",
        )
        _create_index_if_missing(
            conn,
            "ix_agent_configs_order",
            "CREATE INDEX ix_agent_configs_order ON agent_configs(display_order, name)",
        )
    if _has_table(conn, "agent_runs"):
        _create_index_if_missing(
            conn,
            "ix_agent_runs_agent_created",
            "CREATE INDEX ix_agent_runs_agent_created ON agent_runs(agent_name, created_at)",
        )
    if _has_table(conn, "analysis_history"):
        _create_index_if_missing(
            conn,
            "ix_analysis_history_kind_date",
            "CREATE INDEX ix_analysis_history_kind_date ON analysis_history(agent_kind_snapshot, analysis_date)",
        )
        _create_index_if_missing(
            conn,
            "ix_analysis_history_agent_updated",
            "CREATE INDEX ix_analysis_history_agent_updated ON analysis_history(agent_name, updated_at)",
        )


def _m106_log_observability(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "log_entries",
        "trace_id",
        "ALTER TABLE log_entries ADD COLUMN trace_id TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "run_id",
        "ALTER TABLE log_entries ADD COLUMN run_id TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "agent_name",
        "ALTER TABLE log_entries ADD COLUMN agent_name TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "event",
        "ALTER TABLE log_entries ADD COLUMN event TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "tags",
        "ALTER TABLE log_entries ADD COLUMN tags TEXT DEFAULT '{}'",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "notify_status",
        "ALTER TABLE log_entries ADD COLUMN notify_status TEXT DEFAULT ''",
    )
    _add_column_if_missing(
        conn,
        "log_entries",
        "notify_reason",
        "ALTER TABLE log_entries ADD COLUMN notify_reason TEXT DEFAULT ''",
    )

    if _has_table(conn, "log_entries"):
        _create_index_if_missing(
            conn,
            "ix_log_entries_time_id",
            "CREATE INDEX ix_log_entries_time_id ON log_entries(timestamp, id)",
        )
        _create_index_if_missing(
            conn,
            "ix_log_entries_trace",
            "CREATE INDEX ix_log_entries_trace ON log_entries(trace_id)",
        )
        _create_index_if_missing(
            conn,
            "ix_log_entries_agent_event",
            "CREATE INDEX ix_log_entries_agent_event ON log_entries(agent_name, event)",
        )


def _m107_suggestion_market_dimension(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "stock_suggestions",
        "stock_market",
        "ALTER TABLE stock_suggestions ADD COLUMN stock_market TEXT DEFAULT 'CN'",
    )

    if not _has_table(conn, "stock_suggestions"):
        return

    # 历史数据平滑回填：优先从 stocks 里推断 market，否则回退 CN。
    conn.execute(
        text(
            """
UPDATE stock_suggestions
SET stock_market = COALESCE(
    (
      SELECT s.market
      FROM stocks s
      WHERE s.symbol = stock_suggestions.stock_symbol
      ORDER BY CASE WHEN s.market='CN' THEN 0 ELSE 1 END, s.id ASC
      LIMIT 1
    ),
    'CN'
)
WHERE stock_market IS NULL OR TRIM(stock_market) = ''
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_suggestion_market_symbol_time",
        "CREATE INDEX ix_suggestion_market_symbol_time ON stock_suggestions(stock_market, stock_symbol, created_at)",
    )
    _create_index_if_missing(
        conn,
        "ix_suggestion_market_expires",
        "CREATE INDEX ix_suggestion_market_expires ON stock_suggestions(stock_market, expires_at)",
    )


def _m108_entry_candidates_table(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS entry_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  stock_name TEXT DEFAULT '',
  snapshot_date TEXT NOT NULL,
  status TEXT DEFAULT 'active',
  score REAL NOT NULL DEFAULT 0,
  confidence REAL,
  action TEXT NOT NULL DEFAULT 'watch',
  action_label TEXT NOT NULL DEFAULT '观望',
  signal TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  entry_low REAL,
  entry_high REAL,
  stop_loss REAL,
  target_price REAL,
  invalidation TEXT DEFAULT '',
  source_agent TEXT DEFAULT '',
  source_suggestion_id INTEGER,
  source_trace_id TEXT DEFAULT '',
  evidence TEXT DEFAULT '[]',
  plan TEXT DEFAULT '{}',
  meta TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_entry_candidate_stock_date UNIQUE(stock_symbol, stock_market, snapshot_date)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_entry_candidate_score_date",
        "CREATE INDEX ix_entry_candidate_score_date ON entry_candidates(snapshot_date, score)",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_candidate_status_updated",
        "CREATE INDEX ix_entry_candidate_status_updated ON entry_candidates(status, updated_at)",
    )

    # 历史平滑迁移：将每个市场/股票最新建议回填为“今日候选”基线记录。
    today = date.today().strftime("%Y-%m-%d")
    conn.execute(
        text(
            """
INSERT OR IGNORE INTO entry_candidates (
  stock_symbol, stock_market, stock_name, snapshot_date,
  status, score, action, action_label, signal, reason,
  source_agent, source_suggestion_id, evidence, plan, meta
)
SELECT
  s.stock_symbol,
  COALESCE(NULLIF(TRIM(s.stock_market), ''), 'CN') AS stock_market,
  COALESCE(s.stock_name, ''),
  :today AS snapshot_date,
  CASE
    WHEN s.action IN ('buy', 'add', 'hold', 'watch') THEN 'active'
    ELSE 'inactive'
  END AS status,
  CASE
    WHEN s.action = 'buy' THEN 78
    WHEN s.action = 'add' THEN 72
    WHEN s.action = 'hold' THEN 58
    WHEN s.action = 'watch' THEN 50
    ELSE 30
  END AS score,
  COALESCE(s.action, 'watch'),
  COALESCE(s.action_label, '观望'),
  COALESCE(s.signal, ''),
  COALESCE(s.reason, ''),
  COALESCE(s.agent_name, ''),
  s.id,
  '[]',
  '{}',
  COALESCE(s.meta, '{}')
FROM stock_suggestions s
JOIN (
  SELECT stock_symbol, COALESCE(NULLIF(TRIM(stock_market), ''), 'CN') AS stock_market, MAX(id) AS max_id
  FROM stock_suggestions
  GROUP BY stock_symbol, COALESCE(NULLIF(TRIM(stock_market), ''), 'CN')
) latest
ON latest.max_id = s.id
"""
        ),
        {"today": today},
    )


def _m109_entry_candidate_upgrade(conn: Connection) -> None:
    _add_column_if_missing(
        conn,
        "entry_candidates",
        "candidate_source",
        "ALTER TABLE entry_candidates ADD COLUMN candidate_source TEXT DEFAULT 'watchlist'",
    )
    _add_column_if_missing(
        conn,
        "entry_candidates",
        "strategy_tags",
        "ALTER TABLE entry_candidates ADD COLUMN strategy_tags TEXT DEFAULT '[]'",
    )
    _add_column_if_missing(
        conn,
        "entry_candidates",
        "is_holding_snapshot",
        "ALTER TABLE entry_candidates ADD COLUMN is_holding_snapshot INTEGER DEFAULT 0",
    )
    _add_column_if_missing(
        conn,
        "entry_candidates",
        "plan_quality",
        "ALTER TABLE entry_candidates ADD COLUMN plan_quality INTEGER DEFAULT 0",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_candidate_source_score",
        "CREATE INDEX ix_entry_candidate_source_score ON entry_candidates(candidate_source, score)",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_candidate_market_status",
        "CREATE INDEX ix_entry_candidate_market_status ON entry_candidates(stock_market, status)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS entry_candidate_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT DEFAULT '',
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  candidate_source TEXT NOT NULL DEFAULT 'watchlist',
  strategy_tags TEXT DEFAULT '[]',
  useful INTEGER DEFAULT 1,
  reason TEXT DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_entry_feedback_time",
        "CREATE INDEX ix_entry_feedback_time ON entry_candidate_feedback(created_at)",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_feedback_symbol_day",
        "CREATE INDEX ix_entry_feedback_symbol_day ON entry_candidate_feedback(stock_market, stock_symbol, snapshot_date)",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_feedback_source",
        "CREATE INDEX ix_entry_feedback_source ON entry_candidate_feedback(candidate_source)",
    )


def _m110_entry_candidate_outcomes(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS entry_candidate_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL REFERENCES entry_candidates(id) ON DELETE CASCADE,
  snapshot_date TEXT DEFAULT '',
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  candidate_source TEXT NOT NULL DEFAULT 'watchlist',
  strategy_tags TEXT DEFAULT '[]',
  horizon_days INTEGER NOT NULL DEFAULT 1,
  target_date TEXT DEFAULT '',
  base_price REAL,
  outcome_price REAL,
  outcome_return_pct REAL,
  hit_target INTEGER,
  hit_stop INTEGER,
  outcome_status TEXT DEFAULT 'pending',
  meta TEXT DEFAULT '{}',
  evaluated_at DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_entry_outcome_candidate_horizon UNIQUE(candidate_id, horizon_days)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_entry_outcome_status_horizon",
        "CREATE INDEX ix_entry_outcome_status_horizon ON entry_candidate_outcomes(outcome_status, horizon_days)",
    )
    _create_index_if_missing(
        conn,
        "ix_entry_outcome_symbol_day",
        "CREATE INDEX ix_entry_outcome_symbol_day ON entry_candidate_outcomes(stock_market, stock_symbol, snapshot_date)",
    )


def _m111_strategy_layer(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_catalog (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT DEFAULT '',
  version TEXT DEFAULT 'v1',
  enabled INTEGER DEFAULT 1,
  market_scope TEXT DEFAULT 'ALL',
  risk_level TEXT DEFAULT 'medium',
  params TEXT DEFAULT '{}',
  default_weight REAL DEFAULT 1.0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_catalog_enabled",
        "CREATE INDEX ix_strategy_catalog_enabled ON strategy_catalog(enabled)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_signal_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  stock_name TEXT DEFAULT '',
  strategy_code TEXT NOT NULL,
  strategy_name TEXT DEFAULT '',
  strategy_version TEXT DEFAULT 'v1',
  risk_level TEXT DEFAULT 'medium',
  source_pool TEXT DEFAULT 'watchlist',
  score REAL NOT NULL DEFAULT 0,
  rank_score REAL NOT NULL DEFAULT 0,
  confidence REAL,
  status TEXT DEFAULT 'active',
  action TEXT DEFAULT 'watch',
  action_label TEXT DEFAULT '观望',
  signal TEXT DEFAULT '',
  reason TEXT DEFAULT '',
  evidence TEXT DEFAULT '[]',
  holding_days INTEGER DEFAULT 3,
  entry_low REAL,
  entry_high REAL,
  stop_loss REAL,
  target_price REAL,
  invalidation TEXT DEFAULT '',
  plan_quality INTEGER DEFAULT 0,
  source_agent TEXT DEFAULT '',
  source_suggestion_id INTEGER,
  source_candidate_id INTEGER,
  trace_id TEXT DEFAULT '',
  is_holding_snapshot INTEGER DEFAULT 0,
  context_quality_score REAL,
  payload TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strategy_signal_daily_unique UNIQUE(snapshot_date, stock_symbol, stock_market, strategy_code, source_candidate_id)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_signal_snapshot_rank",
        "CREATE INDEX ix_strategy_signal_snapshot_rank ON strategy_signal_runs(snapshot_date, rank_score)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_signal_strategy_market",
        "CREATE INDEX ix_strategy_signal_strategy_market ON strategy_signal_runs(strategy_code, stock_market)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_signal_status",
        "CREATE INDEX ix_strategy_signal_status ON strategy_signal_runs(status, updated_at)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_run_id INTEGER NOT NULL REFERENCES strategy_signal_runs(id) ON DELETE CASCADE,
  strategy_code TEXT NOT NULL,
  snapshot_date TEXT DEFAULT '',
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  source_pool TEXT DEFAULT 'watchlist',
  horizon_days INTEGER NOT NULL DEFAULT 1,
  target_date TEXT DEFAULT '',
  base_price REAL,
  outcome_price REAL,
  outcome_return_pct REAL,
  hit_target INTEGER,
  hit_stop INTEGER,
  outcome_status TEXT DEFAULT 'pending',
  meta TEXT DEFAULT '{}',
  evaluated_at DATETIME,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strategy_outcome_signal_horizon UNIQUE(signal_run_id, horizon_days)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_outcome_strategy_horizon",
        "CREATE INDEX ix_strategy_outcome_strategy_horizon ON strategy_outcomes(strategy_code, horizon_days)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_outcome_market_date",
        "CREATE INDEX ix_strategy_outcome_market_date ON strategy_outcomes(stock_market, target_date)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_outcome_status",
        "CREATE INDEX ix_strategy_outcome_status ON strategy_outcomes(outcome_status, evaluated_at)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_weights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_code TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT 'ALL',
  regime TEXT NOT NULL DEFAULT 'default',
  weight REAL NOT NULL DEFAULT 1.0,
  reason TEXT DEFAULT '',
  meta TEXT DEFAULT '{}',
  effective_from DATETIME DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strategy_weight_key UNIQUE(strategy_code, market, regime)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_weight_effective",
        "CREATE INDEX ix_strategy_weight_effective ON strategy_weights(effective_from)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_weight_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_code TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT 'ALL',
  regime TEXT NOT NULL DEFAULT 'default',
  old_weight REAL NOT NULL DEFAULT 1.0,
  new_weight REAL NOT NULL DEFAULT 1.0,
  reason TEXT DEFAULT '',
  window_days INTEGER DEFAULT 45,
  sample_size INTEGER DEFAULT 0,
  meta TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_weight_history_time",
        "CREATE INDEX ix_strategy_weight_history_time ON strategy_weight_history(created_at)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_weight_history_strategy_market",
        "CREATE INDEX ix_strategy_weight_history_strategy_market ON strategy_weight_history(strategy_code, market)",
    )

    # Seed strategy catalog (parameterized to avoid ':' bind parsing in JSON literals)
    seed_sql = text(
        """
INSERT OR IGNORE INTO strategy_catalog(
  code, name, description, version, enabled, market_scope, risk_level, params, default_weight
)
VALUES(
  :code, :name, :description, :version, :enabled, :market_scope, :risk_level, :params, :default_weight
)
"""
    )
    seed_rows = [
        {
            "code": "trend_follow",
            "name": "趋势延续",
            "description": "顺势跟随，优先均线多头且动量延续",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "medium",
            "params": '{"horizon_days":5}',
            "default_weight": 1.15,
        },
        {
            "code": "macd_golden",
            "name": "MACD金叉",
            "description": "MACD 金叉确认，偏中短线",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "medium",
            "params": '{"horizon_days":3}',
            "default_weight": 1.10,
        },
        {
            "code": "volume_breakout",
            "name": "放量突破",
            "description": "放量突破关键位，偏进攻",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "high",
            "params": '{"horizon_days":3}',
            "default_weight": 1.18,
        },
        {
            "code": "pullback",
            "name": "回踩确认",
            "description": "回踩支撑后二次启动",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "low",
            "params": '{"horizon_days":5}',
            "default_weight": 1.05,
        },
        {
            "code": "rebound",
            "name": "超跌反弹",
            "description": "超跌后的反弹交易",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "high",
            "params": '{"horizon_days":3}',
            "default_weight": 0.95,
        },
        {
            "code": "watchlist_agent",
            "name": "Agent建议",
            "description": "来自既有 Agent 的综合建议映射",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "medium",
            "params": '{"horizon_days":3}',
            "default_weight": 1.00,
        },
        {
            "code": "market_scan",
            "name": "市场扫描",
            "description": "市场池扫描策略（热门与活跃）",
            "version": "v1",
            "enabled": 1,
            "market_scope": "ALL",
            "risk_level": "medium",
            "params": '{"horizon_days":3}',
            "default_weight": 1.08,
        },
    ]
    for row in seed_rows:
        conn.execute(seed_sql, row)

    # Legacy smooth migration: entry_candidates -> strategy_signal_runs
    if _has_table(conn, "entry_candidates"):
        conn.execute(
            text(
                """
INSERT OR IGNORE INTO strategy_signal_runs (
  snapshot_date, stock_symbol, stock_market, stock_name,
  strategy_code, strategy_name, strategy_version, risk_level, source_pool,
  score, rank_score, confidence, status, action, action_label, signal, reason,
  evidence, holding_days, entry_low, entry_high, stop_loss, target_price, invalidation,
  plan_quality, source_agent, source_suggestion_id, source_candidate_id, trace_id,
  is_holding_snapshot, payload, created_at, updated_at
)
SELECT
  ec.snapshot_date,
  ec.stock_symbol,
  ec.stock_market,
  ec.stock_name,
  CASE
    WHEN ec.strategy_tags LIKE '%trend_follow%' THEN 'trend_follow'
    WHEN ec.strategy_tags LIKE '%macd_golden%' THEN 'macd_golden'
    WHEN ec.strategy_tags LIKE '%volume_breakout%' THEN 'volume_breakout'
    WHEN ec.strategy_tags LIKE '%pullback%' THEN 'pullback'
    WHEN ec.strategy_tags LIKE '%rebound%' THEN 'rebound'
    WHEN ec.candidate_source = 'market_scan' THEN 'market_scan'
    ELSE 'watchlist_agent'
  END AS strategy_code,
  CASE
    WHEN ec.strategy_tags LIKE '%trend_follow%' THEN '趋势延续'
    WHEN ec.strategy_tags LIKE '%macd_golden%' THEN 'MACD金叉'
    WHEN ec.strategy_tags LIKE '%volume_breakout%' THEN '放量突破'
    WHEN ec.strategy_tags LIKE '%pullback%' THEN '回踩确认'
    WHEN ec.strategy_tags LIKE '%rebound%' THEN '超跌反弹'
    WHEN ec.candidate_source = 'market_scan' THEN '市场扫描'
    ELSE 'Agent建议'
  END AS strategy_name,
  'v1' AS strategy_version,
  CASE
    WHEN ec.action IN ('buy', 'add') AND ec.score >= 80 THEN 'high'
    WHEN ec.action IN ('watch', 'hold') THEN 'low'
    ELSE 'medium'
  END AS risk_level,
  COALESCE(ec.candidate_source, 'watchlist') AS source_pool,
  ec.score,
  ec.score,
  ec.confidence,
  ec.status,
  ec.action,
  ec.action_label,
  COALESCE(ec.signal, ''),
  COALESCE(ec.reason, ''),
  COALESCE(ec.evidence, '[]'),
  3,
  ec.entry_low,
  ec.entry_high,
  ec.stop_loss,
  ec.target_price,
  COALESCE(ec.invalidation, ''),
  COALESCE(ec.plan_quality, 0),
  COALESCE(ec.source_agent, ''),
  ec.source_suggestion_id,
  ec.id,
  COALESCE(ec.source_trace_id, ''),
  COALESCE(ec.is_holding_snapshot, 0),
  COALESCE(ec.meta, '{}'),
  ec.created_at,
  ec.updated_at
FROM entry_candidates ec
"""
            )
        )

    # Legacy smooth migration: entry_candidate_outcomes -> strategy_outcomes
    if _has_table(conn, "entry_candidate_outcomes"):
        conn.execute(
            text(
                """
INSERT OR IGNORE INTO strategy_outcomes (
  signal_run_id, strategy_code, snapshot_date, stock_symbol, stock_market, source_pool,
  horizon_days, target_date, base_price, outcome_price, outcome_return_pct,
  hit_target, hit_stop, outcome_status, meta, evaluated_at, created_at
)
SELECT
  sr.id,
  sr.strategy_code,
  eco.snapshot_date,
  eco.stock_symbol,
  eco.stock_market,
  COALESCE(eco.candidate_source, 'watchlist'),
  eco.horizon_days,
  eco.target_date,
  eco.base_price,
  eco.outcome_price,
  eco.outcome_return_pct,
  eco.hit_target,
  eco.hit_stop,
  eco.outcome_status,
  COALESCE(eco.meta, '{}'),
  eco.evaluated_at,
  eco.created_at
FROM entry_candidate_outcomes eco
JOIN strategy_signal_runs sr ON sr.source_candidate_id = eco.candidate_id
"""
            )
        )


def _m112_strategy_analytics_snapshots(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS market_regime_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT 'CN',
  regime TEXT NOT NULL DEFAULT 'neutral',
  regime_score REAL NOT NULL DEFAULT 0.0,
  confidence REAL NOT NULL DEFAULT 0.0,
  breadth_up_pct REAL,
  avg_change_pct REAL,
  volatility_pct REAL,
  active_ratio REAL,
  sample_size INTEGER DEFAULT 0,
  meta TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_market_regime_day_market UNIQUE(snapshot_date, market)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_market_regime_snapshot",
        "CREATE INDEX ix_market_regime_snapshot ON market_regime_snapshots(snapshot_date, market)",
    )
    _create_index_if_missing(
        conn,
        "ix_market_regime_type",
        "CREATE INDEX ix_market_regime_type ON market_regime_snapshots(regime)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS strategy_factor_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_run_id INTEGER NOT NULL REFERENCES strategy_signal_runs(id) ON DELETE CASCADE,
  snapshot_date TEXT NOT NULL,
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  strategy_code TEXT NOT NULL,
  alpha_score REAL DEFAULT 0.0,
  catalyst_score REAL DEFAULT 0.0,
  quality_score REAL DEFAULT 0.0,
  risk_penalty REAL DEFAULT 0.0,
  crowd_penalty REAL DEFAULT 0.0,
  source_bonus REAL DEFAULT 0.0,
  regime_multiplier REAL DEFAULT 1.0,
  final_score REAL NOT NULL DEFAULT 0.0,
  factor_payload TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_strategy_factor_signal UNIQUE(signal_run_id)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_factor_snapshot_score",
        "CREATE INDEX ix_strategy_factor_snapshot_score ON strategy_factor_snapshots(snapshot_date, final_score)",
    )
    _create_index_if_missing(
        conn,
        "ix_strategy_factor_strategy_market",
        "CREATE INDEX ix_strategy_factor_strategy_market ON strategy_factor_snapshots(strategy_code, stock_market)",
    )

    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT 'CN',
  total_signals INTEGER DEFAULT 0,
  active_signals INTEGER DEFAULT 0,
  held_signals INTEGER DEFAULT 0,
  unheld_signals INTEGER DEFAULT 0,
  high_risk_ratio REAL,
  concentration_top5 REAL,
  avg_rank_score REAL,
  risk_level TEXT DEFAULT 'medium',
  meta TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_portfolio_risk_day_market UNIQUE(snapshot_date, market)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_portfolio_risk_snapshot",
        "CREATE INDEX ix_portfolio_risk_snapshot ON portfolio_risk_snapshots(snapshot_date, market)",
    )

    if not _has_table(conn, "strategy_signal_runs"):
        return

    rows = conn.execute(
        text(
            """
SELECT
  id,
  snapshot_date,
  stock_symbol,
  stock_market,
  strategy_code,
  status,
  risk_level,
  rank_score,
  is_holding_snapshot,
  payload
FROM strategy_signal_runs
ORDER BY snapshot_date DESC, stock_market ASC, rank_score DESC
"""
        )
    ).fetchall()
    if not rows:
        return

    factor_insert = text(
        """
INSERT OR IGNORE INTO strategy_factor_snapshots(
  signal_run_id, snapshot_date, stock_symbol, stock_market, strategy_code,
  alpha_score, catalyst_score, quality_score, risk_penalty, crowd_penalty, source_bonus,
  regime_multiplier, final_score, factor_payload
)
VALUES(
  :signal_run_id, :snapshot_date, :stock_symbol, :stock_market, :strategy_code,
  :alpha_score, :catalyst_score, :quality_score, :risk_penalty, :crowd_penalty, :source_bonus,
  :regime_multiplier, :final_score, :factor_payload
)
"""
    )

    bucket: dict[tuple[str, str], dict] = {}
    for r in rows:
        signal_id = int(r[0])
        snapshot_date = str(r[1] or "")
        stock_symbol = str(r[2] or "")
        stock_market = str(r[3] or "CN")
        strategy_code = str(r[4] or "")
        status = str(r[5] or "")
        risk_level = str(r[6] or "medium")
        rank_score = float(r[7] or 0.0)
        is_holding = bool(r[8] or 0)
        payload_raw = r[9]

        payload_obj = {}
        if isinstance(payload_raw, str) and payload_raw.strip():
            try:
                payload_obj = json.loads(payload_raw)
            except Exception:
                payload_obj = {}
        elif isinstance(payload_raw, dict):
            payload_obj = payload_raw

        change_pct = None
        source_meta = payload_obj.get("source_meta") if isinstance(payload_obj, dict) else None
        if isinstance(source_meta, dict):
            quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
            try:
                if quote.get("change_pct") is not None:
                    change_pct = float(quote.get("change_pct"))
            except Exception:
                change_pct = None

        # Backfill factor snapshot with conservative decomposition.
        conn.execute(
            factor_insert,
            {
                "signal_run_id": signal_id,
                "snapshot_date": snapshot_date,
                "stock_symbol": stock_symbol,
                "stock_market": stock_market,
                "strategy_code": strategy_code,
                "alpha_score": round(rank_score * 0.35, 4),
                "catalyst_score": 0.0,
                "quality_score": round(rank_score * 0.15, 4),
                "risk_penalty": 0.0,
                "crowd_penalty": 0.0,
                "source_bonus": 0.0,
                "regime_multiplier": 1.0,
                "final_score": round(rank_score, 4),
                "factor_payload": json.dumps(
                    {
                        "backfilled": True,
                        "change_pct": change_pct,
                    },
                    ensure_ascii=False,
                ),
            },
        )

        key = (snapshot_date, stock_market)
        agg = bucket.setdefault(
            key,
            {
                "scores": [],
                "changes": [],
                "total": 0,
                "active": 0,
                "held": 0,
                "high_risk": 0,
            },
        )
        agg["total"] += 1
        if status == "active":
            agg["active"] += 1
        if is_holding:
            agg["held"] += 1
        if risk_level == "high":
            agg["high_risk"] += 1
        agg["scores"].append(rank_score)
        if change_pct is not None:
            agg["changes"].append(change_pct)

    regime_insert = text(
        """
INSERT OR REPLACE INTO market_regime_snapshots(
  snapshot_date, market, regime, regime_score, confidence, breadth_up_pct,
  avg_change_pct, volatility_pct, active_ratio, sample_size, meta, updated_at
)
VALUES(
  :snapshot_date, :market, :regime, :regime_score, :confidence, :breadth_up_pct,
  :avg_change_pct, :volatility_pct, :active_ratio, :sample_size, :meta, CURRENT_TIMESTAMP
)
"""
    )
    risk_insert = text(
        """
INSERT OR REPLACE INTO portfolio_risk_snapshots(
  snapshot_date, market, total_signals, active_signals, held_signals, unheld_signals,
  high_risk_ratio, concentration_top5, avg_rank_score, risk_level, meta, updated_at
)
VALUES(
  :snapshot_date, :market, :total_signals, :active_signals, :held_signals, :unheld_signals,
  :high_risk_ratio, :concentration_top5, :avg_rank_score, :risk_level, :meta, CURRENT_TIMESTAMP
)
"""
    )

    for (snap, market), agg in bucket.items():
        total = int(agg["total"] or 0)
        active = int(agg["active"] or 0)
        held = int(agg["held"] or 0)
        unheld = max(0, total - held)
        scores = sorted([float(x) for x in agg["scores"] if x is not None], reverse=True)
        score_sum = sum(scores)
        avg_score = (score_sum / total) if total else 0.0
        top5 = sum(scores[:5])
        concentration = (top5 / score_sum) if score_sum > 0 else 0.0
        high_risk_ratio = (float(agg["high_risk"]) / total) if total else 0.0

        changes = [float(x) for x in agg["changes"] if x is not None]
        breadth_up_pct = (
            sum(1 for c in changes if c > 0) / len(changes) * 100.0 if changes else None
        )
        avg_change_pct = (sum(changes) / len(changes)) if changes else None
        volatility_pct = None
        if len(changes) >= 2:
            mean = sum(changes) / len(changes)
            variance = sum((c - mean) ** 2 for c in changes) / (len(changes) - 1)
            volatility_pct = variance ** 0.5

        active_ratio = (active / total) if total else 0.0
        breadth_norm = (
            max(-1.0, min(1.0, ((breadth_up_pct or 50.0) - 50.0) / 50.0))
            if breadth_up_pct is not None
            else 0.0
        )
        change_norm = (
            max(-1.0, min(1.0, (avg_change_pct or 0.0) / 3.0))
            if avg_change_pct is not None
            else 0.0
        )
        active_norm = max(-1.0, min(1.0, (active_ratio - 0.5) / 0.5))
        regime_score = 0.45 * breadth_norm + 0.30 * change_norm + 0.25 * active_norm
        if regime_score >= 0.20:
            regime = "bullish"
        elif regime_score <= -0.20:
            regime = "bearish"
        else:
            regime = "neutral"
        confidence = min(
            1.0,
            max(0.0, abs(regime_score) * 1.4 + min(0.4, total / 250.0)),
        )

        if high_risk_ratio >= 0.45 or concentration >= 0.65:
            risk_level = "high"
        elif high_risk_ratio >= 0.28 or concentration >= 0.48:
            risk_level = "medium"
        else:
            risk_level = "low"

        conn.execute(
            regime_insert,
            {
                "snapshot_date": snap,
                "market": market,
                "regime": regime,
                "regime_score": round(regime_score, 4),
                "confidence": round(confidence, 4),
                "breadth_up_pct": round(breadth_up_pct, 4) if breadth_up_pct is not None else None,
                "avg_change_pct": round(avg_change_pct, 4) if avg_change_pct is not None else None,
                "volatility_pct": round(volatility_pct, 4) if volatility_pct is not None else None,
                "active_ratio": round(active_ratio, 4),
                "sample_size": total,
                "meta": json.dumps(
                    {
                        "from_strategy_runs": True,
                        "active_signals": active,
                    },
                    ensure_ascii=False,
                ),
            },
        )
        conn.execute(
            risk_insert,
            {
                "snapshot_date": snap,
                "market": market,
                "total_signals": total,
                "active_signals": active,
                "held_signals": held,
                "unheld_signals": unheld,
                "high_risk_ratio": round(high_risk_ratio, 4),
                "concentration_top5": round(concentration, 4),
                "avg_rank_score": round(avg_score, 4),
                "risk_level": risk_level,
                "meta": json.dumps(
                    {
                        "from_strategy_runs": True,
                        "score_sum": round(score_sum, 4),
                    },
                    ensure_ascii=False,
                ),
            },
        )


def _m113_market_scan_snapshot_and_mixed_source(conn: Connection) -> None:
    conn.execute(
        text(
            """
CREATE TABLE IF NOT EXISTS market_scan_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_date TEXT NOT NULL,
  stock_symbol TEXT NOT NULL,
  stock_market TEXT NOT NULL DEFAULT 'CN',
  stock_name TEXT DEFAULT '',
  source TEXT NOT NULL DEFAULT 'market_scan',
  score_seed REAL NOT NULL DEFAULT 0.0,
  quote TEXT DEFAULT '{}',
  meta TEXT DEFAULT '{}',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_market_scan_snapshot_symbol UNIQUE(snapshot_date, stock_symbol, stock_market)
)
"""
        )
    )
    _create_index_if_missing(
        conn,
        "ix_market_scan_snapshot_day_market",
        "CREATE INDEX ix_market_scan_snapshot_day_market ON market_scan_snapshots(snapshot_date, stock_market)",
    )
    _create_index_if_missing(
        conn,
        "ix_market_scan_snapshot_source",
        "CREATE INDEX ix_market_scan_snapshot_source ON market_scan_snapshots(snapshot_date, source)",
    )

    if _has_table(conn, "entry_candidates"):
        conn.execute(
            text(
                """
UPDATE entry_candidates
SET candidate_source = 'mixed'
WHERE candidate_source = 'market_scan'
  AND source_suggestion_id IS NOT NULL
"""
            )
        )
    if _has_table(conn, "strategy_signal_runs"):
        conn.execute(
            text(
                """
UPDATE strategy_signal_runs
SET source_pool = 'mixed'
WHERE source_pool = 'market_scan'
  AND source_suggestion_id IS NOT NULL
"""
            )
        )


def _m114_paper_trading_tables(conn: Connection) -> None:
    """创建模拟盘三张表。"""
    if not _has_table(conn, "paper_trading_account"):
        conn.execute(
            text(
                """
CREATE TABLE paper_trading_account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    initial_capital REAL NOT NULL DEFAULT 1000000.0,
    current_capital REAL NOT NULL DEFAULT 1000000.0,
    total_pnl REAL NOT NULL DEFAULT 0.0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    winning_trades INTEGER NOT NULL DEFAULT 0,
    max_drawdown_pct REAL NOT NULL DEFAULT 0.0,
    peak_capital REAL NOT NULL DEFAULT 1000000.0,
    enabled BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
            )
        )
    if not _has_table(conn, "paper_trading_positions"):
        conn.execute(
            text(
                """
CREATE TABLE paper_trading_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_symbol TEXT NOT NULL,
    stock_market TEXT NOT NULL DEFAULT 'CN',
    stock_name TEXT DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 100,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    target_price REAL,
    current_price REAL,
    unrealized_pnl REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'open',
    signal_run_id INTEGER,
    signal_snapshot_date TEXT DEFAULT '',
    signal_action TEXT DEFAULT '',
    strategy_code TEXT DEFAULT '',
    opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
            )
        )
        conn.execute(text("CREATE INDEX ix_paper_pos_status ON paper_trading_positions(status)"))
        conn.execute(text("CREATE INDEX ix_paper_pos_symbol_market ON paper_trading_positions(stock_symbol, stock_market)"))
    if not _has_table(conn, "paper_trading_trades"):
        conn.execute(
            text(
                """
CREATE TABLE paper_trading_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_symbol TEXT NOT NULL,
    stock_market TEXT NOT NULL DEFAULT 'CN',
    stock_name TEXT DEFAULT '',
    quantity INTEGER NOT NULL DEFAULT 100,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    pnl REAL NOT NULL DEFAULT 0.0,
    pnl_pct REAL NOT NULL DEFAULT 0.0,
    exit_reason TEXT NOT NULL DEFAULT '',
    signal_run_id INTEGER,
    signal_snapshot_date TEXT DEFAULT '',
    strategy_code TEXT DEFAULT '',
    holding_days INTEGER DEFAULT 0,
    opened_at DATETIME,
    closed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    meta TEXT DEFAULT '{}'
)
"""
            )
        )
        conn.execute(text("CREATE INDEX ix_paper_trade_closed ON paper_trading_trades(closed_at)"))
        conn.execute(text("CREATE INDEX ix_paper_trade_symbol ON paper_trading_trades(stock_symbol, stock_market)"))


def _m115_paper_trading_excluded_markets(conn: Connection) -> None:
    """模拟盘账户新增 excluded_markets 字段。"""
    _add_column_if_missing(
        conn,
        "paper_trading_account",
        "excluded_markets",
        "ALTER TABLE paper_trading_account ADD COLUMN excluded_markets TEXT DEFAULT '[]'",
    )


def _m116_chat_tables(conn: Connection) -> None:
    """AI 对话表。"""
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS chat_conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT '',
            stock_symbol TEXT,
            stock_market TEXT,
            ai_model_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
    )
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            content TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
    )
    _create_index_if_missing(
        conn, "ix_chat_conv_updated",
        "CREATE INDEX ix_chat_conv_updated ON chat_conversations(updated_at)",
    )
    _create_index_if_missing(
        conn, "ix_chat_conv_stock",
        "CREATE INDEX ix_chat_conv_stock ON chat_conversations(stock_symbol, stock_market)",
    )
    _create_index_if_missing(
        conn, "ix_chat_msg_conv",
        "CREATE INDEX ix_chat_msg_conv ON chat_messages(conversation_id, created_at)",
    )


def _m117_chat_initial_context(conn: Connection) -> None:
    """Add initial_context column to chat_conversations."""
    try:
        conn.execute(text("ALTER TABLE chat_conversations ADD COLUMN initial_context TEXT"))
    except Exception:
        pass  # column already exists


def _m118_paper_trading_market_allocations(conn: Connection) -> None:
    """模拟盘账户新增 market_allocations（各市场投资比例），并由 excluded_markets 回填。"""
    _add_column_if_missing(
        conn,
        "paper_trading_account",
        "market_allocations",
        "ALTER TABLE paper_trading_account ADD COLUMN market_allocations TEXT DEFAULT '{}'",
    )
    if not _has_table(conn, "paper_trading_account"):
        return

    # 复用引擎的纯函数推导比例（函数内导入，避免模块级循环依赖）
    from src.core.paper_trading_engine import allocations_from_excluded

    rows = conn.execute(
        text("SELECT id, excluded_markets, market_allocations FROM paper_trading_account")
    ).fetchall()
    for r in rows:
        row_id = r[0]

        # 已有非空比例则跳过，避免覆盖用户配置
        raw_alloc = r[2]
        has_alloc = False
        if isinstance(raw_alloc, str) and raw_alloc.strip() and raw_alloc.strip() not in ("{}", "null"):
            try:
                has_alloc = bool(json.loads(raw_alloc))
            except Exception:
                has_alloc = False
        elif isinstance(raw_alloc, dict):
            has_alloc = bool(raw_alloc)
        if has_alloc:
            continue

        excluded: list[str] = []
        raw_excluded = r[1]
        if isinstance(raw_excluded, str) and raw_excluded.strip():
            try:
                parsed = json.loads(raw_excluded)
                if isinstance(parsed, list):
                    excluded = [str(x) for x in parsed]
            except Exception:
                excluded = []
        elif isinstance(raw_excluded, list):
            excluded = [str(x) for x in raw_excluded]

        alloc = allocations_from_excluded(excluded)
        conn.execute(
            text("UPDATE paper_trading_account SET market_allocations = :alloc WHERE id = :id"),
            {"alloc": json.dumps(alloc, ensure_ascii=False), "id": row_id},
        )


def _m119_pat_and_mcp_tables(conn: Connection) -> None:
    """PAT 令牌表 + MCP 调用日志表(MCP Server 鉴权与审计)。"""
    conn.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS personal_access_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT DEFAULT '',
            token_hash TEXT NOT NULL,
            prefix TEXT DEFAULT '',
            scopes_json TEXT DEFAULT '[]',
            expires_at TIMESTAMP,
            last_used_at TIMESTAMP,
            last_used_ip TEXT,
            revoked_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
    )
    # 与 ORM 模型的自动唯一索引同名(token_hash unique+index),便于 create_all 复核
    _create_index_if_missing(
        conn,
        "ix_personal_access_tokens_token_hash",
        "CREATE UNIQUE INDEX ix_personal_access_tokens_token_hash "
        "ON personal_access_tokens(token_hash)",
    )
    _create_index_if_missing(
        conn,
        "ix_pat_revoked",
        "CREATE INDEX ix_pat_revoked ON personal_access_tokens(revoked_at)",
    )
    conn.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS mcp_call_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pat_id INTEGER,
            pat_prefix TEXT,
            tool_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ok',
            error_message TEXT,
            args_summary TEXT,
            duration_ms INTEGER DEFAULT 0,
            client_ip TEXT,
            called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
    )
    _create_index_if_missing(
        conn,
        "ix_mcp_log_tool",
        "CREATE INDEX ix_mcp_log_tool ON mcp_call_logs(tool_name)",
    )
    _create_index_if_missing(
        conn,
        "ix_mcp_log_called",
        "CREATE INDEX ix_mcp_log_called ON mcp_call_logs(called_at)",
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(101, "agent_config_kind_and_visibility", _m101_agent_config_kind),
    Migration(102, "backfill_agent_kind_data", _m102_backfill_agent_kind),
    Migration(103, "agent_run_observability_fields", _m103_agent_run_observability),
    Migration(104, "analysis_history_kind_snapshot", _m104_history_kind_snapshot),
    Migration(105, "indexes_for_agent_kind_and_history", _m105_indexes),
    Migration(106, "log_entry_observability_fields", _m106_log_observability),
    Migration(107, "stock_suggestion_market_dimension", _m107_suggestion_market_dimension),
    Migration(108, "entry_candidates_table", _m108_entry_candidates_table),
    Migration(109, "entry_candidate_upgrade", _m109_entry_candidate_upgrade),
    Migration(110, "entry_candidate_outcomes", _m110_entry_candidate_outcomes),
    Migration(111, "strategy_layer", _m111_strategy_layer),
    Migration(112, "strategy_analytics_snapshots", _m112_strategy_analytics_snapshots),
    Migration(113, "market_scan_snapshot_and_mixed_source", _m113_market_scan_snapshot_and_mixed_source),
    Migration(114, "paper_trading_tables", _m114_paper_trading_tables),
    Migration(115, "paper_trading_excluded_markets", _m115_paper_trading_excluded_markets),
    Migration(116, "chat_tables", _m116_chat_tables),
    Migration(117, "chat_initial_context", _m117_chat_initial_context),
    Migration(118, "paper_trading_market_allocations", _m118_paper_trading_market_allocations),
    Migration(119, "pat_and_mcp_tables", _m119_pat_and_mcp_tables),
)


def _get_applied(conn: Connection, version: int) -> tuple[int, str, int] | None:
    row = conn.execute(
        text(
            """
SELECT version, checksum, success
FROM schema_migrations
WHERE version = :version
LIMIT 1
"""
        ),
        {"version": version},
    ).first()
    if not row:
        return None
    return int(row[0]), str(row[1]), int(row[2])


def has_pending_migrations(engine: Engine) -> bool:
    with engine.begin() as conn:
        _ensure_schema_table(conn)
        for m in MIGRATIONS:
            rec = _get_applied(conn, m.version)
            if not rec or rec[2] != 1 or rec[1] != m.checksum:
                return True
    return False


def run_versioned_migrations(engine: Engine) -> None:
    with engine.begin() as conn:
        _ensure_schema_table(conn)

    for m in MIGRATIONS:
        with engine.begin() as conn:
            _ensure_schema_table(conn)
            rec = _get_applied(conn, m.version)
            if rec and rec[2] == 1 and rec[1] == m.checksum:
                continue

            conn.execute(
                text(
                    """
INSERT INTO schema_migrations(version, name, checksum, success, error)
VALUES(:version, :name, :checksum, 0, '')
ON CONFLICT(version) DO UPDATE SET
  name = excluded.name,
  checksum = excluded.checksum,
  success = 0,
  error = ''
"""
                ),
                {
                    "version": m.version,
                    "name": m.name,
                    "checksum": m.checksum,
                },
            )
            logger.info("Applying migration v%s: %s", m.version, m.name)

            try:
                m.runner(conn)
                conn.execute(
                    text(
                        """
UPDATE schema_migrations
SET success = 1,
    error = '',
    applied_at = CURRENT_TIMESTAMP
WHERE version = :version
"""
                    ),
                    {"version": m.version},
                )
            except Exception as exc:
                conn.execute(
                    text(
                        """
UPDATE schema_migrations
SET success = 0,
    error = :error,
    applied_at = CURRENT_TIMESTAMP
WHERE version = :version
"""
                    ),
                    {"version": m.version, "error": str(exc)[:2000]},
                )
                logger.exception("Migration v%s failed: %s", m.version, m.name)
                raise
