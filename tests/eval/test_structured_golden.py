"""structured_output 解析 golden set 全量回归（纯规则，随 make test 常跑）。"""

import pytest

from tests.eval.cases.structured_cases import STRUCTURED_CASES, check_structured_case


@pytest.mark.parametrize("case", STRUCTURED_CASES, ids=[c.id for c in STRUCTURED_CASES])
def test_structured_golden_case(case):
    """结构化输出解析 golden set 用例逐条回归"""
    failures = check_structured_case(case)
    assert failures == [], f"[{case.id}] {case.notes}: {failures}"
