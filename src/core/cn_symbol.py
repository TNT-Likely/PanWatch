"""Compatibility alias for market symbol utilities."""

import sys

from src.modules.market import cn_symbol as _implementation

sys.modules[__name__] = _implementation
