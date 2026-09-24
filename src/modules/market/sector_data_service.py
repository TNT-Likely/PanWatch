"""行业数据服务:每日行业快照采集、行业动量、宏观指标缓存与个股估值序列。

设计约定(与仓库数据可靠性约束一致):
- **fail-soft**:任一外部源失败只跳过该源并标注来源不可用,绝不编造数据;
- **多源交叉**:行业资金流以东财为主源、同花顺为备源,双源可得时经
  ``data_cross_validation.cross_validate`` 校验,偏差超容差标 disputed;
- **幂等**:快照/宏观/估值均按业务键 upsert,重跑不产生重复行;
- **可注入**:各取数步骤是模块级函数,测试可 monkeypatch 或经 ``fetchers``
  参数注入替身,离线可测。

数据源通道:
- 行业榜:复用现有 Discovery 通道(marketdata 包 hot_boards,东财行业板块排名);
- 行业资金流:akshare 东财 ``stock_sector_fund_flow_rank``(主) /
  同花顺 ``stock_fund_flow_industry``(备);
- 涨停数:东财涨停池 ``stock_zt_pool_em`` 按所属行业聚合(official 口径,含ST)/
  全市场快照 ``stock_zh_a_spot_em`` 自算(self_counted 口径,过滤ST前缀);
- 行业K线:``stock_board_industry_hist_em``;板块成分股:Discovery 通道 board_stocks;
- 宏观:marketdata 包 macro 引擎(akshare);估值:``stock_value_em``(主)/
  baostock peTTM·pbMRQ(备)。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy.orm import Session

from src.modules.market.data_cross_validation import (
    DEGRADE_CONSISTENT,
    DEGRADE_MISSING,
    DEGRADE_SINGLE_SOURCE,
    cross_validate,
    make_provenance,
    pick_primary,
    to_float,
)
from src.platform.persistence.models import (
    MacroIndicatorValue,
    SectorSnapshot,
    ValuationSeries,
)
from src.platform.scheduling.timezone import beijing_now

logger = logging.getLogger(__name__)

#: 行业资金流相对偏差容差(东财 vs 同花顺口径不同,容差放宽到 20%)
SECTOR_FLOW_TOLERANCE = 0.20


def _now_iso() -> str:
    return beijing_now().isoformat(timespec="seconds")


def _today_str() -> str:
    """本地(默认 Asia/Shanghai)今天,YYYY-MM-DD。"""
    return beijing_now().strftime("%Y-%m-%d")


def _compact_date(date_str: str) -> str:
    return date_str.replace("-", "")


# ────────────────────────── 取数步骤(默认实现,可注入) ──────────────────────────


def fetch_board_list(limit: int = 100, proxy: str | None = None) -> list[dict]:
    """行业榜:主源 Discovery 通道(东财);不可用时降级同花顺行业资金流页。

    返回 [{code, name, change_pct, turnover, _source}, ...];失败返回 [](fail-soft)。
    ``_source`` 标记行来源,供快照血统(meta.provenance)如实标注。
    """
    out: list[dict] = []
    try:
        from src.platform.marketdata.marketdata_client import get_market_data

        boards = get_market_data().hot_boards(
            market="CN", mode="gainers", limit=limit, proxy=proxy
        )
    except Exception as e:
        logger.warning("[行业快照] Discovery 行业榜获取失败: %s", e)
        boards = []
    for it in boards or []:
        code = str(getattr(it, "code", "") or "").strip()
        name = str(getattr(it, "name", "") or "").strip()
        if not code or not name:
            continue
        out.append(
            {
                "code": code,
                "name": name,
                "change_pct": to_float(getattr(it, "change_pct", None)),
                "turnover": to_float(getattr(it, "turnover", None)),
                "_source": "discovery.hot_boards",
            }
        )
    if out:
        return out[:limit]
    ths_rows = _ths_board_list(limit)
    if ths_rows:
        logger.warning(
            "[行业快照] Discovery 行业榜不可用,降级同花顺行业榜 boards=%d(degrade=1)",
            len(ths_rows),
        )
    return ths_rows


def _ak():
    """惰性 import akshare;缺库抛 ImportError 由调用方 fail-soft。"""
    import akshare as ak

    return ak


def _df_records(df) -> list[dict]:
    """DataFrame → list[dict];df 为空/无列返回 []。"""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        return df.to_dict("records")
    except Exception:
        return []


#: 同花顺页面直连 UA(境内站,不走代理)
_THS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/126 Safari/537.36"
)


def _ths_get(url: str, referer: str) -> str:
    """同花顺页面直连取数(境内站禁用代理);8s 超时,重试 1 次;GBK 解码。

    失败抛 RuntimeError,由调用方 fail-soft。
    """
    import requests

    last_err: Exception | None = None
    for _ in range(2):
        try:
            r = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": _THS_UA, "Referer": referer},
                proxies={"http": None, "https": None},
            )
            r.raise_for_status()
            r.encoding = "gbk"
            return r.text
        except Exception as e:  # noqa: PERF203 - 重试语义需要循环内捕获
            last_err = e
    raise RuntimeError(f"ths_fetch_failed: {last_err}")


def _ths_hyzjl_rows() -> list[dict]:
    """同花顺行业资金流页(服务端渲染表格)→ [{name, change_pct, net_e8, companies}]。

    该页是行业级资金流的可靠服务端渲染源;akshare 的
    ``stock_fund_flow_industry`` 解析器已与页面结构脱节(列数不匹配),故直析。
    """
    import io

    import pandas as pd

    html = _ths_get("https://data.10jqka.com.cn/funds/hyzjl/", "https://data.10jqka.com.cn/")
    tables = pd.read_html(io.StringIO(html))
    df = max(tables, key=len)
    rows: list[dict] = []
    for rec in _df_records(df):
        name = str(rec.get("行业") or "").strip()
        if not name:
            continue
        pct_raw = str(rec.get("涨跌幅") or "").replace("%", "").strip()
        rows.append(
            {
                "name": name,
                "change_pct": to_float(pct_raw or None),
                "net_e8": to_float(rec.get("净额(亿)")),
                "companies": to_float(rec.get("公司家数")),
            }
        )
    return rows


def _ths_name_codes() -> dict[str, str]:
    """同花顺行业名→板块代码(akshare 名单接口);失败返回 {}。"""
    try:
        ak = _ak()
        df = ak.stock_board_industry_name_ths()
    except Exception as e:
        logger.warning("[行业快照] 同花顺行业名单获取失败: %s", e)
        return {}
    out: dict[str, str] = {}
    for rec in _df_records(df):
        name = str(rec.get("name") or "").strip()
        code = str(rec.get("code") or "").strip()
        if name and code:
            out[name] = code
    return out


def _ths_board_list(limit: int) -> list[dict]:
    """同花顺行业榜:资金流页(涨跌幅/净流入)+ 名单页(代码)按名称对齐。

    名称对不上的行直接丢弃(无代码无法支撑成分股链路,不给伪代码)。
    """
    try:
        rows = _ths_hyzjl_rows()
    except Exception as e:
        logger.warning("[行业快照] 同花顺行业页获取失败: %s", e)
        return []
    if not rows:
        return []
    codes = _ths_name_codes()
    out: list[dict] = []
    for r in rows[:limit]:
        code = codes.get(r["name"], "")
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": r["name"],
                "change_pct": r["change_pct"],
                "turnover": None,
                "_source": "ths.hyzjl",
            }
        )
    return out


def fetch_em_sector_flow() -> dict[str, dict]:
    """行业资金流主源:东财行业资金流排行(今日)。

    返回 {板块名: {"main": 主力净额, "small": 小单净额}};失败返回 {}(fail-soft)。
    """
    try:
        ak = _ak()
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    except Exception as e:
        logger.warning("[行业快照] 东财行业资金流获取失败: %s", e)
        return {}
    out: dict[str, dict] = {}
    for row in _df_records(df):
        name = str(row.get("名称") or "").strip()
        if not name:
            continue
        main = to_float(row.get("今日主力净流入-净额"))
        small = to_float(row.get("今日小单净流入-净额"))
        if main is None and small is None:
            continue
        out[name] = {"main": main, "small": small}
    return out


def fetch_ths_sector_flow() -> dict[str, dict]:
    """行业资金流备源:同花顺行业资金流页(hyzjl)服务端表格直析。

    返回 {行业名: {"main": 净额(元)}}。注意口径:同花顺净额是全口径净流入
    (非东财"主力"口径,页值单位为亿,此处换算为元对齐东财),caliber 记
    ths_total,交叉时容差放宽。失败返回 {}。
    """
    try:
        rows = _ths_hyzjl_rows()
    except Exception as e:
        logger.warning("[行业快照] 同花顺行业资金流获取失败: %s", e)
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if r["net_e8"] is None:
            continue
        out[r["name"]] = {"main": round(r["net_e8"] * 1e8, 2)}
    return out


def fetch_official_limit_up(trade_date: str) -> dict[str, int]:
    """涨停数主源:东财涨停池按「所属行业」聚合计数(official 口径,含ST)。

    Args:
        trade_date: YYYY-MM-DD(接口侧转 YYYYMMDD;当日未收盘时池可能为空)。
    返回 {行业名: 涨停家数};失败/空池返回 {}(fail-soft)。
    """
    try:
        ak = _ak()
        df = ak.stock_zt_pool_em(date=_compact_date(trade_date))
    except Exception as e:
        logger.warning("[行业快照] 东财涨停池获取失败: %s", e)
        return {}
    counts: dict[str, int] = {}
    for row in _df_records(df):
        industry = str(row.get("所属行业") or "").strip()
        if not industry:
            continue
        counts[industry] = counts.get(industry, 0) + 1
    return counts


def _limit_up_ratio(code: str) -> float | None:
    """按板块段位返回涨停涨幅比例:创业板/科创板 20%,北交所 30%,其余 10%。"""
    c = (code or "").strip()
    if c.startswith(("300", "301", "302", "688", "689")):
        return 1.20
    if c.startswith(("83", "87", "88", "92", "43")):
        return 1.30
    if c and c[0] in ("0", "3", "6"):
        return 1.10
    return None


def detect_limit_up(name: str, code: str, price, prev_close) -> bool:
    """自算涨停判定:昨收精确判定 round(昨收*比例,2) 等于最新价,名称 ST 前缀过滤。

    比例按代码段位取 10%/20%/30%(主板/创业板科创板/北交所);价格比较允许
    半分位误差(浮点)。任一价格缺失、ST/*ST/退市整理前缀、比例未知 → False。
    """
    pname = (name or "").strip().upper()
    if pname.startswith(("ST", "*ST", "SST", "退")):
        return False
    p = to_float(price)
    prev = to_float(prev_close)
    if p is None or prev is None or prev <= 0 or p <= 0:
        return False
    ratio = _limit_up_ratio(code)
    if ratio is None:
        return False
    limit_price = round(prev * ratio, 2)
    return abs(p - limit_price) < 0.005


def fetch_market_spot() -> list[dict]:
    """全市场快照(备源涨停自算的输入):akshare 东财全市场即时行情。

    返回 [{code, name, price, prev_close}, ...];失败返回 [](fail-soft)。
    """
    try:
        ak = _ak()
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        logger.warning("[行业快照] 全市场快照获取失败: %s", e)
        return []
    out: list[dict] = []
    for row in _df_records(df):
        code = str(row.get("代码") or "").strip()
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": str(row.get("名称") or "").strip(),
                "price": to_float(row.get("最新价")),
                "prev_close": to_float(row.get("昨收")),
            }
        )
    return out


#: 取数步骤名 → 模块函数名(调用时经 globals() 解析,monkeypatch 可替换)
_FETCHER_ATTRS: dict[str, str] = {
    "board_list": "fetch_board_list",
    "em_flow": "fetch_em_sector_flow",
    "ths_flow": "fetch_ths_sector_flow",
    "official_limit_up": "fetch_official_limit_up",
    "market_spot": "fetch_market_spot",
}

#: 默认取数步骤名:collect_daily_snapshot(fetchers=...) 按名覆盖(测试注入用)
FETCHER_KEYS = tuple(_FETCHER_ATTRS)


def _resolve_fetchers(
    fetchers: dict[str, Callable[..., Any]] | None,
) -> dict[str, Callable[..., Any]]:
    """按调用时名称解析默认取数步骤(模块属性查找,monkeypatch 可替换),再合并覆盖项。"""
    steps: dict[str, Callable[..., Any]] = {
        key: globals()[attr] for key, attr in _FETCHER_ATTRS.items()
    }
    if fetchers:
        for key, fn in fetchers.items():
            if key not in FETCHER_KEYS:
                raise ValueError(f"未知取数步骤: {key}(可选: {FETCHER_KEYS})")
            steps[key] = fn
    return steps


# ────────────────────────── 每日行业快照 ──────────────────────────


def _self_counted_limit_up_total(rows: list[dict]) -> int | None:
    """全市场快照自算涨停总数(备源口径,过滤ST)。无快照数据返回 None。"""
    if not rows:
        return None
    return sum(
        1
        for r in rows
        if detect_limit_up(r.get("name", ""), r.get("code", ""), r.get("price"), r.get("prev_close"))
    )


def collect_daily_snapshot(
    db: Session,
    *,
    snapshot_date: str | None = None,
    board_limit: int = 100,
    fetchers: dict[str, Callable[..., Any]] | None = None,
) -> dict:
    """采集当日行业快照:行业榜 + 双源资金流交叉校验 + 涨停数,幂等 upsert。

    各字段血统(source/as_of/caliber/degrade_level)与交叉校验结论写入行 meta。
    Args:
        db: SQLAlchemy 会话(调用方管理事务生命周期,这里统一 commit)。
        snapshot_date: 默认本地今天。
        board_limit: 行业榜取前 N 个板块。
        fetchers: 覆盖默认取数步骤(键见 FETCHER_KEYS),测试注入用。
    Returns:
        汇总 dict:snapshot_date/upserted/sources_available/degraded 等。
    """
    day = snapshot_date or _today_str()
    as_of = _now_iso()
    steps = _resolve_fetchers(fetchers)

    boards: list[dict] = []
    try:
        boards = steps["board_list"](limit=board_limit) or []
    except Exception as e:  # 注入的实现抛错同样 fail-soft
        logger.warning("[行业快照] 行业榜步骤异常: %s", e)

    em_flow: dict = {}
    ths_flow: dict = {}
    official_zt: dict = {}
    spot_rows: list[dict] = []
    source_errors: dict[str, str] = {}
    for key in ("em_flow", "ths_flow"):
        try:
            if key == "em_flow":
                em_flow = steps[key]() or {}
            else:
                ths_flow = steps[key]() or {}
        except Exception as e:
            source_errors[key] = f"{type(e).__name__}: {e}"
            logger.warning("[行业快照] %s 步骤异常: %s", key, e)
    try:
        official_zt = steps["official_limit_up"](day) or {}
    except Exception as e:
        source_errors["official_limit_up"] = f"{type(e).__name__}: {e}"
        logger.warning("[行业快照] official_limit_up 步骤异常: %s", e)
    try:
        spot_rows = steps["market_spot"]() or []
    except Exception as e:
        source_errors["market_spot"] = f"{type(e).__name__}: {e}"
        logger.warning("[行业快照] market_spot 步骤异常: %s", e)

    sources_available = {
        "discovery_boards": bool(boards),
        "em_flow": bool(em_flow),
        "ths_flow": bool(ths_flow),
        "official_limit_up": bool(official_zt),
        "market_spot": bool(spot_rows),
    }

    self_zt_total = _self_counted_limit_up_total(spot_rows)

    upserted = 0
    degrade_rows: list[int] = []
    for idx, board in enumerate(boards):
        code = board["code"]
        name = board["name"]

        # ── 资金流双源交叉(主源东财,备源同花顺;按名称精确匹配) ──
        em = em_flow.get(name) or {}
        ths = ths_flow.get(name) or {}
        main_values = []
        if em.get("main") is not None:
            main_values.append(
                {"value": em["main"], "source": "ak.stock_sector_fund_flow_rank", "caliber": "em_main"}
            )
        if ths.get("main") is not None:
            main_values.append(
                {"value": ths["main"], "source": "ak.stock_fund_flow_industry", "caliber": "ths_total"}
            )
        main_verdict = cross_validate(main_values, tol=SECTOR_FLOW_TOLERANCE)
        main_value, main_degrade = pick_primary(main_values, main_verdict)

        small_values = []
        if em.get("small") is not None:
            small_values.append(
                {"value": em["small"], "source": "ak.stock_sector_fund_flow_rank", "caliber": "em_main"}
            )
        small_verdict = cross_validate(small_values, tol=SECTOR_FLOW_TOLERANCE)
        small_value, small_degrade = pick_primary(small_values, small_verdict)

        # ── 涨停数:主源官方池按行业聚合;备源仅有全市场自算总数 ──
        if name in official_zt:
            zt_count, zt_caliber, zt_degrade = int(official_zt[name]), "official", DEGRADE_CONSISTENT
        else:
            zt_count, zt_caliber = None, ""
            zt_degrade = DEGRADE_MISSING
            if self_zt_total is not None:
                zt_degrade = DEGRADE_SINGLE_SOURCE

        row_degrade = max(main_degrade, small_degrade, zt_degrade)
        degrade_rows.append(row_degrade)

        board_source = str(board.get("_source") or "discovery.hot_boards")
        board_caliber = "ths_board_rank" if board_source == "ths.hyzjl" else "em_board_rank"
        meta = {
            "provenance": {
                "change_pct": make_provenance(
                    value=board.get("change_pct"),
                    source=board_source,
                    as_of=as_of,
                    caliber=board_caliber,
                    degrade_level=DEGRADE_SINGLE_SOURCE if boards else DEGRADE_MISSING,
                ),
                "turnover": make_provenance(
                    value=board.get("turnover"),
                    source=board_source,
                    as_of=as_of,
                    caliber=board_caliber,
                    degrade_level=DEGRADE_SINGLE_SOURCE if boards else DEGRADE_MISSING,
                ),
                "main_net_inflow": {
                    **make_provenance(
                        value=main_value,
                        source=(main_values[0]["source"] if main_values else ""),
                        as_of=as_of,
                        caliber=(main_values[0]["caliber"] if main_values else ""),
                        degrade_level=main_degrade,
                    ),
                    "cross_validation": {
                        "status": main_verdict["status"],
                        "reason": main_verdict["reason"],
                        "deviation": main_verdict["deviation"],
                        "sources": main_verdict["sources"],
                    },
                },
                "small_net_inflow": make_provenance(
                    value=small_value,
                    source=(small_values[0]["source"] if small_values else ""),
                    as_of=as_of,
                    caliber=(small_values[0]["caliber"] if small_values else ""),
                    degrade_level=small_degrade,
                ),
                "limit_up_count": make_provenance(
                    value=zt_count,
                    source="ak.stock_zt_pool_em" if zt_count is not None else "",
                    as_of=as_of,
                    caliber=zt_caliber,
                    degrade_level=zt_degrade,
                ),
            },
            "sources_available": sources_available,
            "limit_up": {
                "official_total": sum(official_zt.values()) if official_zt else None,
                "self_counted_total": self_zt_total,
                "note": (
                    "official=涨停池口径含ST; self_counted=全市场快照自算,过滤ST前缀"
                ),
            },
            "board_rank_in_discovery": idx + 1,
        }
        if source_errors:
            meta["source_errors"] = source_errors

        row = (
            db.query(SectorSnapshot)
            .filter(SectorSnapshot.snapshot_date == day, SectorSnapshot.board_code == code)
            .first()
        )
        if not row:
            row = SectorSnapshot(snapshot_date=day, board_code=code)
            db.add(row)
        row.board_name = name
        row.change_pct = board.get("change_pct")
        row.turnover = board.get("turnover")
        row.limit_up_count = zt_count
        row.limit_up_caliber = zt_caliber
        row.main_net_inflow = main_value
        row.small_net_inflow = small_value
        row.rank = None  # 先置空,落库前统一按主力净流入排名
        row.meta = meta
        row.updated_at = datetime.now()
        upserted += 1

    # rank:当日主力净流入降序(有值者参与;同值按行业榜次序稳定排序)
    with_value = [
        (r, r.main_net_inflow)
        for r in db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == day)
        .all()
        if r.main_net_inflow is not None
    ]
    for rank, (r, _v) in enumerate(
        sorted(with_value, key=lambda x: x[1], reverse=True), start=1
    ):
        r.rank = rank

    db.commit()
    summary = {
        "snapshot_date": day,
        "upserted": upserted,
        "sources_available": sources_available,
        "source_errors": source_errors,
        "limit_up": {
            "official_total": sum(official_zt.values()) if official_zt else None,
            "self_counted_total": self_zt_total,
        },
        "degraded": any(d > DEGRADE_CONSISTENT for d in degrade_rows),
        "max_degrade_level": max(degrade_rows) if degrade_rows else DEGRADE_MISSING,
    }
    logger.info(
        "[行业快照] 采集完成: date=%s boards=%s degraded=%s sources=%s",
        day,
        upserted,
        summary["degraded"],
        {k: v for k, v in sources_available.items() if v},
    )
    return summary


# ────────────────────────── 行业动量 ──────────────────────────


def _pct_changes_from_closes(closes: list[float]) -> dict[str, float | None]:
    """由收盘价序列(旧→新)算 5/10/20 日涨幅%。不足窗口时该项 None。"""
    out: dict[str, float | None] = {"d5": None, "d10": None, "d20": None}
    if not closes:
        return out
    last = closes[-1]
    for key, window in (("d5", 5), ("d10", 10), ("d20", 20)):
        if len(closes) > window and closes[-1 - window]:
            out[key] = round((last / closes[-1 - window] - 1.0) * 100.0, 4)
    return out


def _pct_changes_from_snapshot_rows(pcts: list[float]) -> dict[str, float | None]:
    """由快照历史逐日涨跌幅序列(旧→新,最近在末尾)复利合成 5/10/20 日涨幅%。

    快照序列天然缺起点收盘,这里用「最近 N 个逐日涨跌幅连乘」近似
    (以 N 日前的快照收盘为基);序列不足 2 个点时该项 None。
    """
    out: dict[str, float | None] = {"d5": None, "d10": None, "d20": None}
    for key, window in (("d5", 5), ("d10", 10), ("d20", 20)):
        if len(pcts) < 2:
            continue
        seg = pcts[-window:]
        factor = 1.0
        for p in seg:
            factor *= 1.0 + (p or 0.0) / 100.0
        out[key] = round((factor - 1.0) * 100.0, 4)
    return out


def fetch_board_hist_closes(board_name: str, days: int = 40) -> list[float]:
    """主源:东财行业指数历史日K收盘价(旧→新);不可用降级同花顺行业指数。失败返回 []。"""
    if not board_name:
        return []
    try:
        ak = _ak()
        end = beijing_now()
        start = end - timedelta(days=max(days * 2, 30))
        df = ak.stock_board_industry_hist_em(
            symbol=board_name,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            period="日k",
            adjust="",
        )
    except Exception as e:
        logger.warning("[行业动量] 行业K线获取失败 name=%s: %s", board_name, e)
        return _ths_board_closes(board_name, days)
    closes: list[float] = []
    for row in _df_records(df):
        c = to_float(row.get("收盘"))
        if c is not None:
            closes.append(c)
    return closes if closes else _ths_board_closes(board_name, days)


def _ths_board_closes(board_name: str, days: int = 40) -> list[float]:
    """行业K线备源:同花顺行业指数日K收盘价(旧→新)。失败返回 [](fail-soft)。"""
    if not board_name:
        return []
    try:
        ak = _ak()
        end = beijing_now()
        start = end - timedelta(days=max(days * 2, 30))
        df = ak.stock_board_industry_index_ths(
            symbol=board_name,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as e:
        logger.warning("[行业动量] 同花顺行业指数K线失败 name=%s: %s", board_name, e)
        return []
    closes: list[float] = []
    for row in _df_records(df):
        c = to_float(row.get("收盘价"))
        if c is not None:
            closes.append(c)
    return closes


def fetch_board_constituents(board_code: str, limit: int = 10) -> list[dict]:
    """备源:Discovery 通道取板块成交额前 N 成分股;不可用降级同花顺详情页。失败返回 []。"""
    if not board_code:
        return []
    out: list[dict] = []
    try:
        from src.platform.marketdata.marketdata_client import get_market_data

        stocks = get_market_data().board_stocks(
            board_code=board_code, mode="turnover", limit=limit
        )
    except Exception as e:
        logger.warning("[行业动量] 板块成分股获取失败 code=%s: %s", board_code, e)
        stocks = []
    for it in stocks or []:
        symbol = str(getattr(it, "symbol", "") or "").strip()
        if not symbol:
            continue
        out.append(
            {
                "symbol": symbol,
                "turnover": to_float(getattr(it, "turnover", None)),
            }
        )
    if out:
        return out[:limit]
    ths_rows = _ths_board_constituents(board_code, limit)
    if ths_rows:
        logger.warning(
            "[行业动量] 成分股降级同花顺详情页 code=%s n=%d(degrade=1)",
            board_code,
            len(ths_rows),
        )
    return ths_rows


def _ths_board_constituents(board_code: str, limit: int) -> list[dict]:
    """同花顺板块详情页成分股(服务端表格)。

    code 仅接受 6 位数字(同花顺板块代码),拒绝其他取值拼接 URL。
    """
    import io

    import pandas as pd

    if not re.fullmatch(r"\d{6}", str(board_code)):
        return []
    try:
        html = _ths_get(
            f"https://q.10jqka.com.cn/thshy/detail/code/{board_code}/",
            "https://q.10jqka.com.cn/",
        )
        tables = pd.read_html(io.StringIO(html))
    except Exception as e:
        logger.warning("[行业动量] 同花顺详情页获取失败 code=%s: %s", board_code, e)
        return []
    df = max(tables, key=len)
    out: list[dict] = []
    for rec in _df_records(df):
        symbol = str(rec.get("代码") or "").strip()
        if not symbol:
            continue
        turnover_raw = str(rec.get("成交额") or "").replace("亿", "").strip()
        out.append({"symbol": symbol, "turnover": to_float(turnover_raw or None)})
    return out[:limit]


def sector_momentum(
    db: Session,
    board_code: str,
    *,
    days: int = 40,
    top_constituents: int = 10,
) -> dict:
    """行业动量:5/10/20 日涨幅,主备兜底三级降级。

    - 主源:akshare 行业历史K线(源 "ak.stock_board_industry_hist_em",degrade 0);
    - 备源:板块成交额前 N 成分股经现有 klines 通道,按成交额加权合成
      (源 "board_constituents_kline",degrade 1;权重用成交额近似「市值前N」,
      成分榜单无市值字段,口径记 turnover_weighted);
    - 兜底:快照库该行业历史行涨跌幅复利合成(源 "sector_snapshots_history",
      degrade 2)。

    degrade_level 这里是「源层级」语义:0=官方K线,1=合成,2=快照兜底,
    3=全部不可得(与 data_cross_validation 的校验语义是两套刻度,勿混读)。

    Returns:
        {board_code, board_name, momentum:{d5,d10,d20}, source, degrade_level, as_of}
        全部不可得时 momentum 各项为 None、degrade_level=3(fail-soft 不抛错)。
    """
    code = (board_code or "").strip()
    as_of = _now_iso()
    result: dict[str, Any] = {
        "board_code": code,
        "board_name": "",
        "momentum": {"d5": None, "d10": None, "d20": None},
        "source": "",
        "degrade_level": DEGRADE_MISSING,
        "as_of": as_of,
    }
    if not code:
        return result

    # board_name:快照库最近一条 → Discovery 行业榜(顺序先库后网,库是已落地口径)
    board_name = ""
    latest = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.board_code == code)
        .order_by(SectorSnapshot.snapshot_date.desc(), SectorSnapshot.id.desc())
        .first()
    )
    if latest:
        board_name = latest.board_name or ""
        result["board_name"] = board_name
    if not board_name:
        for b in fetch_board_list(limit=100):
            if b["code"] == code:
                board_name = b["name"]
                result["board_name"] = board_name
                break

    # ── 主源:行业历史K线 ──
    closes = fetch_board_hist_closes(board_name, days=days) if board_name else []
    if len(closes) >= 6:
        result["momentum"] = _pct_changes_from_closes(closes)
        result["source"] = "ak.stock_board_industry_hist_em"
        result["degrade_level"] = DEGRADE_CONSISTENT
        return result

    # ── 备源:成分股加权合成 ──
    members = fetch_board_constituents(code, limit=top_constituents)
    if members:
        synth = _synthesize_from_constituents(members, days=days)
        if any(v is not None for v in synth.values()):
            result["momentum"] = synth
            result["source"] = "board_constituents_kline"
            result["degrade_level"] = DEGRADE_SINGLE_SOURCE
            return result

    # ── 兜底:快照库历史行 ──
    # 取「最近」N 行(desc + limit),再 reverse 回旧→新序列;若按 asc 取最旧 N 行,
    # 历史超过 N 行后会把数月前的动量当最新值输出(as_of=now 无法暴露陈旧)。
    rows = (
        db.query(SectorSnapshot)
        .filter(
            SectorSnapshot.board_code == code,
            SectorSnapshot.change_pct.isnot(None),
        )
        .order_by(SectorSnapshot.snapshot_date.desc())
        .limit(days)
        .all()
    )
    rows.reverse()
    pcts = [float(r.change_pct) for r in rows if r.change_pct is not None]
    if len(pcts) >= 2:
        result["momentum"] = _pct_changes_from_snapshot_rows(pcts)
        result["source"] = "sector_snapshots_history"
        result["degrade_level"] = 2
        result["momentum_window_end"] = rows[-1].snapshot_date if rows else ""
        return result

    return result


def _synthesize_from_constituents(members: list[dict], *, days: int) -> dict[str, float | None]:
    """成分股经现有 klines 通道取日K,按成交额权重(缺权重等权)合成板块涨幅。"""
    try:
        from src.platform.marketdata.marketdata_client import get_market_data
    except Exception as e:  # pragma: no cover - marketdata 必装,防御兜底
        logger.warning("[行业动量] klines 通道不可用: %s", e)
        return {"d5": None, "d10": None, "d20": None}

    md = get_market_data()
    weights: list[float] = []
    series: list[list[float]] = []
    for m in members:
        try:
            bars = md.klines(m["symbol"], market="CN", days=days)
        except Exception as e:
            logger.debug("[行业动量] 成分股K线失败 symbol=%s: %s", m["symbol"], e)
            bars = []
        closes = [float(b.close) for b in bars or [] if b.close]
        if len(closes) < 2:
            continue
        series.append(closes)
        weights.append(m.get("turnover") if m.get("turnover") else 0.0)
    if not series:
        return {"d5": None, "d10": None, "d20": None}

    total_w = sum(weights)
    if total_w <= 0:
        weights = [1.0] * len(series)
        total_w = float(len(series))

    out: dict[str, float | None] = {"d5": None, "d10": None, "d20": None}
    for key, window in (("d5", 5), ("d10", 10), ("d20", 20)):
        acc = 0.0
        w_sum = 0.0
        for closes, w in zip(series, weights):
            if len(closes) > window and closes[-1 - window]:
                acc += w * ((closes[-1] / closes[-1 - window] - 1.0) * 100.0)
                w_sum += w
        if w_sum > 0:
            out[key] = round(acc / w_sum, 4)
    return out


# ────────────────────────── 宏观指标缓存 ──────────────────────────


def refresh_macro_cache(db: Session) -> dict:
    """刷新宏观指标缓存:marketdata macro 引擎(akshare 多指标)→ 幂等 upsert。

    按 (indicator, period) 唯一;同期新值覆盖旧值,新期新增行。fail-soft。
    """
    try:
        from src.platform.marketdata.marketdata_client import get_market_data

        resp = get_market_data().macro()
    except Exception as e:
        logger.warning("[宏观缓存] macro 引擎异常: %s", e)
        return {"ok": False, "upserted": 0, "error": f"{type(e).__name__}: {e}"}

    ok = bool(getattr(resp, "ok", False))
    items = list(getattr(resp, "data", None) or [])
    if not ok and not items:
        err = str(getattr(resp, "error", "") or "macro 不可用")
        logger.warning("[宏观缓存] macro 全源失败: %s", err)
        return {"ok": False, "upserted": 0, "error": err}

    as_of = _now_iso()
    upserted = 0
    for item in items:
        name = str(getattr(item, "name", "") or "").strip()
        period = str(getattr(item, "period", "") or "").strip()
        value = to_float(getattr(item, "value", None))
        if not name or not period or value is None:
            continue  # 缺值不落库,绝不编造
        row = (
            db.query(MacroIndicatorValue)
            .filter(
                MacroIndicatorValue.indicator == name,
                MacroIndicatorValue.period == period,
            )
            .first()
        )
        if not row:
            row = MacroIndicatorValue(indicator=name, period=period)
            db.add(row)
        row.value = value
        row.publish_date = str(getattr(item, "publish_date", "") or "")
        row.source = str(getattr(item, "source", "") or "")
        row.as_of = as_of
        row.updated_at = datetime.now()
        upserted += 1
    db.commit()
    logger.info("[宏观缓存] 刷新完成: ok=%s upserted=%s", ok, upserted)
    return {"ok": ok, "upserted": upserted, "error": "" if ok else "partial_sources"}


# ────────────────────────── 个股估值序列 ──────────────────────────


def _baostock_code(symbol: str) -> str:
    """A 股代码 → baostock 前缀代码(sh./sz.)。北交所等不支持的返回空。"""
    c = (symbol or "").strip()
    if c.startswith(("6", "9", "5")):
        return f"sh.{c}"
    if c.startswith(("0", "2", "3")):
        return f"sz.{c}"
    return ""


def _valuation_from_eastmoney(symbol: str, since: str | None) -> list[dict]:
    """主源:东财估值分析历史(数据日期/PE(TTM)/市净率)。

    since: YYYY-MM-DD,只保留晚于该日的行(增量);None 表示全量(截尾 250 行)。
    """
    ak = _ak()
    df = ak.stock_value_em(symbol=symbol)
    out: list[dict] = []
    for row in _df_records(df):
        d = row.get("数据日期")
        trade_date = d.isoformat() if hasattr(d, "isoformat") else str(d or "")[:10]
        if len(trade_date) != 10:
            continue
        if since and trade_date <= since:
            continue
        out.append(
            {
                "trade_date": trade_date,
                "pe_ttm": to_float(row.get("PE(TTM)")),
                "pb": to_float(row.get("市净率")),
                "source": "ak.stock_value_em",
            }
        )
    return out[-250:]


def _load_baostock():
    """惰性导入并登录 baostock;任一步失败抛 RuntimeError(原因进 errors,不静默)。

    baostock 未登录时查询返回 BSERR_NO_LOGIN 且只 print 到 stdout,不会抛错,
    必须显式 login 并核对 login 返回的 error_code。
    """
    try:
        import baostock as bs
    except ImportError as e:
        raise RuntimeError(f"baostock 未安装(未声明或未 pip install): {e}") from e
    try:
        login = bs.login()
    except Exception as e:
        raise RuntimeError(f"baostock login 异常: {e}") from e
    code = str(getattr(login, "error_code", ""))
    if code and code != "0":
        msg = getattr(login, "error_msg", "")
        raise RuntimeError(f"baostock 登录失败: {code} {msg}")
    return bs


def _valuation_from_baostock(symbol: str, start: str, end: str) -> list[dict]:
    """备源:baostock 日线 peTTM/pbMRQ。

    login → 查询 → finally logout;查询 error_code != '0'(含未登录
    BSERR_NO_LOGIN)抛 RuntimeError 带错误码,由调用方记入 errors。缺库/
    失败一律抛异常由调用方 fail-soft,不静默返回空表。
    """
    bs = _load_baostock()
    code = _baostock_code(symbol)
    if not code:
        raise RuntimeError(f"baostock 不支持该代码: {symbol}")
    try:
        lg = bs.query_history_k_data_plus(
            code,
            "date,peTTM,pbMRQ",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )
        lg_code = str(getattr(lg, "error_code", ""))
        if lg_code and lg_code != "0":
            msg = getattr(lg, "error_msg", "")
            raise RuntimeError(f"baostock 查询失败: {lg_code} {msg}")
        out: list[dict] = []
        while lg.next():
            row = lg.get_row_data()
            if len(row) < 3 or not row[0]:
                continue
            out.append(
                {
                    "trade_date": row[0][:10],
                    "pe_ttm": to_float(row[1]),
                    "pb": to_float(row[2]),
                    "source": "baostock",
                }
            )
        return out
    finally:
        try:
            bs.logout()
        except Exception as e:  # logout 失败不影响主流程,只留痕
            logger.debug("[估值序列] baostock logout 异常: %s", e)


def refresh_valuation_series(
    db: Session,
    symbols: list[str],
    *,
    lookback_days: int = 30,
) -> dict:
    """增量刷新个股估值序列(PE-TTM/PB):东财估值历史(主)→ baostock(备)。

    增量口径:只补该标的现有最大 trade_date 之后的行;主源失败时备源兜底
    (备源需全量重拉后按唯一键 upsert,天然幂等)。单只失败不影响其余(fail-soft)。
    """
    from src.platform.persistence.models import ValuationSeries as _VS

    end = beijing_now()
    start = end - timedelta(days=max(lookback_days * 3, 90))
    report: dict[str, dict] = {}
    for raw in symbols or []:
        symbol = str(raw or "").strip()
        if not symbol:
            continue
        latest = (
            db.query(_VS.trade_date)
            .filter(_VS.symbol == symbol)
            .order_by(_VS.trade_date.desc())
            .first()
        )
        since = latest[0] if latest else None

        rows: list[dict] = []
        source_used = ""
        errors: list[str] = []
        try:
            rows = _valuation_from_eastmoney(symbol, since)
            source_used = "ak.stock_value_em"
        except Exception as e:
            errors.append(f"eastmoney: {type(e).__name__}: {e}")
            logger.warning("[估值序列] 东财估值失败 symbol=%s: %s", symbol, e)
            try:
                rows = _valuation_from_baostock(
                    symbol,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                )
                source_used = "baostock"
            except Exception as e2:
                errors.append(f"baostock: {type(e2).__name__}: {e2}")
                logger.warning("[估值序列] baostock 兜底失败 symbol=%s: %s", symbol, e2)

        upserted = 0
        for item in rows:
            if item["trade_date"] <= (since or ""):
                continue
            row = (
                db.query(_VS)
                .filter(
                    _VS.symbol == symbol,
                    _VS.trade_date == item["trade_date"],
                )
                .first()
            )
            if not row:
                row = _VS(symbol=symbol, trade_date=item["trade_date"])
                db.add(row)
            row.pe_ttm = item["pe_ttm"]
            row.pb = item["pb"]
            row.source = item["source"]
            row.updated_at = datetime.now()
            upserted += 1
        if upserted:
            db.commit()
        report[symbol] = {
            "upserted": upserted,
            "source": source_used if upserted else "",
            "errors": errors,
            "degraded": bool(errors) or source_used == "baostock",
        }
        if report[symbol]["degraded"] and errors:
            logger.warning(
                "[估值序列] symbol=%s 降级(source=%s): %s", symbol, source_used, errors
            )
    return {"as_of": _now_iso(), "symbols": report}
