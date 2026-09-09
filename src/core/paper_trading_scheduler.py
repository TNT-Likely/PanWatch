"""Compatibility module alias; implementation moved to modules.paper_trading."""
import sys
from src.modules.paper_trading import paper_trading_scheduler as _implementation
sys.modules[__name__] = _implementation
