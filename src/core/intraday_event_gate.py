"""Compatibility alias for strategy intraday event gating."""

import sys

from src.modules.strategy import intraday_event_gate as _implementation

sys.modules[__name__] = _implementation
