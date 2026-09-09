"""Compatibility alias for the platform notification implementation."""

import sys

from src.platform.notifications import notifier as _implementation

sys.modules[__name__] = _implementation
