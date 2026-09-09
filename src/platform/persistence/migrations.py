"""Versioned migration facade for module-owned schema additions."""

from src.web.migrations import has_pending_migrations, run_versioned_migrations

__all__ = ["has_pending_migrations", "run_versioned_migrations"]

