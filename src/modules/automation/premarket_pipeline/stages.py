"""盘前决策流水线 阶段1-3:宏观三卡 / 板块预测 / 选股三价。

设计约定:
- 全部阈值来自 ``premarket_config``(唯一事实源),本模块不散落魔法数;
- 外部通道 fail-soft:失败返回空并标注 available/level,绝不编造;
- LLM 输出走 ``try_extract_tagged_json`` 结构化解析,失败重试 1 次并注入失败原因;
- 三卡 position_policy.diff 的 relax 判定是**结构化 JSON 校验**,不做文本子串扫描;
- 阻塞 IO(akshare/行情通道)一律经 ``asyncio.to_thread`` 离开事件循环
  (sector 初筛在 ``run_sector_stage`` 内部包 to_thread),LLM 调用受
  ``llm_timeout_seconds`` 硬超时,akshare 外呼叠加限频 + 单次硬超时
  (``akshare_throttle`` / ``call_with_timeout``)。
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.premarket_pipeline.types import (
    MacroCards,
    PickCandidate,
    SectorForecast,
    SectorForecastItem,
)
from src.modules.research.signals.structured_output import try_extract_tagged_json

logger = logging.getLogger(__name__)

ChatFn = Callable[[str, str], Awaitable[str]]

#: 阶段2/3 依赖的行情数据服务(模块属性引用,测试可 monkeypatch)
_SVC_TARGET = "src.modules.market.sector_data_service"


def _svc():
    """惰性 import 行业数据服务(便于测试 monkeypatch 模块属性)。"""
    from src.modules.market import sector_data_service as ssvc

    return ssvc


def _md():
    """惰性获取 marketdata 单例(fail-soft 由调用方负责)。"""
    from src.platform.marketdata.marketdata_client import get_market_data

    return get_market_data()


# ── akshare 外呼保护:限频 + 硬超时 ──
# 同步 akshare 调用必须跑在工作线程(asyncio.to_thread / 本执行器)里;
# 这里再叠加两层保护:调用间隔限频(AKSHARE_FETCH_INTERVAL_SEC)与
# 单次硬超时(AKSHARE_FETCH_TIMEOUT_SEC,经共享线程池 future 实现)。
# 超时不关闭执行器(线程随进程退出),避免 join 挂死——泄漏上限即 max_workers。
_AKSHARE_LAST_FETCH: list[float] = [0.0]
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pm-io")


class FetchTimeout(RuntimeError):
    """单次外部抓取超时。"""


def akshare_throttle() -> None:
    """维持 akshare 调用最小间隔(限频保护,幂等可重入)。"""
    gap = pcfg.AKSHARE_FETCH_INTERVAL_SEC - (time.monotonic() - _AKSHARE_LAST_FETCH[0])
    if gap > 0:
        time.sleep(gap)
    _AKSHARE_LAST_FETCH[0] = time.monotonic()


# 兼容别名(阶段3 财务按需拉取使用)
_akshare_throttle = akshare_throttle


def call_with_timeout(fn, *args, timeout_sec: float, **kwargs):
    """在共享线程池里执行 fn 并限时;超时抛 FetchTimeout(底层线程不 join)。"""
    future = _IO_EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=max(1.0, float(timeout_sec)))
    except FuturesTimeoutError as e:
        future.cancel()
        raise FetchTimeout(
            f"{getattr(fn, '__name__', fn)} 超时(>{timeout_sec}s)"
        ) from e


def _today_str() -> str:
    from src.platform.scheduling.timezone import beijing_now

    return beijing_now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    from src.platform.scheduling.timezone import beijing_now

    return beijing_now().isoformat(timespec="seconds")


def is_trading_day_cn(d: date | None = None) -> bool:
    """CN 交易日守卫(交易日历加载失败时降级只判周末,与全仓口径一致)。"""
    from src.platform.scheduling.trading_calendar import is_trading_day

    try:
        return bool(is_trading_day("CN", d))
    except Exception as e:  # pragma: no cover - 日历异常极罕见,防御兜底
        logger.warning("[盘前流水线] 交易日判定异常,降级只判周末: %s", e)
        return (d or date.today()).weekday() < 5


def next_trading_days(n: int, start: date | None = None) -> list[date]:
    """从 start(默认今天)起(含)向后找 n 个 CN 交易日。日历不可用时按工作日近似。"""
    d = start or date.today()
    out: list[date] = []
    for _ in range(400):  # 硬上限防死循环
        if is_trading_day_cn(d):
            out.append(d)
            if len(out) >= n:
                break
        d += timedelta(days=1)
    return out


# ══════════════════════════ 采集(阶段1输入) ══════════════════════════


def _channel(name: str, ok: bool, level: int, data: Any, as_of: str = "") -> dict:
    """通道描述:available + level(0=正常,1=降级,2=不可用)。"""
    return {
        "name": name,
        "available": bool(ok),
        "level": int(level),
        "as_of": as_of or _now_iso(),
        "data": data,
    }


def _load_calendar_window(db: Session) -> list:
    """事件日历窗口:近 7 天 + 未来 5 个交易日(含边界)。"""
    from src.platform.persistence.models import EventCalendarItem

    start = (date.today() - timedelta(days=pcfg.CALENDAR_PAST_DAYS)).isoformat()
    future = next_trading_days(pcfg.CALENDAR_FUTURE_TRADING_DAYS)
    end = (future[-1] if future else date.today()).isoformat()
    return (
        db.query(EventCalendarItem)
        .filter(EventCalendarItem.event_date >= start, EventCalendarItem.event_date <= end)
        .order_by(EventCalendarItem.event_date.asc())
        .all()
    )


def _load_macro_latest(db: Session) -> list[dict]:
    """宏观指标:每指标取最新 period 一行(库内缓存,不现场拉取)。"""
    from src.platform.persistence.models import MacroIndicatorValue

    rows = db.query(MacroIndicatorValue).order_by(MacroIndicatorValue.indicator.asc()).all()
    latest: dict[str, MacroIndicatorValue] = {}
    for r in rows:
        cur = latest.get(r.indicator)
        if cur is None or str(r.period) > str(cur.period):
            latest[r.indicator] = r
    return [
        {
            "indicator": r.indicator,
            "period": r.period,
            "value": r.value,
            "publish_date": r.publish_date,
            "source": r.source,
            "as_of": r.as_of,
        }
        for r in sorted(latest.values(), key=lambda x: x.indicator)
    ]


def _load_global_indices() -> list[dict]:
    """全球指数实时(PR1 global_markets 引擎,跨源统一键)。全源失败返回 [](fail-soft)。"""
    resp = _md().global_markets()
    items = list(getattr(resp, "data", None) or [])
    out = []
    for it in items:
        out.append(
            {
                "symbol": getattr(it, "symbol", ""),
                "name": getattr(it, "name", ""),
                "price": getattr(it, "price", None),
                "change_pct": getattr(it, "change_pct", None),
                "source_tag": getattr(it, "source_tag", ""),
            }
        )
    return out


def _load_us_overnight() -> list[dict]:
    """隔夜美股(现有 index_quotes 腾讯通道)。失败返回 [](fail-soft)。"""
    rows = _md().index_quotes(["usDJI", "usIXIC", "usINX"]) or []
    return [
        {
            "name": r.get("name") or r.get("symbol"),
            "current": r.get("current_price"),
            "change_pct": r.get("change_pct"),
        }
        for r in rows
    ]


def collect_channels(db: Session) -> dict:
    """阶段1 输入采集:四通道带 available/level,全部 fail-soft。

    channels: calendar(事件日历窗口)/macro(宏观指标)/global(全球指数实时)/
    us_indices(隔夜美股)。events_recent 为日历近 3 日切片。
    """
    rows = _load_calendar_window(db)
    cal_items = [
        {
            "event_date": str(r.event_date),
            "level": r.level,
            "name": r.name,
            "scope": r.scope or "",
            "expected": r.expected or "",
            "actual": r.actual or "",
            "direction": r.direction or "",
            "impact_boards": r.impact_boards or [],
        }
        for r in rows
    ]
    cutoff_recent = (date.today() - timedelta(days=pcfg.EVENTS_RECENT_DAYS)).isoformat()
    events_recent = [c for c in cal_items if c["event_date"] >= cutoff_recent]

    macro_rows: list[dict] = []
    try:
        macro_rows = _load_macro_latest(db)
    except Exception as e:
        logger.warning("[盘前流水线] 宏观指标读取失败: %s", e)

    global_rows: list[dict] = []
    try:
        global_rows = _load_global_indices()
    except Exception as e:
        logger.warning("[盘前流水线] 全球指数采集失败: %s", e)

    us_rows: list[dict] = []
    try:
        us_rows = _load_us_overnight()
    except Exception as e:
        logger.warning("[盘前流水线] 隔夜美股采集失败: %s", e)

    return {
        "today": _today_str(),
        "calendar": _channel("calendar", bool(cal_items), 0 if cal_items else 2, cal_items),
        "macro": _channel("macro", bool(macro_rows), 0 if macro_rows else 2, macro_rows),
        "global": _channel("global", bool(global_rows), 0 if global_rows else 2, global_rows),
        "us_indices": _channel("us_indices", bool(us_rows), 0 if us_rows else 2, us_rows),
        "events_recent": events_recent,
    }


# ══════════════════════════ 阶段1 宏观三卡 ══════════════════════════


def macro_gates(channels: dict) -> dict:
    """阶段1 门禁判定(纯函数,可单测)。

    - 日历窗口空 → STOP(硬停,不进后续阶段);
    - 可用通道 < 2 → 只产数据快照报告(snapshot_only);
    - 事件/海外通道弱 → 三卡置信度上限 4。
    """
    cal = channels.get("calendar") or {}
    available = [
        c for c in
        (channels.get("calendar"), channels.get("macro"), channels.get("global"), channels.get("us_indices"))
        if c and c.get("available")
    ]
    weak = (not (channels.get("calendar") or {}).get("available")) or (
        not (channels.get("global") or {}).get("available")
    )
    if not cal.get("available"):
        return {
            "status": "stopped",
            "confidence_cap": None,
            "reason": "事件日历窗口为空(近7日+未来5交易日无任何事件)。"
            "请先在「事件日历」导入数据后再触发本流水线。",
        }
    if len(available) < pcfg.MIN_AVAILABLE_CHANNELS:
        return {
            "status": "snapshot_only",
            "confidence_cap": None,
            "reason": f"可用通道仅 {len(available)} 个(<{pcfg.MIN_AVAILABLE_CHANNELS}),"
            "只产数据快照报告。",
        }
    return {
        "status": "ok",
        "confidence_cap": pcfg.WEAK_CHANNEL_CONF_CAP if weak else None,
        "reason": "事件或海外通道弱,三卡置信度封顶" if weak else "",
    }


def build_macro_user_content(channels: dict) -> str:
    """阶段1 user content:数据快照按通道分节(空通道显式标注缺失)。"""
    lines = [f"## 日期:{channels.get('today') or _today_str()} 盘前"]
    cal = (channels.get("calendar") or {})
    if cal.get("available"):
        lines.append("## 事件日历窗口(近7日+未来5交易日)")
        for it in cal.get("data") or []:
            actual = f" 实际:{it['actual']}" if it.get("actual") else ""
            expect = f" 预期:{it['expected']}" if it.get("expected") else ""
            boards = ",".join(it.get("impact_boards") or [])
            boards_txt = f" 影响板块:{boards}" if boards else ""
            lines.append(
                f"- [{it['event_date']}][{it['level']}] {it['name']}"
                f"({it.get('scope') or '全球'}){expect}{actual}"
                f" 方向:{it.get('direction') or 'neutral'}{boards_txt}"
            )
    else:
        lines.append("## 事件日历窗口:数据缺失(STOP 门禁应已拦截)")
    macro = channels.get("macro") or {}
    if macro.get("available"):
        lines.append("## 宏观指标(库内最新期)")
        for m in macro.get("data") or []:
            val = m.get("value")
            val_txt = f"{val}" if val is not None else "缺失"
            lines.append(
                f"- {m['indicator']}({m['period']}):{val_txt}"
                f" 发布:{m.get('publish_date') or '未知'} 来源:{m.get('source') or '未知'}"
            )
    else:
        lines.append("## 宏观指标:数据缺失(通道不可用)")
    glob = channels.get("global") or {}
    if glob.get("available"):
        lines.append("## 全球指数(实时,口径:实时快照)")
        for g in glob.get("data") or []:
            chg = g.get("change_pct")
            chg_txt = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "N/A"
            lines.append(f"- {g.get('name') or g.get('symbol')}:{g.get('price')} {chg_txt}")
    else:
        lines.append("## 全球指数:数据缺失(通道不可用)")
    us = channels.get("us_indices") or {}
    if us.get("available"):
        lines.append("## 隔夜美股(T-1 美东收盘口径)")
        for u in us.get("data") or []:
            chg = u.get("change_pct")
            chg_txt = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "N/A"
            lines.append(f"- {u.get('name')}:{u.get('current')} {chg_txt}")
    else:
        lines.append("## 隔夜美股:数据缺失(通道不可用)")
    lines.append("\n请按系统指令输出宏观三卡(含输出契约 JSON)。")
    return "\n".join(lines)


def _cards_check_errors(payload: dict) -> list[str]:
    """三卡 payload 的结构化校验:shape 错误列表 + relax 判定(禁止文本扫描)。

    规则:
    - cards 必须是非空数组,每卡含 title/direction/confidence/rationale/position_policy;
    - position_policy.diff 必须为数组,每项为含 item/direction/from/to 的对象,
      direction ∈ {tighten, relax};
    - 任一 relax 项 → 返回 "relax_found"(废卡重写信号)。
    """
    errors: list[str] = []
    cards = payload.get("cards")
    if not isinstance(cards, list) or not cards:
        return ["cards 必须是非空数组"]
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            errors.append(f"cards[{i}] 必须是对象")
            continue
        for key in ("title", "direction", "confidence", "rationale", "position_policy"):
            if key not in card:
                errors.append(f"cards[{i}] 缺少字段 {key}")
        policy = card.get("position_policy")
        if isinstance(policy, dict):
            diff = policy.get("diff")
            if not isinstance(diff, list):
                errors.append(f"cards[{i}].position_policy.diff 必须是数组")
            else:
                for j, item in enumerate(diff):
                    if not isinstance(item, dict):
                        errors.append(f"cards[{i}].diff[{j}] 必须是对象")
                        continue
                    for key in ("item", "direction", "from", "to"):
                        if key not in item:
                            errors.append(f"cards[{i}].diff[{j}] 缺少字段 {key}")
                    if item.get("direction") not in pcfg.POLICY_DIFF_DIRECTIONS:
                        errors.append(
                            f"cards[{i}].diff[{j}].direction 非法:"
                            f"{item.get('direction')!r}(只允许 tighten/relax)"
                        )
        elif policy is not None:
            errors.append(f"cards[{i}].position_policy 必须是对象")
    if any("direction 非法" in e or ".direction" in e and "relax" in e for e in errors):
        pass  # direction 非法与 relax 是两类错误,都保留
    if _has_relax(payload):
        errors.append("relax_found")
    return errors


def _has_relax(payload: dict) -> bool:
    """结构化 relax 判定:遍历 diff 数组的 direction 字段(非文本扫描)。"""
    for card in payload.get("cards") or []:
        if not isinstance(card, dict):
            continue
        policy = card.get("position_policy")
        if not isinstance(policy, dict):
            continue
        for item in policy.get("diff") or []:
            if isinstance(item, dict) and item.get("direction") == "relax":
                return True
    return False


def _apply_confidence_cap(cards: MacroCards) -> None:
    cap = cards.confidence_cap
    if cap is None:
        return
    for card in cards.cards:
        try:
            conf = float(card.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0
        if conf > cap:
            card["confidence"] = cap
            card.setdefault("annotations", []).append(f"通道弱,置信度封顶{cap}")


async def run_macro_stage(
    channels: dict,
    chat_fn: ChatFn,
    *,
    llm_timeout_seconds: int,
) -> MacroCards:
    """阶段1:门禁 → prompt(配置注入) → chat → 结构化解析(失败重试1次)。"""
    gate = macro_gates(channels)
    cards = MacroCards(
        status=gate["status"],
        confidence_cap=gate.get("confidence_cap"),
        gate_reason=gate.get("reason") or "",
    )
    if gate["status"] == "stopped":
        cards.sources = [_src_snapshot(channels)]
        return cards

    system_prompt = pcfg.render_prompt(pcfg.PROMPT_MACRO)
    user_content = build_macro_user_content(channels)
    if gate["status"] == "snapshot_only":
        # 只产数据快照报告:不做三卡推理,保留快照本身
        cards.sources = [_src_snapshot(channels)]
        return cards

    failure_note = ""
    payload: dict | None = None
    for attempt in range(1, pcfg.CARD_REWRITE_MAX_ATTEMPTS + 1):
        user = user_content
        if failure_note:
            user = (
                user
                + f"\n\n## 上一次输出解析失败原因(必须修正)\n{failure_note}\n"
                + "请严格按输出契约重写:三卡 JSON 必须含 position_policy.diff 数组,"
                "diff 项 direction 只能是 tighten 或 relax;若真需要放松立场,说明证据后"
                "整体降低卡片置信度,而不是输出 relax。"
            )
        try:
            content = await asyncio.wait_for(
                chat_fn(system_prompt, user), timeout=llm_timeout_seconds
            )
        except (asyncio.TimeoutError, Exception) as e:  # noqa: B014 - 超时与异常同样降级
            failure_note = f"LLM 调用失败({type(e).__name__}: {e})"
            logger.warning("[盘前流水线][阶段1] LLM 调用失败: %s", e)
            continue
        payload = try_extract_tagged_json(content)
        if payload is None:
            failure_note = "未找到有效的 <!--PANWATCH_JSON--> 标签或 JSON 解析失败"
            continue
        errors = _cards_check_errors(payload)
        if not errors:
            break
        if errors == ["relax_found"] or "relax_found" in errors:
            failure_note = (
                "position_policy.diff 中出现 relax(放松风控)项。风控立场只允许收紧;"
                "如证据支持更积极,请整体提高置信度并保持 diff 全部为 tighten。"
            )
            payload = None
            continue
        failure_note = ";".join(errors[:8])

    if payload is None:
        cards.status = "llm_failed"
        cards.gate_reason = cards.gate_reason or "三卡 LLM 输出解析失败(已重试),降级为数据快照。"
        cards.sources = [_src_snapshot(channels)]
        return cards

    cards.cards = payload.get("cards") or []
    for card in cards.cards:
        card.setdefault("annotations", [])
        if isinstance(card.get("confidence"), (int, float)) and card["confidence"] > 10:
            card["confidence"] = 10.0
    _apply_confidence_cap(cards)
    cards.raw_payload = payload
    cards.sources = [_src_snapshot(channels), {"name": "llm_macro_cards", "ok": True, "degrade_level": 0}]
    return cards


def _src_snapshot(channels: dict) -> dict:
    return {
        "name": "data_snapshot",
        "ok": True,
        "degrade_level": 0 if all(
            (channels.get(k) or {}).get("available")
            for k in ("calendar", "macro", "global", "us_indices")
        ) else 1,
    }


# ══════════════════════════ 阶段2 板块预测 ══════════════════════════


def classify_stage(
    *,
    d5: float | None,
    d10: float | None,
    d20: float | None,
    main_inflow: float | None,
    small_inflow: float | None,
    limit_up_count: int | None,
    market_limit_up_total: int | None,
) -> str:
    """四阶段矩阵(蓄力/突破/加速/衰竭),顺序判定,无命中返回「未知」。

    - 蓄力:5日 < 2% 且 20日 < -5% 且 小单净流入;
    - 突破:5日 > 3% 且 主力净流入;
    - 加速:5日 > 5% 且 涨停潮(行业涨停达标);
    - 衰竭:涨幅收窄(5日动量明显弱于10日段)且 主力净流出。
    """
    wave = limit_up_count is not None and limit_up_count >= pcfg.HEAT_LIMIT_UP["industry"]
    if (
        d5 is not None and d20 is not None
        and d5 < pcfg.STAGE_MATRIX["蓄力"]["d5_max"]
        and d20 < pcfg.STAGE_MATRIX["蓄力"]["d20_min"]
        and (small_inflow or 0) > 0
    ):
        return "蓄力"
    if (
        d5 is not None and d5 > pcfg.STAGE_MATRIX["突破"]["d5_min"]
        and (main_inflow or 0) > 0
    ):
        return "突破"
    if (
        d5 is not None and d5 > pcfg.STAGE_MATRIX["加速"]["d5_min"] and wave
    ):
        return "加速"
    fading = (
        d5 is not None and d10 is not None and d5 >= 0 and d5 * 2 <= d10
    ) or (d5 is not None and d5 < 0 <= (d10 or 0))
    if fading and (main_inflow or 0) < 0:
        return "衰竭"
    return "未知"


def detect_divergence(change_pct: float | None, main_inflow_e8: float | None) -> str:
    """背离判定(单位:涨跌幅 %,主力净流入 亿)。涨>2%且主力流出>5亿=派发;跌>2%且净流入>3亿=吸筹。"""
    if change_pct is None or main_inflow_e8 is None:
        return ""
    if (
        change_pct > pcfg.DIVERGENCE["rally_pct"]
        and main_inflow_e8 < -pcfg.DIVERGENCE["rally_outflow_e8"]
    ):
        return "派发"
    if (
        change_pct < pcfg.DIVERGENCE["drop_pct"]
        and main_inflow_e8 > pcfg.DIVERGENCE["drop_inflow_e8"]
    ):
        return "吸筹"
    return ""


def _market_limit_up_total(rows) -> int | None:
    """全市场涨停总数:取任一行 meta.limit_up.official_total / self_counted_total。"""
    for r in rows:
        meta = r.meta if isinstance(r.meta, dict) else {}
        limit_up = meta.get("limit_up") or {}
        for key in ("official_total", "self_counted_total"):
            v = limit_up.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return int(v)
    return None


def screen_sectors(
    db: Session,
    *,
    snapshot_date: str,
    top_n: int,
    policy_events: list[dict] | None = None,
) -> tuple[list[SectorForecastItem], list[dict]]:
    """板块初筛(纯 Python):动量/四阶段/涨停潮/背离 → top_n。

    三级降级:快照库(0) → 实时拉 collect_daily_snapshot(1) → 维度缺失降置信度(2)。
    """
    from src.platform.persistence.models import SectorSnapshot

    ssvc = _svc()
    sources: list[dict] = []
    rows = (
        db.query(SectorSnapshot).filter(SectorSnapshot.snapshot_date == snapshot_date).all()
    )
    base_degrade = 0
    if not rows:
        logger.info("[盘前流水线][阶段2] 快照库无当日数据,实时拉取 degrade=1")
        try:
            ssvc.collect_daily_snapshot(db, snapshot_date=snapshot_date)
            rows = (
                db.query(SectorSnapshot)
                .filter(SectorSnapshot.snapshot_date == snapshot_date)
                .all()
            )
            base_degrade = 1
            sources.append(
                {"name": "sector_snapshot_live", "ok": bool(rows), "degrade_level": 1}
            )
        except Exception as e:
            logger.warning("[盘前流水线][阶段2] 实时快照拉取失败: %s", e)
            sources.append({"name": "sector_snapshot_live", "ok": False, "degrade_level": 3})
    else:
        sources.append({"name": "sector_snapshot_db", "ok": True, "degrade_level": 0})

    market_total = _market_limit_up_total(rows)
    items: list[SectorForecastItem] = []
    for row in rows:
        d5 = d10 = d20 = None
        momentum_source = ""
        momentum_degrade = 3
        try:
            # 行业主源是逐板块 akshare HTTP:限频 + 单板块硬超时,失败降级不中断
            _akshare_throttle()
            momentum = call_with_timeout(
                ssvc.sector_momentum, db, row.board_code,
                timeout_sec=pcfg.AKSHARE_FETCH_TIMEOUT_SEC,
            )
            d5 = (momentum.get("momentum") or {}).get("d5")
            d10 = (momentum.get("momentum") or {}).get("d10")
            d20 = (momentum.get("momentum") or {}).get("d20")
            momentum_source = momentum.get("source") or ""
            raw_degrade = momentum.get("degrade_level")
            momentum_degrade = 3 if raw_degrade is None else int(raw_degrade)
        except Exception as e:
            logger.warning("[盘前流水线][阶段2] 动量计算失败 board=%s: %s", row.board_code, e)

        weights = pcfg.MOMENTUM_WEIGHTS
        score = None
        if all(v is not None for v in (d5, d10, d20)):
            score = round(
                d5 * weights["d5"] + d10 * weights["d10"] + d20 * weights["d20"], 4
            )

        main_e8 = row.main_net_inflow / 1e8 if row.main_net_inflow is not None else None
        small_e8 = row.small_net_inflow / 1e8 if row.small_net_inflow is not None else None
        stage = classify_stage(
            d5=d5, d10=d10, d20=d20,
            main_inflow=main_e8, small_inflow=small_e8,
            limit_up_count=row.limit_up_count,
            market_limit_up_total=market_total,
        )
        divergence = detect_divergence(row.change_pct, main_e8)
        heat = bool(
            (row.limit_up_count is not None and row.limit_up_count >= pcfg.HEAT_LIMIT_UP["industry"])
            or (market_total is not None and market_total > pcfg.HEAT_LIMIT_UP["market_total"])
        )

        # 维度缺失 → degrade=2,置信度封顶
        missing = []
        if score is None:
            missing.append("momentum")
        if main_e8 is None:
            missing.append("main_flow")
        if row.limit_up_count is None:
            missing.append("limit_up")
        degrade = max(base_degrade, 2 if missing else 0, min(momentum_degrade, 2))

        confidence = 0.40
        if score is not None:
            confidence += 0.15
        if main_e8 is not None:
            confidence += 0.10
        if heat:
            confidence += 0.10
        if stage != "未知":
            confidence += 0.05
        if divergence == "派发":
            confidence -= 0.20
        elif divergence == "吸筹":
            confidence += 0.05
        if missing:
            confidence = min(confidence, 0.40)
        confidence = round(max(0.05, min(confidence, 0.85)), 3)

        observed = []
        if row.change_pct is not None:
            observed.append(f"板块涨跌【已观察】{row.change_pct:+.2f}%")
        if score is not None:
            observed.append(f"动量【已观察】{score:+.2f}")
        if row.limit_up_count is not None:
            observed.append(f"涨停家数【已观察】{row.limit_up_count}")
        predicted = []
        if stage != "未知":
            predicted.append(f"所处阶段【预测】{stage}")
        if divergence:
            predicted.append(f"资金背离【预测】{divergence}")

        items.append(
            SectorForecastItem(
                board_code=row.board_code,
                board_name=row.board_name or "",
                direction="bullish" if (score or 0) > 0 else ("bearish" if (score or 0) < 0 else "neutral"),
                confidence=confidence,
                stage=stage,
                momentum_score=score,
                divergence=divergence,
                heat=heat,
                degrade_level=degrade,
                source="algorithm",
                rationale=";".join(observed + predicted),
                observed_labels=observed,
                predicted_labels=predicted,
            )
        )

    items.sort(key=lambda x: (x.momentum_score if x.momentum_score is not None else -999), reverse=True)
    top = items[: max(1, int(top_n))]
    # 政策关联:事件日历 impact_boards 命中板块名(用于 LLM 修正与报告)
    if policy_events:
        for it in top:
            hit = _policy_hit(it.board_name, policy_events)
            if hit:
                it.catalysts.append(f"事件关联:{hit}")
    return top, sources


def _policy_hit(board_name: str, policy_events: list[dict]) -> str:
    """事件 impact_boards/scope 与板块名的结构化匹配(取第一个命中)。"""
    for ev in policy_events or []:
        boards = ev.get("impact_boards") or []
        scope = str(ev.get("scope") or "")
        for b in boards:
            if board_name and (board_name in str(b) or str(b) in board_name):
                return f"{ev.get('name')}({ev.get('event_date')})"
        if board_name and board_name in scope:
            return f"{ev.get('name')}({ev.get('event_date')})"
    return ""


def build_sector_user_content(items: list[SectorForecastItem]) -> str:
    lines = ["## 初筛结果(算法,按动量得分降序)"]
    for i, it in enumerate(items, 1):
        lines.append(
            f"### {i}. {it.board_name}({it.board_code})\n"
            f"- 动量得分:{it.momentum_score if it.momentum_score is not None else '缺失'}"
            f" 阶段:{it.stage} 热度:{'达标' if it.heat else '未达标'}"
            f" 背离:{it.divergence or '无'} 降级级别:{it.degrade_level}\n"
            f"- 依据:{';'.join(it.observed_labels) or '缺失'}\n"
            f"- 算法初判:{';'.join(it.predicted_labels) or '中性'}"
        )
    lines.append("\n请按系统指令输出板块预测修正(含输出契约 JSON)。")
    return "\n".join(lines)


def _merge_sector_llm(payload: dict, items: list[SectorForecastItem]) -> bool:
    """把 LLM 修正合并回 items(仅接受已知 board_code;confidence 归一到 0-1)。"""
    preds = payload.get("predictions")
    if not isinstance(preds, list):
        return False
    by_code = {it.board_code: it for it in items}
    merged = False
    for p in preds:
        if not isinstance(p, dict):
            continue
        code = str(p.get("board_code") or "").strip()
        it = by_code.get(code)
        if it is None:
            continue
        direction = str(p.get("direction") or "").strip().lower()
        if direction in ("bullish", "bearish", "neutral"):
            it.direction = direction
        conf = p.get("confidence")
        try:
            conf = float(conf)
            if conf > 1.5:  # 10 分制 → 0-1
                conf = conf / 10.0
            it.confidence = round(max(0.0, min(conf, 1.0)), 3)
        except (TypeError, ValueError):
            pass
        rationale = str(p.get("rationale") or "").strip()
        if rationale:
            it.rationale = f"{it.rationale};LLM修正:{rationale[:300]}"
        stage = str(p.get("stage") or "").strip()
        if stage:
            it.stage = stage
        for cat in p.get("catalysts") or []:
            if isinstance(cat, str) and cat:
                it.catalysts.append(cat[:120])
        if str(p.get("observed") or "").strip():
            it.observed_labels.append(f"LLM【已观察】{str(p['observed'])[:120]}")
        if str(p.get("predicted") or "").strip():
            it.predicted_labels.append(f"LLM【预测】{str(p['predicted'])[:120]}")
        it.source = "llm+algorithm"
        merged = True
    return merged


async def run_sector_stage(
    db: Session,
    *,
    snapshot_date: str,
    chat_fn: ChatFn | None,
    llm_timeout_seconds: int,
    top_n: int,
    policy_events: list[dict] | None = None,
) -> SectorForecast:
    """阶段2:纯 Python 初筛 → LLM 修正方向/置信度/理由(AI 失败用算法结果并标注)。

    初筛含逐板块 akshare 行业K线(阻塞 IO),必须跑在工作线程,不能阻塞事件循环。
    """
    items, sources = await asyncio.to_thread(
        screen_sectors,
        db,
        snapshot_date=snapshot_date,
        top_n=top_n,
        policy_events=policy_events,
    )
    forecast = SectorForecast(items=items, sources=sources)
    if not items or chat_fn is None:
        return forecast

    system_prompt = pcfg.render_prompt(pcfg.PROMPT_SECTOR)
    user_content = build_sector_user_content(items)
    try:
        content = await asyncio.wait_for(
            chat_fn(system_prompt, user_content), timeout=llm_timeout_seconds
        )
    except (asyncio.TimeoutError, Exception) as e:  # noqa: B014 - 超时/异常同路径降级
        logger.warning("[盘前流水线][阶段2] LLM 修正失败,保留算法结果: %s", e)
        forecast.sources.append({"name": "llm_sector_fix", "ok": False, "degrade_level": 2})
        for it in forecast.items:
            it.source = "algorithm(AI失败降级)"
        return forecast

    payload = try_extract_tagged_json(content)
    if payload is None or not _merge_sector_llm(payload, forecast.items):
        logger.warning("[盘前流水线][阶段2] LLM 输出解析失败,保留算法结果")
        forecast.sources.append({"name": "llm_sector_fix", "ok": False, "degrade_level": 2})
        for it in forecast.items:
            it.source = "algorithm(AI失败降级)"
        return forecast

    forecast.llm_ok = True
    forecast.sources.append({"name": "llm_sector_fix", "ok": True, "degrade_level": 0})
    return forecast


def upsert_sector_predictions(db: Session, forecast: SectorForecast, snapshot_date: str) -> int:
    """SectorPrediction 幂等 upsert(按 snapshot_date+board_code 唯一)。"""
    from src.platform.persistence.models import SectorPrediction

    count = 0
    for it in forecast.items:
        row = (
            db.query(SectorPrediction)
            .filter(
                SectorPrediction.snapshot_date == snapshot_date,
                SectorPrediction.board_code == it.board_code,
            )
            .first()
        )
        if not row:
            row = SectorPrediction(snapshot_date=snapshot_date, board_code=it.board_code)
            db.add(row)
        row.market = "CN"
        row.board_name = it.board_name
        row.direction = it.direction
        row.confidence = it.confidence
        row.stage = it.stage
        row.momentum_score = it.momentum_score
        row.rationale = it.rationale[:2000]
        row.catalysts = list(it.catalysts)[:20]
        row.meta = {
            "source": it.source,
            "degrade_level": it.degrade_level,
            "divergence": it.divergence,
            "heat": it.heat,
            "observed": it.observed_labels,
            "predicted": it.predicted_labels,
            "as_of": _now_iso(),
        }
        row.source_agent = pcfg.AGENT_NAME
        count += 1
    db.commit()
    return count


# ══════════════════════════ 阶段3 选股三价 ══════════════════════════


def _is_st_name(name: str) -> bool:
    return (name or "").strip().upper().startswith(("ST", "*ST", "SST", "退"))


def _to_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _extract_cagr(values_by_period: dict[str, float | None]) -> float | None:
    """由逐期值(期间升序)算年化 CAGR;期间不足 5 期(>1 年)或非正起点返回 None。"""
    series = [v for _, v in sorted((values_by_period or {}).items()) if v is not None]
    if len(series) < 5 or series[0] is None or series[0] <= 0 or series[-1] is None:
        return None
    years = (len(series) - 1) / 4.0
    if series[-1] <= 0:
        return None  # 亏损期无法几何平均,由 veto/缺项逻辑处理
    try:
        return (series[-1] / series[0]) ** (1.0 / years) - 1.0
    except (ZeroDivisionError, ValueError, OverflowError):
        return None


def _latest(series: dict[str, float | None]) -> float | None:
    items = [(k, v) for k, v in sorted((series or {}).items()) if v is not None]
    return items[-1][1] if items else None


def _pct_to_ratio(v: float | None) -> float | None:
    """百分数(91.5)→ 比例(0.915);已是比例(<1.5)原样返回。"""
    if v is None:
        return None
    return v / 100.0 if abs(v) > 1.5 else v


def summarize_financial(raw: dict) -> dict:
    """financial_abstract 原始 dict → 流水线摘要(五指标 + veto + 单季拆解)。"""
    ind = raw.get("indicators") or {}
    periods = list(raw.get("periods") or [])
    rev = ind.get("营业总收入") or {}
    profit = ind.get("归母净利润") or {}
    eps = ind.get("基本每股收益") or {}
    gross = _pct_to_ratio(_latest(ind.get("毛利率") or {}))
    net_margin = _pct_to_ratio(_latest(ind.get("销售净利率") or {}))
    roe = _pct_to_ratio(_latest(ind.get("净资产收益率(ROE)") or {}))

    rev_cagr = _extract_cagr(rev)
    profit_cagr = _extract_cagr(profit)
    # 单季拆解:最新单季净利 = 最新累计 - 上一累计;同比 = 与去年同期单季比
    single_q_yoy = _single_quarter_profit_yoy(profit, periods)

    recent_profits = [v for _, v in sorted(profit.items()) if v is not None][-pcfg.VETO_CONSECUTIVE_LOSS_PERIODS:]
    consecutive_loss = (
        len(recent_profits) >= pcfg.VETO_CONSECUTIVE_LOSS_PERIODS
        and all(v < 0 for v in recent_profits)
    )
    latest_profit = recent_profits[-1] if recent_profits else None

    return {
        "report_period": _latest_period_label(periods),
        "rev_cagr": rev_cagr,
        "profit_cagr": profit_cagr,
        "gross_margin": gross,
        "net_margin": net_margin,
        "roe": roe,
        "eps_latest": _latest(eps),
        "eps_series": {k: v for k, v in sorted(eps.items()) if v is not None},
        "profit_series": {k: v for k, v in sorted(profit.items()) if v is not None},
        "single_quarter_profit_yoy": single_q_yoy,
        "latest_net_profit": latest_profit,
        "consecutive_loss": consecutive_loss,
        "indicators_available": sum(
            1 for v in (rev_cagr, profit_cagr, gross, net_margin, roe) if v is not None
        ),
    }


def _latest_period_label(periods: list[str]) -> str:
    if not periods:
        return ""
    p = sorted(periods)[-1]
    if len(p) == 8:
        q = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(p[4:6], p[4:6])
        return f"{p[:4]}{q}"
    return p


def _single_quarter_profit_yoy(profit: dict[str, float | None], periods: list[str]) -> float | None:
    """最新单季净利同比:累计序列差分出单季,与去年同季单季比;不可得返回 None。

    键为 ``YYYYMMDD`` 报告期(值为年初至今累计)。单季 = 本期累计 − 上期累计
    (Q1 单季即累计);去年同季单季同样差分。任何一步不可得返回 None。
    """
    items = sorted((k, v) for k, v in (profit or {}).items() if v is not None)
    if len(items) < 5:
        return None
    keys = [k for k, _ in items]
    vals = [v for _, v in items]

    def _single(idx: int) -> float | None:
        """第 idx 期(与 keys 对齐)的单季净利;Q1 即累计,否则减同年上一累计期。"""
        k = keys[idx]
        if k[4:6] == "03":
            return vals[idx]
        if idx < 1 or keys[idx - 1][:4] != k[:4] or keys[idx - 1][4:6] >= k[4:6]:
            return None
        return vals[idx] - vals[idx - 1]

    last = keys[-1]
    single_now = _single(len(keys) - 1)
    if single_now is None:
        return None
    prev_same_q = [k for k in keys if k[4:6] == last[4:6] and k[:4] < last[:4]]
    if not prev_same_q:
        return None
    single_prev = _single(keys.index(prev_same_q[-1]))
    if single_prev is None or single_prev == 0:
        return None
    try:
        return (single_now - single_prev) / abs(single_prev)
    except (ZeroDivisionError, TypeError):
        return None


def score_triple_high(summary: dict) -> dict:
    """三高评分:维度内加权 + 缺项降级(>=3/5 均值;2/5 ×0.9;<2/5 弃维度)。

    返回 {dimensions:{growth,profit,moat}, composite, missing_dims, factor, detail}
    """
    norm = {"cagr_max": 0.30, "gross_margin_max": 0.60, "net_margin_max": 0.30, "roe_max": 0.30}

    def _clamp01(v: float | None, cap: float) -> float | None:
        if v is None:
            return None
        return max(0.0, min(v / cap, 1.0)) if cap > 0 else None

    rev_g = _clamp01(_to_float(summary.get("rev_cagr")), norm["cagr_max"])
    prof_g = _clamp01(_to_float(summary.get("profit_cagr")), norm["cagr_max"])
    gross = _clamp01(_to_float(summary.get("gross_margin")), norm["gross_margin_max"])
    net_m = _clamp01(_to_float(summary.get("net_margin")), norm["net_margin_max"])
    roe = _clamp01(_to_float(summary.get("roe")), norm["roe_max"])

    available = sum(1 for v in (rev_g, prof_g, gross, net_m, roe) if v is not None)
    missing_dims: list[str] = []
    dims: dict[str, float | None] = {}

    def _dim(indicators: dict[str, float | None], weights: dict[str, float]) -> float | None:
        got = {k: v for k, v in indicators.items() if v is not None}
        if not got:
            return None
        w_sum = sum(weights[k] for k in got)
        if w_sum <= 0:
            return None
        return sum(got[k] * weights[k] for k in got) / w_sum

    dims["growth"] = _dim({"rev_cagr": rev_g, "profit_cagr": prof_g}, pcfg.TRIPLE_HIGH["growth_indicators"])
    dims["profit"] = _dim({"gross_margin": gross, "net_margin": net_m, "roe": roe}, pcfg.TRIPLE_HIGH["profit_indicators"])
    dims["moat"] = _dim({"gross_margin": gross, "roe": roe}, pcfg.TRIPLE_HIGH["moat_indicators"])

    if dims["growth"] is None:
        missing_dims.append("growth")
    if dims["profit"] is None:
        missing_dims.append("profit")
    if dims["moat"] is None:
        missing_dims.append("moat")

    dw = pcfg.TRIPLE_HIGH["dimension_weights"]
    got_dims = {k: v for k, v in dims.items() if v is not None}
    composite = None
    factor = 1.0
    if got_dims:
        w_sum = sum(dw[k] for k in got_dims)
        weighted = sum(got_dims[k] * dw[k] for k in got_dims) / w_sum
        # 缺项降级:>=3/5 指标可得 → 正常;恰 2/5 → ×0.9;<2/5 → 弃维度
        if available >= 5 - pcfg.TRIPLE_HIGH_MISSING_RULES["full_max_missing"]:
            factor = 1.0
            composite = weighted
        elif available == 5 - pcfg.TRIPLE_HIGH_MISSING_RULES["degraded_missing"]:
            factor = pcfg.TRIPLE_HIGH_MISSING_RULES["degrade_factor"]
            composite = weighted
        else:
            factor = 0.0
            composite = None
        if composite is not None:
            composite = round(composite * factor, 4)
    if composite is None:
        missing_dims = sorted(set(missing_dims) | {"all"})
    return {
        "dimensions": {k: (round(v, 4) if v is not None else None) for k, v in dims.items()},
        "composite": composite,
        "missing_dims": missing_dims,
        "factor": factor,
        "indicators_available": available,
        "normalized": {
            "rev_cagr": rev_g, "profit_cagr": prof_g,
            "gross_margin": gross, "net_margin": net_m, "roe": roe,
        },
    }


def load_fundamentals(
    db: Session,
    symbol: str,
    *,
    deadline: float,
    last_fetch_at: list[float] | None = None,
) -> tuple[dict | None, str]:
    """财务摘要:FundamentalsCache 优先;miss 时 akshare 按需拉(1s 间隔)并回写缓存。

    返回 (summary, note);note 为空表示正常,否则是缺口标注。预算不足抛 BudgetExhausted。
    """
    from src.platform.persistence.models import FundamentalsCache

    row = (
        db.query(FundamentalsCache)
        .filter(FundamentalsCache.symbol == symbol)
        .order_by(FundamentalsCache.report_period.desc(), FundamentalsCache.id.desc())
        .first()
    )
    # 复用条件须覆盖核验必需字段:缺 single_quarter_profit_yoy 的缓存(旧版缺陷)
    # 会让核验必然 FAIL,不如重拉重算
    if (
        row
        and isinstance(row.summary, dict)
        and row.summary.get("rev_cagr") is not None
        and row.summary.get("single_quarter_profit_yoy") is not None
    ):
        return row.summary, ""

    if time.monotonic() >= deadline - pcfg.FINANCE_BUDGET_RESERVE_SEC:
        raise BudgetExhausted(symbol)

    # 限频(与阶段2/核验共享间隔状态)+ 单次硬超时
    akshare_throttle()
    if last_fetch_at is not None:
        last_fetch_at[0] = time.monotonic()

    raw = call_with_timeout(
        _fetch_financial_abstract, symbol, timeout_sec=pcfg.AKSHARE_FETCH_TIMEOUT_SEC
    )
    if not raw:
        return None, "财务摘要按需拉取失败(源不可用),该候选财务维度缺失"
    summary = summarize_financial(raw)
    summary["sources"] = [{"source": "ak.stock_financial_abstract", "as_of": _now_iso()}]
    try:
        cache = FundamentalsCache(
            symbol=symbol,
            report_period=summary.get("report_period") or "unknown",
            summary=summary,
            sources=summary["sources"],
        )
        db.add(cache)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("[盘前流水线][阶段3] 财务缓存回写失败 symbol=%s: %s", symbol, e)
    return summary, ""


class BudgetExhausted(RuntimeError):
    """财务拉取总预算耗尽(剩余 < 预留秒数)。"""

    def __init__(self, symbol: str):
        super().__init__(symbol)
        self.symbol = symbol


def _fetch_financial_abstract(symbol: str) -> dict | None:
    """模块级间接层(测试 monkeypatch 点):复用 tradingagents 的财务摘要拉取。"""
    from src.modules.automation.tradingagents.data_context import fetch_financial_abstract

    return fetch_financial_abstract(symbol)


def _policy_score(board_name: str, policy_events: list[dict]) -> float:
    """政策关联分:高等级事件命中 1.0,中等级 0.7,无命中给中性缺省 0.5。"""
    for ev in policy_events or []:
        if _policy_hit(board_name, [ev]):
            level = str(ev.get("level") or "").lower()
            if level == "high":
                return pcfg.POLICY_SCORE["event_high_hit"]
            return pcfg.POLICY_SCORE["event_medium_hit"]
    return pcfg.POLICY_SCORE["default"]


def _entry_zone_from_pe(
    db: Session, symbol: str, price: float
) -> tuple[float | None, float | None, dict]:
    """PE 百分位入场区:p30/p50 分位 PE 回归映射价格。样本不足返回 (None, None, meta)。"""
    from src.platform.persistence.models import ValuationSeries

    start = (date.today() - timedelta(days=int(pcfg.PE_WINDOW_YEARS * 365.25))).isoformat()
    rows = (
        db.query(ValuationSeries)
        .filter(ValuationSeries.symbol == symbol, ValuationSeries.trade_date >= start)
        .order_by(ValuationSeries.trade_date.asc())
        .all()
    )
    pes = [r.pe_ttm for r in rows if r.pe_ttm is not None and r.pe_ttm > 0]
    meta: dict = {
        "source": "valuation_series_pe_p30_p50",
        "window_years": pcfg.PE_WINDOW_YEARS,
        "obs": len(pes),
        "degrade_level": 0,
    }
    if len(pes) < pcfg.PE_MIN_OBS:
        meta["degrade_level"] = 2
        meta["reason"] = f"PE 序列样本不足(<{pcfg.PE_MIN_OBS}),降级技术面支撑"
        return None, None, meta
    pes_sorted = sorted(pes)
    pe_now = pes[-1]
    p30 = _percentile(pes_sorted, 0.30)
    p50 = _percentile(pes_sorted, 0.50)
    if pe_now <= 0 or p30 <= 0:
        meta["degrade_level"] = 2
        meta["reason"] = "PE 现值或 p30 非正,降级技术面支撑"
        return None, None, meta
    meta["pe_now"] = round(pe_now, 4)
    meta["pe_p30"] = round(p30, 4)
    meta["pe_p50"] = round(p50, 4)
    low = round(price * p30 / pe_now, 3)
    high = round(price * p50 / pe_now, 3)
    if high <= low:
        high = round(low * 1.02, 3)
    return low, high, meta


def _percentile(sorted_values: list[float], q: float) -> float:
    """线性插值百分位(输入升序)。"""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def _entry_zone_from_technical(bars) -> tuple[float | None, float | None, dict]:
    """技术面支撑降级:入场下轨=近 N 日低点,上轨=20 日均线(标注 degrade=2)。"""
    closes = [b.close for b in bars if b.close]
    lows = [b.low for b in bars if b.low]
    window = closes[-pcfg.TECH_FALLBACK_MA_DAYS:]
    low_window = lows[-pcfg.TECH_FALLBACK_LOW_WINDOW_DAYS:] or lows
    ma20 = round(sum(window) / len(window), 3) if window else None
    low = round(min(low_window), 3) if low_window else None
    return low, ma20, {
        "source": f"technical_ma{pcfg.TECH_FALLBACK_MA_DAYS}_low{pcfg.TECH_FALLBACK_LOW_WINDOW_DAYS}",
        "degrade_level": 2,
        "reason": "PE 百分位不可得,降级技术面支撑",
    }


def _three_prices(
    *, price: float, leader_score: float, bars, valuation_meta_fn
) -> dict:
    """三价合成:入场区(PE 百分位→技术降级)+ 评级止损 + 止盈(与近端压力孰低)+ V11。"""
    low, high, val_meta = valuation_meta_fn()
    if low is None or high is None:
        low, high, val_meta = _entry_zone_from_technical(bars)
    if low is None or high is None or price is None or price <= 0:
        return {"drop": "入场区不可得"}
    mid_raw = (low + high) / 2.0
    mid = round(mid_raw, 3)
    grade = "A" if leader_score >= pcfg.GRADE_THRESHOLDS["A"] else (
        "B" if leader_score >= pcfg.GRADE_THRESHOLDS["B"] else "C"
    )
    stop_raw = mid_raw * (1.0 + pcfg.STOP_LOSS_BY_GRADE[grade])
    stop = round(stop_raw, 3)
    near_res = None
    highs = [b.high for b in bars if b.high]
    if highs:
        near_res = round(max(highs[-pcfg.NEAR_RESISTANCE_WINDOW_DAYS:]), 3)
    target_raw = min(mid_raw * (1.0 + pcfg.TAKE_PROFIT_PCT), float(near_res)) if near_res is not None \
        else mid_raw * (1.0 + pcfg.TAKE_PROFIT_PCT)
    target = round(target_raw, 3)
    rr = None
    if stop_raw < mid_raw < target_raw:
        # V11 用未取整值计算,避免存储取整误差把边界 2.0 打成 1.99
        rr = round((target_raw - mid_raw) / (mid_raw - stop_raw), 4)
    return {
        "entry_low": low,
        "entry_high": high,
        "entry_mid": mid,
        "grade": grade,
        "stop_loss": stop,
        "target_price": target,
        "near_resistance": near_res,
        "rr": rr,
        "v11_pass": bool(rr is not None and rr >= pcfg.RR_MIN),
        "valuation": val_meta,
    }


def hard_filter(
    *,
    symbol: str,
    name: str,
    db_name: str | None,
    quote: dict | None,
    bars,
) -> str:
    """硬过滤:返回空串=通过;非空=淘汰原因。

    - ST 名称前缀(候选名 + Stock 表双保险);
    - 上市 > 60 天(K 线首根日期距今天数);
    - 市值区间 [mv_min_e8, mv_max_e8](亿,取流通市值);
    - 非停牌(当日成交量 > 0)。
    """
    if _is_st_name(name) or (db_name and _is_st_name(db_name)):
        return "ST 证券(名称前缀)"
    if not bars or len(bars) < 2:
        return "K 线不可得"
    first_day = bars[0].date
    try:
        first_date = datetime.strptime(str(first_day)[:10], "%Y-%m-%d").date()
    except ValueError:
        return "K 线日期不可解析"
    if (date.today() - first_date).days < pcfg.LIST_AGE_MIN_DAYS:
        return f"上市不足 {pcfg.LIST_AGE_MIN_DAYS} 天"
    volume = _to_float((quote or {}).get("volume"))
    if volume is None or volume <= 0:
        return "疑似停牌(成交量为 0)"
    mv = _to_float((quote or {}).get("circulating_market_value"))
    if mv is None:
        return "流通市值缺失"
    # 腾讯行情的流通市值字段单位已是「亿」(与 insights 渲染口径一致),无需换算
    mv_e8 = mv
    if mv_e8 < pcfg.DEFAULT_MV_MIN_E8 or mv_e8 > pcfg.DEFAULT_MV_MAX_E8:
        return (
            f"流通市值 {mv_e8:.0f} 亿超出区间 "
            f"[{pcfg.DEFAULT_MV_MIN_E8}, {pcfg.DEFAULT_MV_MAX_E8}] 亿"
        )
    return ""


def pick_candidates(
    db: Session,
    sector_items: list[SectorForecastItem],
    policy_events: list[dict],
    *,
    max_candidates: int,
    budget_seconds: float,
    stock_names: dict[str, str] | None = None,
) -> tuple[list[PickCandidate], list[dict]]:
    """阶段3 主入口(同步;阻塞 IO 由调用方放线程)。

    返回 (candidates(含通过/未通过,V11 与硬过滤结论保留在字段), sources)。
    """
    sources: list[dict] = []
    deadline = time.monotonic() + max(30.0, float(budget_seconds))
    last_fetch: list[float] = [0.0]
    stock_names = stock_names or {}

    candidates: list[PickCandidate] = []
    constituents_fn = _svc().fetch_board_constituents

    for sector in sector_items:
        try:
            members = constituents_fn(sector.board_code, limit=pcfg.COARSE_PICK_TOP_N) or []
        except Exception as e:
            logger.warning("[盘前流水线][阶段3] 成分股获取失败 board=%s: %s", sector.board_code, e)
            members = []
        if not members:
            sources.append(
                {"name": f"constituents:{sector.board_code}", "ok": False, "degrade_level": 3}
            )
            continue
        symbols = [m["symbol"] for m in members]
        quotes = {}
        try:
            for q in _md().quotes(symbols, market="CN") or []:
                quotes[q.symbol] = {
                    "name": q.name,
                    "current_price": q.current_price,
                    "volume": q.volume,
                    "circulating_market_value": q.circulating_market_value,
                }
        except Exception as e:
            logger.warning("[盘前流水线][阶段3] 批量行情失败 board=%s: %s", sector.board_code, e)
        sources.append(
            {
                "name": f"constituents:{sector.board_code}",
                "ok": True,
                "degrade_level": 1,
                "count": len(members),
            }
        )

        for m in members:
            symbol = m["symbol"]
            name = m.get("name") or quotes.get(symbol, {}).get("name") or stock_names.get(symbol) or symbol
            try:
                bars = _md().klines(symbol, market="CN", days=90) or []
            except Exception as e:
                logger.debug("[盘前流水线][阶段3] K线失败 symbol=%s: %s", symbol, e)
                bars = []
            quote = quotes.get(symbol)
            cand = PickCandidate(
                symbol=symbol, stock_name=name,
                board_code=sector.board_code, board_name=sector.board_name,
            )
            drop = hard_filter(
                symbol=symbol, name=name,
                db_name=_stock_name_in_db(db, symbol),
                quote=quote, bars=bars,
            )
            if drop:
                cand.filters_dropped = drop
                candidates.append(cand)
                continue

            price = _to_float((quote or {}).get("current_price"))
            if price is None or price <= 0:
                cand.filters_dropped = "现价缺失"
                candidates.append(cand)
                continue
            cand.current_price = price

            # 一票否决:连续亏损(ST 已在硬过滤)
            try:
                summary, note = load_fundamentals(
                    db, symbol, deadline=deadline, last_fetch_at=last_fetch
                )
            except BudgetExhausted:
                cand.budget_exhausted = True
                cand.filters_dropped = "budget_exhausted:财务拉取预算不足"
                candidates.append(cand)
                return _finalize(candidates, sources, max_candidates)
            if note:
                cand.missing_dims.append(note)
                summary = None
            if summary:
                cand.fundamentals = summary
                if summary.get("consecutive_loss"):
                    cand.veto_reason = "连续亏损(最近两期归母净利为负)"
                    candidates.append(cand)
                    continue
                th = score_triple_high(summary)
                cand.triple_high = th
                policy = _policy_score(sector.board_name, policy_events)
                cand.policy_score = policy
                composite = th.get("composite")
                if composite is None:
                    cand.leader_score = 0.0
                    cand.missing_dims.append("三高维度不可得")
                else:
                    lw = pcfg.TRIPLE_HIGH["leader_weights"]
                    cand.leader_score = round(
                        composite * lw["triple_high"] + policy * lw["policy"], 4
                    )
            else:
                cand.leader_score = 0.0
                if not cand.missing_dims:
                    cand.missing_dims.append("财务摘要不可得")

            def _valuation_fn(symbol=symbol, price=price):
                return _entry_zone_from_pe(db, symbol, price)

            prices = _three_prices(
                price=price,
                leader_score=cand.leader_score,
                bars=bars,
                valuation_meta_fn=_valuation_fn,
            )
            if prices.get("drop"):
                cand.filters_dropped = prices["drop"]
                candidates.append(cand)
                continue
            cand.entry_low = prices["entry_low"]
            cand.entry_high = prices["entry_high"]
            cand.entry_mid = prices["entry_mid"]
            cand.grade = prices["grade"]
            cand.stop_loss = prices["stop_loss"]
            cand.target_price = prices["target_price"]
            cand.valuation = prices.get("valuation") or {}
            cand.rr = prices.get("rr")
            cand.v11_pass = prices["v11_pass"]
            if not cand.v11_pass:
                cand.filters_dropped = (
                    f"V11 未过(盈亏比 {cand.rr} < {pcfg.RR_MIN})"
                    if cand.rr is not None
                    else "V11 不可计算(止损/目标无效)"
                )
            candidates.append(cand)
    return _finalize(candidates, sources, max_candidates)


def _stock_name_in_db(db: Session, symbol: str) -> str | None:
    from src.platform.persistence.models import Stock

    row = db.query(Stock).filter(Stock.symbol == symbol, Stock.market == "CN").first()
    return row.name if row else None


def _finalize(
    candidates: list[PickCandidate], sources: list[dict], max_candidates: int
) -> tuple[list[PickCandidate], list[dict]]:
    """排序取 top:同一 symbol 跨板块只保留龙头分最高一条;V11 通过者按分截断。

    返回列表内 symbol 唯一(头部为入选 top,尾部为落选者),下游(LLM 终评/写库)
    按 symbol 索引不会撞行。
    """
    best_by_symbol: dict[str, PickCandidate] = {}
    for c in candidates:
        if c.v11_pass and not c.veto_reason and not c.filters_dropped:
            cur = best_by_symbol.get(c.symbol)
            if cur is None or c.leader_score > cur.leader_score:
                best_by_symbol[c.symbol] = c
    passed = sorted(best_by_symbol.values(), key=lambda c: c.leader_score, reverse=True)
    ordered = passed[: max(1, int(max_candidates))]
    ordered_symbols = {c.symbol for c in ordered}
    rest: list[PickCandidate] = []
    seen_rest: set[str] = set()
    for c in candidates:
        if c.symbol in ordered_symbols or c.symbol in seen_rest:
            continue
        seen_rest.add(c.symbol)
        rest.append(c)
    return ordered + rest, sources


def build_pick_user_content(candidates: list[PickCandidate]) -> str:
    """阶段3 终评 user content:仅送 V11 通过的头部候选。"""
    lines = ["## 候选三价清单(算法产出,V11 已过)"]
    for i, c in enumerate(candidates, 1):
        th = c.triple_high or {}
        lines.append(
            f"### {i}. {c.stock_name}({c.symbol}) 板块:{c.board_name}\n"
            f"- 现价:{c.current_price} 入场区:[{c.entry_low}, {c.entry_high}]"
            f" 中值:{c.entry_mid} 止损:{c.stop_loss}({c.grade}档)"
            f" 止盈:{c.target_price} 盈亏比:{c.rr}\n"
            f"- 三高:增长={ (th.get('dimensions') or {}).get('growth') }"
            f" 利润={ (th.get('dimensions') or {}).get('profit') }"
            f" 壁垒={ (th.get('dimensions') or {}).get('moat') }"
            f" 龙头总分={c.leader_score} 政策关联={c.policy_score}\n"
            f"- 估值口径:{c.valuation.get('source')}(degrade={c.valuation.get('degrade_level')})\n"
            f"- 缺项:{';'.join(c.missing_dims) or '无'}"
        )
    lines.append("\n请按系统指令输出终评(含输出契约 JSON)。")
    return "\n".join(lines)


def apply_pick_review(payload: dict, candidates: list[PickCandidate]) -> bool:
    """把 LLM 终评合并回候选(action/置信度/风险/理由);仅接受已知 symbol。"""
    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        return False
    by_symbol: dict[str, PickCandidate] = {}
    for c in candidates:
        if c.v11_pass and c.symbol not in by_symbol:
            by_symbol[c.symbol] = c
    merged = False
    for r in reviews:
        if not isinstance(r, dict):
            continue
        c = by_symbol.get(str(r.get("symbol") or ""))
        if c is None:
            continue
        action = str(r.get("action") or "").strip().lower()
        if action in ("buy", "add", "watch"):
            c.action = action
            c.action_label = {"buy": "建仓", "add": "加仓", "watch": "观望"}[action]
        try:
            c.confidence = round(max(0.0, min(float(r.get("confidence")), 10.0)), 2)
        except (TypeError, ValueError):
            pass
        for key, attr in (("risks", "risks"), ("reasons", "reasons")):
            vals = r.get(key)
            if isinstance(vals, list):
                setattr(c, attr, [str(v)[:200] for v in vals if v][:6])
        review_note = str(r.get("rationale") or "").strip()
        c.final_review = {
            "rationale": review_note[:600],
            "three_price_check": r.get("three_price_check") or "",
        }
        c.llm_ok = True
        merged = True
    return merged


async def run_pick_llm_review(
    candidates: list[PickCandidate], chat_fn: ChatFn, *, llm_timeout_seconds: int
) -> bool:
    """LLM 终评(AI 失败保留算法结果并标注)。返回是否成功。"""
    top = [c for c in candidates if c.v11_pass]
    if not top or chat_fn is None:
        return False
    system_prompt = pcfg.render_prompt(pcfg.PROMPT_PICK)
    user_content = build_pick_user_content(top)
    try:
        content = await asyncio.wait_for(
            chat_fn(system_prompt, user_content), timeout=llm_timeout_seconds
        )
    except (asyncio.TimeoutError, Exception) as e:  # noqa: B014
        logger.warning("[盘前流水线][阶段3] 终评失败,保留算法三价: %s", e)
        for c in top:
            c.llm_ok = False
        return False
    payload = try_extract_tagged_json(content)
    if payload is None:
        logger.warning("[盘前流水线][阶段3] 终评解析失败,保留算法三价")
        for c in top:
            c.llm_ok = False
        return False
    return apply_pick_review(payload, candidates)
