"""Compatibility module alias; implementation moved to modules.strategy."""
import sys
from src.modules.strategy import entry_candidates as _implementation
sys.modules[__name__] = _implementation
