"""Compatibility alias for observability log context."""

import sys

from src.platform.observability import log_context as _implementation

sys.modules[__name__] = _implementation
