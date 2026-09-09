"""Transitional facade for notification channels."""

from src.core.notifier import NotifierManager, get_global_proxy, sanitize_for_telegram

__all__ = ["NotifierManager", "get_global_proxy", "sanitize_for_telegram"]

