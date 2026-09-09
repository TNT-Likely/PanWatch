"""Compatibility module alias; implementation moved to modules.strategy."""
import sys
from src.modules.strategy import factor_calibration as _implementation
sys.modules[__name__] = _implementation
