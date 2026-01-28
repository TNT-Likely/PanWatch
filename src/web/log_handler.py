"""Custom logging handler that writes log entries to SQLite."""
import logging
import threading
from datetime import datetime, timezone

from src.web.database import SessionLocal
from src.web.models import LogEntry

MAX_LOG_ENTRIES = 10000
BUFFER_SIZE = 50
FLUSH_INTERVAL = 2.0  # seconds


class DBLogHandler(logging.Handler):
    """Buffered logging handler that writes to the log_entries table."""

    def __init__(self, level=logging.DEBUG):
        super().__init__(level)
        self._buffer: list[dict] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._start_flush_timer()

    def emit(self, record: logging.LogRecord):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc),
            "level": record.levelname,
            # Persist original module logger name; UI maps to Chinese for display
            "logger_name": getattr(record, "name", ""),
            "message": self.format(record),
        }
        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= BUFFER_SIZE:
                self._flush()

    def _start_flush_timer(self):
        self._timer = threading.Timer(FLUSH_INTERVAL, self._timed_flush)
        self._timer.daemon = True
        self._timer.start()

    def _timed_flush(self):
        with self._lock:
            self._flush()
        self._start_flush_timer()

    def _flush(self):
        if not self._buffer:
            return
        entries = self._buffer[:]
        self._buffer.clear()

        try:
            db = SessionLocal()
            try:
                for entry in entries:
                    db.add(LogEntry(**entry))
                db.commit()
                self._cleanup(db)
            finally:
                db.close()
        except Exception:
            pass  # Avoid recursion if logging fails

    def _cleanup(self, db):
        """Keep only the most recent MAX_LOG_ENTRIES entries."""
        count = db.query(LogEntry).count()
        if count > MAX_LOG_ENTRIES:
            cutoff = (
                db.query(LogEntry.id)
                .order_by(LogEntry.id.desc())
                .offset(MAX_LOG_ENTRIES)
                .first()
            )
            if cutoff:
                db.query(LogEntry).filter(LogEntry.id <= cutoff[0]).delete()
                db.commit()

    def close(self):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._flush()
        super().close()
