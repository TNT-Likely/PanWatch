"""Versioned database migrations for PanWatch."""

from __future__ import annotations

import hashlib
import inspect
import logging
from dataclasses import dataclass
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


MIGRATIONS: tuple[Migration, ...] = (
    Migration(101, "agent_config_kind_and_visibility", _m101_agent_config_kind),
    Migration(102, "backfill_agent_kind_data", _m102_backfill_agent_kind),
    Migration(103, "agent_run_observability_fields", _m103_agent_run_observability),
    Migration(104, "analysis_history_kind_snapshot", _m104_history_kind_snapshot),
    Migration(105, "indexes_for_agent_kind_and_history", _m105_indexes),
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
