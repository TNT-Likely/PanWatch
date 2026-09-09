"""Compatibility alias for market kline context."""

import sys

from src.modules.market import kline_context as _implementation

sys.modules[__name__] = _implementation
