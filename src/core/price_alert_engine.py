"""Compatibility alias for market alert evaluation."""

import sys

from src.modules.market import price_alert_engine as _implementation

sys.modules[__name__] = _implementation
