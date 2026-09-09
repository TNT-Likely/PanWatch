"""Compatibility module alias; implementation moved to platform.events."""
import sys
from src.platform.events import sse as _implementation
sys.modules[__name__] = _implementation
