"""Compatibility module alias; implementation moved to platform.marketdata."""
import sys
from src.platform.marketdata import marketdata_client as _implementation
sys.modules[__name__] = _implementation
