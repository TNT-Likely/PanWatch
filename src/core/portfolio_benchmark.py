"""Compatibility alias for portfolio benchmark services."""

import sys

from src.modules.portfolio import portfolio_benchmark as _implementation

sys.modules[__name__] = _implementation
