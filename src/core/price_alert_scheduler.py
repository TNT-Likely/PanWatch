"""Compatibility alias for market alert scheduling."""

import sys

from src.modules.market import price_alert_scheduler as _implementation

sys.modules[__name__] = _implementation
