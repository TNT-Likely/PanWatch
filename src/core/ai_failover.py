"""Compatibility module alias; implementation moved to platform.ai."""
import sys
from src.platform.ai import ai_failover as _implementation
sys.modules[__name__] = _implementation
