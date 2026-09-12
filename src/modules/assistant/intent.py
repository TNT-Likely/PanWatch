"""Small, deterministic intent gate for PanWatch write actions.

The generic runtime deliberately does not know what a business action looks
like.  PanWatch only needs a conservative gate for its current price-alert
write tools: clear execution verbs enter required-tool mode, while questions
about how an operation works remain ordinary assistant queries.
"""

from __future__ import annotations

import re


_WRITE_VERB_PATTERN = re.compile(
    r"(?:创建|新增|修改|更新|删除|重命名|设置|启用|停用|改成|调整|取消)"
)
_WRITE_TARGET_PATTERN = re.compile(
    r"(?:提醒|价格提醒|price\s*alert|alert|标题|名称|名字|规则|阈值|"
    r"触发条件|配置|设置|#\s*\d+|这条|该条|这个)",
    re.IGNORECASE,
)
_QUESTION_PATTERN = re.compile(
    r"(?:怎么|如何|怎样|为什么|是否|能否|可不可以|要不要|行不行|"
    r"请问|方法|教程|分析|解释)"
)
_EXPLICIT_ACTION_PATTERN = re.compile(r"(?:帮我|请直接|替我|直接|执行|把|将)")


def is_write_intent(content: str) -> bool:
    """Return whether the user clearly asks to mutate a price alert."""
    text = " ".join((content or "").strip().split())
    if not text:
        return False
    if not _WRITE_VERB_PATTERN.search(text) or not _WRITE_TARGET_PATTERN.search(text):
        return False
    if _QUESTION_PATTERN.search(text) and not _EXPLICIT_ACTION_PATTERN.search(text):
        return False
    return True
