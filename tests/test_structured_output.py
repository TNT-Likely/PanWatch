from src.modules.research.signals.structured_output import try_parse_action_json


def test_try_parse_action_json_plain_json_prefix() -> None:
    """LLM 输出解析 — json 前缀格式"""
    text = '\njson\n{"action":"add","action_label":"建仓","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
    assert obj.get("action_label") == "建仓"


def test_try_parse_action_json_fenced_json() -> None:
    """LLM 输出解析 — 代码块格式"""
    text = '```json\n{"action":"reduce","action_label":"减仓"}\n```'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "reduce"


def test_try_parse_action_json_action_alias_build_to_add() -> None:
    """LLM 输出解析 — build 别名自动映射为 add"""
    text = '\njson\n{"action":"build","action_label":"建仓","reason":"突破"}\n'
    obj = try_parse_action_json(text)
    assert obj is not None
    assert obj.get("action") == "add"
