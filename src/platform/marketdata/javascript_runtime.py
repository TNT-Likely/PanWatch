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
        with MiniRacer() as context:
            context.eval("1 + 1")
        _ready = True
        logger.info("行情 JavaScript 引擎预热完成")
