"""监管红线检测：警示函等 S 级事件应一票否决建仓/加仓建议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RegulatoryTier(str, Enum):
    S = "S"  # 红线：默认禁止建仓/加仓
    A = "A"  # 重大利空：原则上不新开仓
    NONE = "none"


# S 级：监管红线，权重远高于技术面
S_TIER_KEYWORDS: tuple[str, ...] = (
    "警示函",
    "监管函",
    "立案调查",
    "立案告知",
    "证监会立案",
    "被立案调查",
    "财务造假",
    "退市风险",
    "退市风险警示",
    "可能被实施退市",
    "重大违法",
    "无法表示意见",
    "否定意见",
    "非标审计",
    "保留意见",
)

# A 级：重大利空，显著降权
A_TIER_KEYWORDS: tuple[str, ...] = (
    "问询函",
    "关注函",
    "业绩预亏",
    "业绩大幅下滑",
    "计提减值",
    "重大诉讼",
    "实控人被调查",
    "被采取强制措施",
    "取保候审",
    "收到行政处罚",
)

TIER_LABELS = {
    RegulatoryTier.S: "监管红线(S级)",
    RegulatoryTier.A: "重大利空(A级)",
}


@dataclass
class RegulatoryHit:
    tier: RegulatoryTier
    keyword: str
    title: str
    time: str = ""


@dataclass
class RegulatoryScanResult:
    hits: list[RegulatoryHit] = field(default_factory=list)

    @property
    def max_tier(self) -> RegulatoryTier:
        if any(h.tier == RegulatoryTier.S for h in self.hits):
            return RegulatoryTier.S
        if any(h.tier == RegulatoryTier.A for h in self.hits):
            return RegulatoryTier.A
        return RegulatoryTier.NONE

    @property
    def has_fatal(self) -> bool:
        return self.max_tier == RegulatoryTier.S

    @property
    def has_major(self) -> bool:
        return self.max_tier in (RegulatoryTier.S, RegulatoryTier.A)


def _match_tier(text: str) -> tuple[RegulatoryTier, str] | None:
    for kw in S_TIER_KEYWORDS:
        if kw in text:
            return RegulatoryTier.S, kw
    for kw in A_TIER_KEYWORDS:
        if kw in text:
            return RegulatoryTier.A, kw
    return None


def scan_text(text: str, *, title: str = "", time: str = "") -> RegulatoryHit | None:
    """扫描单段文本，返回最高优先级命中（S 优先于 A）。"""
    blob = f"{title} {text}".strip()
    if not blob:
        return None
    matched = _match_tier(blob)
    if not matched:
        return None
    tier, kw = matched
    return RegulatoryHit(tier=tier, keyword=kw, title=(title or blob[:80]), time=time)


def scan_items(items: list) -> RegulatoryScanResult:
    """扫描新闻/公告列表（NewsItem 或含 title/content/publish_time 的 dict）。"""
    hits: list[RegulatoryHit] = []
    seen: set[tuple[str, str, str]] = set()

    for it in items:
        if isinstance(it, dict):
            title = str(it.get("title") or "")
            content = str(it.get("content") or "")
            time_val = it.get("publish_time") or it.get("time") or ""
            time_str = str(time_val) if time_val else ""
        else:
            title = str(getattr(it, "title", "") or "")
            content = str(getattr(it, "content", "") or "")
            pt = getattr(it, "publish_time", None)
            time_str = pt.strftime("%m-%d %H:%M") if pt else ""

        hit = scan_text(content, title=title, time=time_str)
        if not hit:
            continue
        key = (hit.tier.value, hit.keyword, hit.title[:60])
        if key in seen:
            continue
        seen.add(key)
        hits.append(hit)

    # S 级排前
    hits.sort(key=lambda h: (0 if h.tier == RegulatoryTier.S else 1, h.title))
    return RegulatoryScanResult(hits=hits)


def format_ai_context(result: RegulatoryScanResult) -> str:
    """格式化为注入 AI 的醒目上下文块。"""
    if not result.hits:
        return ""

    lines = [f"- [{TIER_LABELS[h.tier]}·{h.keyword}] {h.title}" + (f"（{h.time}）" if h.time else "") for h in result.hits[:5]]
    header = "【监管风险警报】"
    if result.has_fatal:
        header += " 检测到监管红线，默认禁止建仓/加仓，优先级高于技术面与估值"
    elif result.has_major:
        header += " 检测到重大利空监管/业绩事件，原则上不新开仓"
    return header + "\n" + "\n".join(lines)


REGULATORY_VETO_PROMPT = """
监管红线规则（必须遵守，优先级高于技术面、缠论、产业周期估值）：
- S级（警示函、监管函、立案调查、退市风险、财务造假/非标审计等）：空仓默认「不适合建仓/加仓」；已持仓默认暂停加仓，优先考虑减仓或观望
- A级（问询函、关注函、业绩大幅预亏、重大诉讼等）：原则上不新开仓，加仓需极强理由
- 理由第一条必须说明监管/合规风险；不得因「技术面好转」推翻 S 级否决
- 若「当前数据」中已有【监管风险警报】，必须据此作答，不得忽略"""


def event_bias_for_text(text: str) -> float:
    """供策略引擎使用的消息面偏置增量。"""
    hit = scan_text(text)
    if not hit:
        return 0.0
    if hit.tier == RegulatoryTier.S:
        return -8.0
    if hit.tier == RegulatoryTier.A:
        return -3.0
    return 0.0
