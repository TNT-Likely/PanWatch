"""structured_output 解析 golden set（纯规则，无需模型，随 make test 常跑）。

复用并扩充 tests/test_structured_output.py 的既有资产：
围栏/前缀容错、别名归一、动作白名单拒绝、标签块提取/剥离的边界情况。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.signals.structured_output import (
    TAG_END,
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
    try_parse_action_json,
)


@dataclass
class StructuredEvalCase:
    """一条结构化输出解析用例。

    kind:
    - action: try_parse_action_json（JSON-only 输出）
    - tagged: try_extract_tagged_json（长文末尾标签块）
    - strip:  strip_tagged_json（剥离标签块后的正文）
    """

    id: str
    text: str
    kind: str = "action"
    expect_parsed: bool = True
    # 字段断言：值为普通值时做相等比较，为 callable 时做谓词校验
    expect_fields: dict = field(default_factory=dict)
    # kind=strip 时的期望正文
    expect_stripped: str | None = None
    notes: str = ""


def check_structured_case(case: StructuredEvalCase) -> list[str]:
    """跑一条用例，返回失败原因列表（空即通过）。"""
    failures: list[str] = []

    if case.kind == "strip":
        actual = strip_tagged_json(case.text)
        if case.expect_stripped is not None and actual != case.expect_stripped:
            failures.append(f"剥离结果不符: 期望 {case.expect_stripped!r}, 实际 {actual!r}")
        return failures

    if case.kind == "tagged":
        obj = try_extract_tagged_json(case.text)
    else:
        obj = try_parse_action_json(case.text)

    if case.expect_parsed and obj is None:
        failures.append("期望解析成功，实际返回 None")
        return failures
    if not case.expect_parsed:
        if obj is not None:
            failures.append(f"期望解析失败(None)，实际得到 {obj!r}")
        return failures

    for key, expected in case.expect_fields.items():
        actual = obj.get(key)
        if callable(expected):
            if not expected(actual):
                failures.append(f"字段 {key} 校验失败: 实际 {actual!r}")
        elif actual != expected:
            failures.append(f"字段 {key} 不符: 期望 {expected!r}, 实际 {actual!r}")
    return failures


STRUCTURED_CASES: list[StructuredEvalCase] = [
    # ──────── try_parse_action_json ────────
    StructuredEvalCase(
        id="s-json-prefix",
        text='\njson\n{"action":"add","action_label":"建仓","reason":"突破"}\n',
        expect_fields={"action": "add", "action_label": "建仓"},
        notes="裸 json 前缀行容错",
    ),
    StructuredEvalCase(
        id="s-fenced-json",
        text='```json\n{"action":"reduce","action_label":"减仓"}\n```',
        expect_fields={"action": "reduce"},
        notes="```json 代码围栏容错",
    ),
    StructuredEvalCase(
        id="s-fenced-nolang",
        text='```\n{"action":"hold","confidence":0.7}\n```',
        expect_fields={"action": "hold", "confidence": 0.7},
        notes="无语言标注的代码围栏",
    ),
    StructuredEvalCase(
        id="s-alias-build",
        text='{"action":"build","action_label":"建仓"}',
        expect_fields={"action": "add"},
        notes="build 别名归一化为 add",
    ),
    StructuredEvalCase(
        id="s-action-upper",
        text='{"action":"ADD","action_label":"建仓"}',
        expect_fields={"action": lambda v: str(v).lower() == "add"},
        notes="大写 action 通过白名单校验（保留原大小写）",
    ),
    StructuredEvalCase(
        id="s-illegal-action",
        text='{"action":"yolo","reason":"梭哈"}',
        expect_parsed=False,
        notes="白名单外动作必须拒绝",
    ),
    StructuredEvalCase(
        id="s-json-array",
        text='[{"action":"add"}]',
        expect_parsed=False,
        notes="非 dict（数组）必须拒绝",
    ),
    StructuredEvalCase(
        id="s-empty",
        text="",
        expect_parsed=False,
        notes="空输入返回 None",
    ),
    StructuredEvalCase(
        id="s-broken-json",
        text='{"action":"add",',
        expect_parsed=False,
        notes="截断 JSON 返回 None 而非抛异常",
    ),
    StructuredEvalCase(
        id="s-no-action-field",
        text='{"signal":"volume_spike","note":"放量"}',
        expect_fields={"signal": "volume_spike"},
        notes="无 action 字段的合法 JSON 允许通过（action 为空不校验白名单）",
    ),
    StructuredEvalCase(
        id="s-prose-not-json",
        text="今天大盘震荡，建议观望。",
        expect_parsed=False,
        notes="纯自然语言返回 None",
    ),
    # ──────── try_extract_tagged_json ────────
    StructuredEvalCase(
        id="t-tagged-ok",
        text=f'前面是分析正文。\n{TAG_START}\n{{"action":"watch","score":72}}\n{TAG_END}',
        kind="tagged",
        expect_fields={"action": "watch", "score": 72},
        notes="长文末尾标签块提取",
    ),
    StructuredEvalCase(
        id="t-tagged-take-last",
        text=(
            f'{TAG_START}\n{{"v":1}}\n{TAG_END}\n中间正文\n'
            f'{TAG_START}\n{{"v":2}}\n{TAG_END}'
        ),
        kind="tagged",
        expect_fields={"v": 2},
        notes="多个标签块取最后一个（rfind）",
    ),
    StructuredEvalCase(
        id="t-tagged-missing-end",
        text=f'正文\n{TAG_START}\n{{"v":1}}',
        kind="tagged",
        expect_parsed=False,
        notes="缺结束标签返回 None",
    ),
    StructuredEvalCase(
        id="t-tagged-empty-payload",
        text=f"正文\n{TAG_START}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="空 payload 返回 None",
    ),
    StructuredEvalCase(
        id="t-tagged-broken-payload",
        text=f"正文\n{TAG_START}\n{{bad json}}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="标签内非法 JSON 返回 None",
    ),
    # ──────── strip_tagged_json ────────
    StructuredEvalCase(
        id="strip-ok",
        text=f'结论正文。\n{TAG_START}\n{{"action":"hold"}}\n{TAG_END}',
        kind="strip",
        expect_stripped="结论正文。",
        notes="剥离标签块只留正文",
    ),
    StructuredEvalCase(
        id="strip-no-tag",
        text="没有标签块的正文",
        kind="strip",
        expect_stripped="没有标签块的正文",
        notes="无标签块原样返回",
    ),
]
