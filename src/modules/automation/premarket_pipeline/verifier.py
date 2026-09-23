"""盘前决策流水线 阶段4:财报核验(fail-closed 六项)。

六项检查(任一 FAIL → 剔除并注明;WARN 只降置信度不剔除):
1. disclosure_freshness  披露新鲜度(最新报告期距今 ≤ ~两季度);
2. dual_source           双源交叉(东财/同花顺/新浪财务摘要任两路,
   营收同比/净利同比/EPS 偏差 > 5% = FAIL);
3. single_quarter        单季拆解(最新单季净利同比 < 0 = FAIL);
4. ex_rights             近 60 天除权/拆股检测 → EPS 锚失效强制最新 TTM;
5. disclosure_window     未来 10 个交易日内有预约披露 → WARN 降置信度;
6. field_availability    任一必取字段取不到 = FAIL(fail-closed 兜底)。

结果 + 摘要落 FundamentalsCache(verified_at 标记核验时点)。
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.premarket_pipeline.types import EarningsVerification
from src.modules.market.data_cross_validation import cross_validate, to_float

logger = logging.getLogger(__name__)


class VerificationFailed(RuntimeError):
    """核验通道整体不可用时的显式信号(调用方按 FAIL 记)。"""


# ────────────────────────── 多源财务摘要通道 ──────────────────────────


def _df_records(df) -> list[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    try:
        return df.to_dict("records")
    except Exception:
        return []


def _fetch_em(symbol: str) -> dict | None:
    """东财口径财务摘要(复用流水线既有拉取,含指标结构化)。"""
    from src.modules.automation.premarket_pipeline.stages import _fetch_financial_abstract

    return _fetch_financial_abstract(symbol)


def _fetch_sina(symbol: str) -> dict | None:
    """新浪口径财务摘要(ak.stock_financial_abstract_sina)→ 归一化。失败 None(fail-soft)。"""
    try:
        import akshare as ak

        df = ak.stock_financial_abstract_sina(symbol=symbol)
    except Exception as e:
        logger.debug("[盘前流水线][核验] 新浪财务摘要失败 %s: %s", symbol, e)
        return None
    rows = _df_records(df)
    if not rows:
        return None
    # 新浪摘要:[报告期, 归属于母公司所有者的净利润, 营业总收入, ...] 按列名归一
    out: dict[str, dict[str, float | None]] = {}
    periods: list[str] = []
    for row in rows:
        period = str(row.get("报告期") or row.get("report_date") or "")[:10].replace("-", "")
        if len(period) != 8:
            continue
        periods.append(period)
        out.setdefault("归母净利润", {})[period] = to_float(
            row.get("归属于母公司所有者的净利润") or row.get("净利润")
        )
        out.setdefault("营业总收入", {})[period] = to_float(
            row.get("营业总收入") or row.get("营业收入")
        )
    if not periods:
        return None
    return {"periods": periods, "indicators": out}


def _fetch_ths(symbol: str) -> dict | None:
    """同花顺口径财务摘要(ak.stock_financial_abstract_ths)→ 归一化。失败 None。"""
    try:
        import akshare as ak

        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
    except Exception as e:
        logger.debug("[盘前流水线][核验] 同花顺财务摘要失败 %s: %s", symbol, e)
        return None
    rows = _df_records(df)
    if not rows:
        return None
    periods: list[str] = []
    eps: dict[str, float | None] = {}
    net: dict[str, float | None] = {}
    rev: dict[str, float | None] = {}
    for row in rows:
        period = str(row.get("报告期") or "")[:10].replace("-", "")
        if len(period) != 8:
            continue
        periods.append(period)
        eps[period] = to_float(row.get("基本每股收益") or row.get("每股收益"))
        net[period] = to_float(row.get("净利润") or row.get("归母净利润"))
        rev[period] = to_float(row.get("营业总收入") or row.get("营业收入"))
    if not periods:
        return None
    return {"periods": periods, "indicators": {"基本每股收益": eps, "归母净利润": net, "营业总收入": rev}}


_FETCHERS: dict[str, Callable[[str], dict | None]] = {
    "em": _fetch_em,
    "sina": _fetch_sina,
    "ths": _fetch_ths,
}


def _norm_yoy(series: dict[str, float | None]) -> float | None:
    """最新累计同比:末期/上期同期;期间不足 5 期(>1 年)不可得。"""
    items = [(k, v) for k, v in sorted((series or {}).items()) if v is not None]
    if len(items) < 5:
        return None
    keys = [k for k, _ in items]
    vals = [v for _, v in items]
    same_q = [k for k in keys if k[4:6] == keys[-1][4:6]]
    if len(same_q) < 2:
        return None
    j = keys.index(same_q[-2])
    base = vals[j]
    if not base:
        return None
    return (vals[-1] - base) / abs(base)


def _normalized_metrics(raw: dict | None) -> dict[str, float | None]:
    """从任一通道原始摘要提取可交叉字段:revenue_yoy / profit_yoy / eps。"""
    if not raw:
        return {}
    ind = raw.get("indicators") or {}
    eps_series = ind.get("基本每股收益") or {}
    latest_eps = None
    items = [(k, v) for k, v in sorted(eps_series.items()) if v is not None]
    if items:
        latest_eps = items[-1][1]
    return {
        "revenue_yoy": _norm_yoy(ind.get("营业总收入") or {}),
        "profit_yoy": _norm_yoy(ind.get("归母净利润") or {}),
        "eps": latest_eps,
    }


def dual_source_check(db: Session, symbol: str) -> dict:
    """双源交叉:任两路通道可得 → 逐字段 cross_validate(tol=5%)。

    每路抓取均带限频与硬超时(akshare 同步 HTTP,防单通道挂死拖垮核验)。
    Returns:
        {status: PASS|FAIL|WARN, detail, sources, fields:{field:{verdict, degrade_level}}}
    """
    from src.modules.automation.premarket_pipeline.stages import (
        FetchTimeout,
        akshare_throttle,
        call_with_timeout,
    )

    values_by_field: dict[str, list[dict]] = {}
    used_sources: list[str] = []
    for name in pcfg.FUND_SOURCE_PRIORITY:
        fetcher = _FETCHERS.get(name)
        if fetcher is None:
            continue
        try:
            akshare_throttle()
            raw = call_with_timeout(
                fetcher, symbol, timeout_sec=pcfg.AKSHARE_FETCH_TIMEOUT_SEC
            )
        except FetchTimeout as e:
            logger.warning("[盘前流水线][核验] %s 通道超时 %s: %s", name, symbol, e)
            raw = None
        except Exception as e:  # 注入替身抛错同样 fail-soft
            logger.debug("[盘前流水线][核验] %s 通道异常 %s: %s", name, symbol, e)
            raw = None
        metrics = _normalized_metrics(raw)
        if not metrics:
            continue
        used_sources.append(name)
        for field, value in metrics.items():
            if value is None:
                continue
            values_by_field.setdefault(field, []).append({"value": value, "source": name})

    if not used_sources:
        return {
            "status": "FAIL",
            "detail": "财务摘要所有通道均不可得,无法交叉(fail-closed)",
            "sources": [],
            "fields": {},
        }
    if len(used_sources) < 2:
        return {
            "status": "FAIL",
            "detail": f"仅 {used_sources[0]} 单源可得,无法双源交叉(fail-closed)",
            "sources": used_sources,
            "fields": {},
        }

    fields: dict[str, dict] = {}
    any_disputed = False
    for field, values in values_by_field.items():
        verdict = cross_validate(values, tol=pcfg.DUAL_SOURCE_TOLERANCE)
        fields[field] = {
            "status": verdict["status"],
            "deviation": verdict.get("deviation"),
            "degrade_level": verdict["degrade_level"],
            "sources": [s.get("source") for s in verdict.get("sources", [])],
        }
        if verdict["status"] == "disputed":
            any_disputed = True
    if not fields:
        return {
            "status": "FAIL",
            "detail": "双源通道可达但无任何可交叉字段(fail-closed)",
            "sources": used_sources,
            "fields": {},
        }
    if any_disputed:
        disputed = [k for k, v in fields.items() if v["status"] == "disputed"]
        return {
            "status": "FAIL",
            "detail": f"字段 {disputed} 双源偏差 > {pcfg.DUAL_SOURCE_TOLERANCE:.0%},数据存疑",
            "sources": used_sources,
            "fields": fields,
        }
    return {
        "status": "PASS",
        "detail": f"双源交叉一致(源:{used_sources},字段:{sorted(fields)})",
        "sources": used_sources,
        "fields": fields,
    }


# ────────────────────────── 单项检查 ──────────────────────────


def _parse_period_end(report_period: str) -> date | None:
    """'2026Q2' / '20260630' → 报告期截止日。"""
    p = (report_period or "").strip().upper()
    if len(p) >= 6 and p[4:6] in ("Q1", "Q2", "Q3", "Q4"):
        q_day = {"Q1": "03-31", "Q2": "06-30", "Q3": "09-30", "Q4": "12-31"}[p[4:6]]
        try:
            return date.fromisoformat(f"{p[:4]}-{q_day}")
        except ValueError:
            return None
    if len(p) == 8 and p.isdigit():
        try:
            return datetime.strptime(p, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def check_disclosure_freshness(summary: dict) -> dict:
    """披露新鲜度:最新报告期距今 ≤ DISCLOSURE_FRESH_MAX_DAYS 天。"""
    period = str(summary.get("report_period") or "")
    end = _parse_period_end(period)
    if end is None:
        return {"status": "FAIL", "detail": f"报告期不可解析: {period!r}"}
    age = (date.today() - end).days
    if age > pcfg.DISCLOSURE_FRESH_MAX_DAYS:
        return {"status": "FAIL", "detail": f"最新报告期 {period} 距今 {age} 天,超过 {pcfg.DISCLOSURE_FRESH_MAX_DAYS}"}
    return {"status": "PASS", "detail": f"最新报告期 {period}(距今 {age} 天)"}


def check_single_quarter(summary: dict) -> dict:
    """单季拆解:最新单季净利同比 < 0 = FAIL;不可得 = FAIL(任一字段取不到)。"""
    yoy = summary.get("single_quarter_profit_yoy")
    if yoy is None:
        return {"status": "FAIL", "detail": "单季净利同比不可得(期间不足或差分失败)"}
    pct = yoy * 100
    if pct < 0:
        return {"status": "FAIL", "detail": f"最新单季净利同比 {pct:+.1f}% < 0(伪增长)"}
    return {"status": "PASS", "detail": f"最新单季净利同比 {pct:+.1f}%"}


def check_ex_rights(db: Session, symbol: str) -> dict:
    """EPS 锚突变启发式:相邻可比期 EPS 比值越界 → 判除权/拆股,锚失效强制最新 TTM。

    诚实口径:PanWatch 暂无逐笔分红/拆股数据源,这是 EPS 序列启发式,**不是**
    精确的 60 天除权检测;三价始终按最新报告期数据计算(锚失效语义由此保证)。
    序列不足(检测不可得)按 fail-closed 给 WARN,不给无数据支撑的 PASS。
    """
    items = _eps_sorted(db, symbol)
    if len(items) < 5:
        return {
            "status": "WARN",
            "detail": "EPS 序列不足,锚突变检测不可得;三价已按最新期数据计算",
        }
    keys = [k for k, _ in items]
    vals = [v for _, v in items]
    same_q = [k for k in keys if k[4:6] == keys[-1][4:6]]
    if len(same_q) >= 2:
        j = keys.index(same_q[-2])
        prev, now = vals[j], vals[-1]
        if prev and now and prev > 0 and now > 0:
            ratio = now / prev
            low, high = pcfg.EPS_ANCHOR_MUTATION_BOUNDS
            if ratio < low or ratio > high:
                return {
                    "status": "WARN",
                    "detail": f"检测到 EPS 锚突变(同比 {ratio:.2f}x,越界 [{low}, {high}]),"
                    "判定疑似除权/拆股,EPS 锚失效,三价按最新报告期数据计算",
                    "anchor_invalidated": True,
                }
    return {
        "status": "PASS",
        "detail": "EPS 同比序列启发式未见锚突变(非逐笔除权检测,仅辅助)",
    }


def _eps_sorted(db: Session, symbol: str) -> list[tuple[str, float]]:
    from src.platform.persistence.models import FundamentalsCache

    rows = (
        db.query(FundamentalsCache)
        .filter(FundamentalsCache.symbol == symbol)
        .order_by(FundamentalsCache.report_period.desc(), FundamentalsCache.id.desc())
        .limit(10)
        .all()
    )
    for row in rows:
        summary = row.summary if isinstance(row.summary, dict) else {}
        series = summary.get("eps_series")
        if isinstance(series, dict) and series:
            return sorted((k, float(v)) for k, v in series.items() if v is not None)
    return []


def check_disclosure_window(db: Session, events: list[dict], symbol: str) -> dict:
    """未来 N 个交易日内有该标的预约披露 → WARN(降置信度,不剔除)。

    fail-closed 口径:传入 events 完全不含按标的的披露数据(无任何 symbol 字段,
    当前 PanWatch 的事件日历只有宏观事件)时,检测缺失 → WARN,绝不输出
    「无预约披露」这类无数据支撑的肯定性结论。
    """
    from src.modules.automation.premarket_pipeline.stages import next_trading_days

    per_stock = [
        ev for ev in (events or []) if str(ev.get("symbol") or "").strip()
    ]
    if not per_stock:
        return {
            "status": "WARN",
            "detail": "无按标的的预约披露数据源,披露窗检测缺失(置信度下调,"
            "财报窗内建仓风险未排除)",
        }

    future = next_trading_days(pcfg.DISCLOSURE_WINDOW_TRADING_DAYS)
    end = future[-1] if future else date.today()
    for ev in per_stock:
        if str(ev.get("symbol") or "").strip() != symbol:
            continue
        ev_date = str(ev.get("date") or "")
        if ev_date and end.isoformat() >= ev_date >= date.today().isoformat():
            return {
                "status": "WARN",
                "detail": f"{ev_date} 有预约披露({ev.get('name') or '财报'}),事件窗内置信度下调",
            }
    return {"status": "PASS", "detail": f"未来 {pcfg.DISCLOSURE_WINDOW_TRADING_DAYS} 个交易日内无该标的预约披露"}


# ────────────────────────── 主入口 ──────────────────────────


def verify_candidate(
    db: Session,
    *,
    symbol: str,
    summary: dict | None,
    events: list[dict],
) -> EarningsVerification:
    """六项核验(fail-closed):任一 FAIL → passed=False。"""
    result = EarningsVerification(symbol=symbol)

    # 6. 字段可得性(其余五项的前提)
    if not summary or not isinstance(summary, dict):
        result.checks["field_availability"] = {
            "status": "FAIL",
            "detail": "财务摘要不可得,六项核验无从进行(fail-closed)",
        }
        result.passed = False
        _persist(db, result)
        return result

    required = ("report_period", "latest_net_profit", "eps_latest")
    missing = [k for k in required if summary.get(k) in (None, "")]
    if missing:
        result.checks["field_availability"] = {
            "status": "FAIL",
            "detail": f"关键字段缺失: {missing}",
        }
    else:
        result.checks["field_availability"] = {"status": "PASS", "detail": "关键字段齐全"}

    result.report_period = str(summary.get("report_period") or "")
    result.checks["disclosure_freshness"] = check_disclosure_freshness(summary)
    result.checks["single_quarter"] = check_single_quarter(summary)
    result.checks["ex_rights"] = check_ex_rights(db, symbol)
    result.checks["disclosure_window"] = check_disclosure_window(db, events, symbol)

    try:
        result.checks["dual_source"] = dual_source_check(db, symbol)
    except Exception as e:  # 双源通道整体异常同样 fail-closed
        logger.warning("[盘前流水线][核验] 双源交叉异常 %s: %s", symbol, e)
        result.checks["dual_source"] = {
            "status": "FAIL",
            "detail": f"双源交叉通道异常: {type(e).__name__}: {e}",
        }

    statuses = [c.get("status") for c in result.checks.values()]
    if "FAIL" in statuses:
        result.passed = False
    elif "WARN" in statuses:
        result.passed = True
        result.warnings = [
            f"{name}: {c.get('detail')}"
            for name, c in result.checks.items()
            if c.get("status") == "WARN"
        ]
    else:
        result.passed = True

    result.summary = {
        "report_period": result.report_period,
        "eps_latest": summary.get("eps_latest"),
        "latest_net_profit": summary.get("latest_net_profit"),
        "single_quarter_profit_yoy": summary.get("single_quarter_profit_yoy"),
        "checks": {k: {"status": v.get("status"), "detail": v.get("detail")} for k, v in result.checks.items()},
        "passed": result.passed,
        "verified_at": _now_iso(),
    }
    result.sources = [
        {"source": f"fund:{name}", "as_of": _now_iso()}
        for name in (result.checks.get("dual_source") or {}).get("sources", [])
    ]
    _persist(db, result)
    return result


def _now_iso() -> str:
    from src.platform.scheduling.timezone import beijing_now

    return beijing_now().isoformat(timespec="seconds")


def _persist(db: Session, result: EarningsVerification) -> None:
    """核验结果 + 摘要落 FundamentalsCache(幂等:同 symbol+report_period upsert)。"""
    from src.platform.persistence.models import FundamentalsCache

    if not result.report_period:
        return
    try:
        row = (
            db.query(FundamentalsCache)
            .filter(
                FundamentalsCache.symbol == result.symbol,
                FundamentalsCache.report_period == result.report_period,
            )
            .first()
        )
        if not row:
            row = FundamentalsCache(symbol=result.symbol, report_period=result.report_period)
            db.add(row)
        existing = row.summary if isinstance(row.summary, dict) else {}
        # 必须赋新 dict:原地改 + 赋回同一对象时 SQLAlchemy 不标记脏,commit 会丢更新
        row.summary = {**existing, "verification": result.summary}
        row.sources = result.sources or (row.sources or [])
        row.verified_at = datetime.now()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("[盘前流水线][核验] 核验结果落库失败 %s: %s", result.symbol, e)
