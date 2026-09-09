"""Compatibility alias for market data collection."""

import sys

from src.modules.market import data_collector as _implementation

sys.modules[__name__] = _implementation
