"""Compatibility alias for scheduling trading-calendar utilities."""

import sys

from src.platform.scheduling import trading_calendar as _implementation

sys.modules[__name__] = _implementation
