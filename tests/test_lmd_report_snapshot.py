"""产业周期视角报告快照解析测试。"""

from src.core.lmd_report_snapshot import (
    attach_lmd_snapshot_to_raw_data,
    extract_lmd_report_snapshot,
    snapshot_from_dict,
)


BERTLI_SNIPPET = """
### 1. 基本面 —— ✅ 趋势向上，但短期换挡

**收入端**：2025年全年营收120.14亿，同比+20.91%。

**利润端**：2025年归母净利润13.09亿，同比+8.32%，2026年Q1归母净利润2.69亿，同比-0.64%。

2025年对应EPS约1.46元。

### 2. 估值 —— ⚠️ 中性合理，不算便宜不算贵

当前PE（TTM）18.97倍，现价27.64元，总市值248.11亿。按2026年分析师一致预期净利润约16.49亿计算，前瞻PE约15倍。

**估值小结**：18.97倍PE，不能说便宜，但处于合理偏低区间。我给**估值55分——不算便宜但也不贵，等待催化修复估值。**

### 3. 预期差 —— ✅ 存在于EMB量产和并购协同

**预期差评分75分——预期差方向向上且幅度可观，但需要时间发酵。**
"""


HUANGHE_SNIPPET = """
### 维度A：基本面

| 连续亏损 | 2025年-9.50亿 | ❌ 极差 |

### 维度C：估值

- **PB 53倍**——每股净资产0.32元，股价18.32元
- **PE为负**（-30.05），根本没法算PE

**评价**：极端估值。估值评分：**⚠️ 提前定价2年，偏离基本面极大**
"""


def test_extract_bertli_valuation_snapshot():
    """伯特利样例应解析出 PE、前瞻 PE、净利增速与估值分。"""
    snap = extract_lmd_report_snapshot(BERTLI_SNIPPET, report_date="2026-06-22")
    assert snap.pe_ttm == 18.97
    assert snap.forward_pe == 15.0
    assert snap.profit_yoy_pct == 8.32
    assert snap.revenue_yoy_pct == 20.91
    assert snap.consensus_eps == 1.46
    assert snap.valuation_score == 55
    assert snap.expectation_hint is not None
    assert "向上" in snap.expectation_hint
    assert snap.has_metrics() is True


def test_extract_huanghe_negative_pe_and_pb():
    """黄河旋风样例应解析 PB 与负 PE。"""
    snap = extract_lmd_report_snapshot(HUANGHE_SNIPPET)
    assert snap.pb == 53.0
    assert snap.pe_ttm == -1.0
    assert snap.has_metrics() is True


def test_empty_markdown_returns_no_metrics():
    """空正文应无法解析出指标。"""
    snap = extract_lmd_report_snapshot("")
    assert snap.has_report is False
    assert snap.has_metrics() is False


def test_attach_lmd_snapshot_to_raw_data():
    """入库时应把结构化快照写入 raw_data。"""
    raw = attach_lmd_snapshot_to_raw_data({}, BERTLI_SNIPPET, report_date="2026-06-22")
    cached = raw.get("lmd_snapshot") or {}
    assert cached.get("pe_ttm") == 18.97
    assert cached.get("has_report") is True


def test_snapshot_from_dict_roundtrip():
    """raw_data 缓存应可还原为快照对象。"""
    raw = attach_lmd_snapshot_to_raw_data({}, BERTLI_SNIPPET, report_date="2026-06-22")
    snap = snapshot_from_dict(raw["lmd_snapshot"], report_date="2026-06-22")
    assert snap.pe_ttm == 18.97
    assert snap.revenue_yoy_pct == 20.91
