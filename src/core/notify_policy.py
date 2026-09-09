"""Compatibility alias for notification policy."""

import sys

from src.platform.notifications import notify_policy as _implementation

sys.modules[__name__] = _implementation
