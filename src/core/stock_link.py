"""Compatibility alias for administration stock-link utilities."""

import sys

from src.modules.administration import stock_link as _implementation

sys.modules[__name__] = _implementation
