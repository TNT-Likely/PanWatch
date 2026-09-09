"""Compatibility alias for platform persistence ORM models."""

import sys

from src.platform.persistence import models as _implementation

sys.modules[__name__] = _implementation
