"""Compatibility module alias; implementation moved to modules.strategy."""
import sys
from src.modules.strategy import quant_adapters as _implementation
sys.modules[__name__] = _implementation
