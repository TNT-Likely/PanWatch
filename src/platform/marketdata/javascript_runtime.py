"""Initialize AkShare's JavaScript engine before background workers start."""

import logging
from threading import Lock

logger = logging.getLogger(__name__)
_warmup_lock = Lock()
_ready = False


def warmup_javascript_runtime() -> None:
    """Create the first V8 isolate serially; subsequent contexts can run in parallel."""
    global _ready
    with _warmup_lock:
        if _ready:
            return
        try:
            from py_mini_racer import MiniRacer
        except ImportError:
            # Some deployments omit optional AkShare/JavaScript data sources.
            logger.info("MiniRacer 未安装，跳过行情 JavaScript 引擎预热")
            return
        # init_mini_racer alone does not exercise lazy first-isolate setup.
        # AkShare installs py-mini-racer 0.6.0, whose contexts have neither
        # __enter__ nor close(). The maintained mini-racer package provides
        # close(); release it explicitly while retaining legacy GC cleanup.
        try:
            context = MiniRacer()
        except RuntimeError as exc:
            # The legacy source distribution has no native binary on ARM.
            # Optional JS data sources must not prevent the server starting.
            if str(exc).startswith("Native library not available at "):
                logger.warning("MiniRacer 原生库不可用，跳过行情 JavaScript 引擎预热: %s", exc)
                return
            raise
        try:
            context.eval("1 + 1")
        finally:
            close = getattr(context, "close", None)
            if callable(close):
                close()
            del context
        _ready = True
        logger.info("行情 JavaScript 引擎预热完成")
