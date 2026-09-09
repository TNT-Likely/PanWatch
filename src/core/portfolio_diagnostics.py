"""Compatibility alias for portfolio diagnostics."""

import sys

from src.modules.portfolio import portfolio_diagnostics as _implementation

sys.modules[__name__] = _implementation
