"""Custom logging handler that writes log entries to SQLite."""

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import or_

from src.web.database import SessionLocal
from src.web.models import LogEntry

MAX_LOG_ENTRIES_TOTAL = 120_000
MAX_INFRA_LOG_ENTRIES = 30_000
MAX_BUFFERED_ENTRIES = 2_000
BUFFER_SIZE = 80
FLUSH_INTERVAL = 1.0  # seconds
CLEANUP_EVERY_FLUSHES = 10
INFRA_LOGGER_PREFIXES = (
    "httpx",
    "httpcore",
    "urllib3",
    "uvicorn.access",
    "sqlalchemy.engine",
)

_ACTIVE_HANDLER = None


def get_log_handler_stats() -> dict:
    """Get runtime health stats of DB log handler."""
    h = _ACTIVE_HANDLER
    if not h:
        return {
            "enabled": False,
            "pending_entries": 0,
            "dropped_entries": 0,
            "flush_errors": 0,
            "last_flush_error": "",
            "last_flush_at": "",
        }
    with h._lock:
        return {
            "enabled": True,
            "pending_entries": len(h._buffer),
            "dropped_entries": h._dropped_entries,
            "flush_errors": h._flush_errors,
            "last_flush_error": h._last_flush_error,
            "last_flush_at": h._last_flush_at.isoformat() if h._last_flush_at else "",
        }


class DBLogHandler(logging.Handler):
    """Buffered logging handler that writes to the log_entries table."""

    def __init__(self, level=logging.DEBUG):
        super().__init__(level)
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._dropped_entries = 0
        self._flush_errors = 0
        self._last_flush_error = ""
        self._last_flush_at = None
        self._flush_count = 0
        global _ACTIVE_HANDLER
        _ACTIVE_HANDLER = self
        self._start_flush_timer()

    def emit(self, record: logging.LogRecord):
        try:
            tags = getattr(record, "tags", {})
            if not isinstance(tags, dict):
                tags = {}
            entry = {
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc),
                "level": record.levelname,
                # Persist original module logger name; UI maps to Chinese for display
                "logger_name": getattr(record, "name", ""),
                "message": self.format(record),
                "trace_id": str(getattr(record, "trace_id", "") or "")[:64],
                "run_id": str(getattr(record, "run_id", "") or "")[:64],
                "agent_name": str(getattr(record, "agent_name", "") or "")[:64],
                "event": str(getattr(record, "event", "") or "")[:64],
                "tags": tags,
                "notify_status": str(getattr(record, "notify_status", "") or "")[:32],
                "notify_reason": str(getattr(record, "notify_reason", "") or "")[:255],
            }
            with self._lock:
                if len(self._buffer) >= MAX_BUFFERED_ENTRIES:
                    overflow = len(self._buffer) - MAX_BUFFERED_ENTRIES + 1
                    if overflow > 0:
                        del self._buffer[:overflow]
                        self._dropped_entries += overflow
                self._buffer.append(entry)
                if record.levelno >= logging.ERROR or len(self._buffer) >= BUFFER_SIZE:
                    self._flush_unlocked()
        except Exception:
            # Avoid recursion if logging path fails
            pass

    def _start_flush_timer(self):
        self._timer = threading.Timer(FLUSH_INTERVAL, self._timed_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timed_flush(self):
        with self._lock:
            self._flush_unlocked()
        self._start_flush_timer()

    def _flush_unlocked(self):
        if not self._buffer:
            return
        entries = self._buffer[:]
        self._buffer.clear()

        try:
            db = SessionLocal()
            try:
                db.bulk_insert_mappings(LogEntry, entries)
                db.commit()
                self._last_flush_at = datetime.now(timezone.utc)
                self._flush_count += 1
                if self._flush_count % CLEANUP_EVERY_FLUSHES == 0:
                    self._cleanup(db)
            finally:
                db.close()
        except Exception as e:
            self._flush_errors += 1
            self._last_flush_error = str(e)[:500]

    def _cleanup(self, db):
        """Retention policy: prioritize preserving business logs."""
        # 1) cap infrastructure noise first
        infra_filters = [LogEntry.logger_name.startswith(p) for p in INFRA_LOGGER_PREFIXES]
        infra_count = db.query(LogEntry).filter(or_(*infra_filters)).count()
        if infra_count > MAX_INFRA_LOG_ENTRIES:
            overflow = infra_count - MAX_INFRA_LOG_ENTRIES
            # delete oldest infra logs in one batch
            victim_ids = (
                db.query(LogEntry.id)
                .filter(or_(*infra_filters))
                .order_by(LogEntry.id.asc())
                .limit(overflow)
                .all()
            )
            if victim_ids:
                ids = [x[0] for x in victim_ids]
                db.query(LogEntry).filter(LogEntry.id.in_(ids)).delete(
                    synchronize_session=False
                )
                db.commit()

        # 2) global hard cap
        total = db.query(LogEntry).count()
        if total > MAX_LOG_ENTRIES_TOTAL:
            cutoff = (
                db.query(LogEntry.id)
                .order_by(LogEntry.id.desc())
                .offset(MAX_LOG_ENTRIES_TOTAL)
                .first()
            )
            if cutoff:
                db.query(LogEntry).filter(LogEntry.id <= cutoff[0]).delete(
                    synchronize_session=False
                )
                db.commit()

    def close(self):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._flush_unlocked()
        super().close()
