"""Compatibility alias for observability telemetry."""

import sys

from src.platform.observability import otel as _implementation

sys.modules[__name__] = _implementation
