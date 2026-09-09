"""Compatibility module alias; implementation moved to modules.research."""
import sys
from src.modules.research import context_builder as _implementation
sys.modules[__name__] = _implementation
