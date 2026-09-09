"""Compatibility alias for market news ranking."""

import sys

from src.modules.market import news_ranker as _implementation

sys.modules[__name__] = _implementation
