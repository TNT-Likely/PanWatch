"""Compatibility alias for notification deduplication."""

import sys

from src.platform.notifications import notify_dedupe as _implementation

sys.modules[__name__] = _implementation
