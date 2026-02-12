"""Structured log context helpers (contextvars + LogRecord factory)."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_trace_id_var: ContextVar[str] = ContextVar("log_trace_id", default="")
_run_id_var: ContextVar[str] = ContextVar("log_run_id", default="")
_agent_name_var: ContextVar[str] = ContextVar("log_agent_name", default="")
_event_var: ContextVar[str] = ContextVar("log_event", default="")
_notify_status_var: ContextVar[str] = ContextVar("log_notify_status", default="")
_notify_reason_var: ContextVar[str] = ContextVar("log_notify_reason", default="")
_tags_var: ContextVar[dict] = ContextVar("log_tags", default={})


def get_log_context() -> dict[str, Any]:
    """Return current structured log context."""
    return {
        "trace_id": _trace_id_var.get() or "",
        "run_id": _run_id_var.get() or "",
        "agent_name": _agent_name_var.get() or "",
        "event": _event_var.get() or "",
        "notify_status": _notify_status_var.get() or "",
        "notify_reason": _notify_reason_var.get() or "",
        "tags": _tags_var.get() or {},
    }


@contextmanager
def log_context(
    *,
    trace_id: str | None = None,
    run_id: str | None = None,
    agent_name: str | None = None,
    event: str | None = None,
    notify_status: str | None = None,
    notify_reason: str | None = None,
    tags: dict | None = None,
):
    """Bind structured log context for current execution scope."""
    tokens = []
    if trace_id is not None:
        tokens.append((_trace_id_var, _trace_id_var.set(trace_id)))
    if run_id is not None:
        tokens.append((_run_id_var, _run_id_var.set(run_id)))
    if agent_name is not None:
        tokens.append((_agent_name_var, _agent_name_var.set(agent_name)))
    if event is not None:
        tokens.append((_event_var, _event_var.set(event)))
    if notify_status is not None:
        tokens.append((_notify_status_var, _notify_status_var.set(notify_status)))
    if notify_reason is not None:
        tokens.append((_notify_reason_var, _notify_reason_var.set(notify_reason)))
    if tags is not None:
        tokens.append((_tags_var, _tags_var.set(tags)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


def bind_log_context(**kwargs) -> None:
    """Set context fields for current task without contextmanager."""
    if "trace_id" in kwargs and kwargs["trace_id"] is not None:
        _trace_id_var.set(str(kwargs["trace_id"]))
    if "run_id" in kwargs and kwargs["run_id"] is not None:
        _run_id_var.set(str(kwargs["run_id"]))
    if "agent_name" in kwargs and kwargs["agent_name"] is not None:
        _agent_name_var.set(str(kwargs["agent_name"]))
    if "event" in kwargs and kwargs["event"] is not None:
        _event_var.set(str(kwargs["event"]))
    if "notify_status" in kwargs and kwargs["notify_status"] is not None:
        _notify_status_var.set(str(kwargs["notify_status"]))
    if "notify_reason" in kwargs and kwargs["notify_reason"] is not None:
        _notify_reason_var.set(str(kwargs["notify_reason"]))
    if "tags" in kwargs and kwargs["tags"] is not None:
        _tags_var.set(kwargs["tags"] if isinstance(kwargs["tags"], dict) else {})


_factory_installed = False


def install_log_record_factory() -> None:
    """Install a record factory that injects contextvars into each log record."""
    global _factory_installed
    if _factory_installed:
        return

    old_factory = logging.getLogRecordFactory()

    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        ctx = get_log_context()
        record.trace_id = ctx["trace_id"]
        record.run_id = ctx["run_id"]
        record.agent_name = ctx["agent_name"]
        record.event = ctx["event"]
        record.notify_status = ctx["notify_status"]
        record.notify_reason = ctx["notify_reason"]
        record.tags = ctx["tags"]
        return record

    logging.setLogRecordFactory(record_factory)
    _factory_installed = True

