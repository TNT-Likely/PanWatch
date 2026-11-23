"""Project only facts already available through the device API into a chat turn."""

from datetime import UTC, date, datetime
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.orm import Session

from src.modules.mobile.fact_snapshots import FactSnapshot, FactTarget
from src.modules.mobile.portfolio_history import build_portfolio_lookback
from src.modules.mobile.portfolio_overview import build_overview
from src.modules.mobile.symbol_facts import load_symbol_facts
from src.platform.persistence.models import MobileFactTask
from src.web.errors import api_error


class PortfolioContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["portfolio"]


class SymbolContext(FactTarget):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["symbol"]


class TaskContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["fact_task"]
    task_id: UUID


Context = Annotated[PortfolioContext | SymbolContext | TaskContext, Field(discriminator="kind")]
context_adapter = TypeAdapter(Context | None)
snapshot_adapter = TypeAdapter(FactSnapshot)


class Source(BaseModel):
    title: str
    detail: str
    sensitive_amounts: bool | None = None


EvidenceKind = Literal["definition", "overview", "pnl", "position", "contribution", "quote", "indicator", "close", "disclosure", "brief_status", "basis", "coverage", "lookback"]


class Evidence(Source):
    kind: EvidenceKind = Field(exclude=True)
    metric: str | None = Field(default=None, exclude=True)
    observed_on: date | None = Field(default=None, exclude=True)


def _source(kind: EvidenceKind, title: str, detail: str, *, metric: str | None = None,
            observed_on: date | None = None) -> Evidence:
    return Evidence(kind=kind, title=title, detail=detail, metric=metric, observed_on=observed_on,
                    sensitive_amounts=kind in {"overview", "pnl", "position", "contribution", "lookback"})


def _definitions(zh: bool) -> list[Evidence]:
    rows = (
        ("rsi", "RSI · 6", "RSI6 比较最近六个交易日收盘价的平均上涨与下跌幅度，读数范围是 0 至 100。",
         "RSI · 6", "RSI6 compares average closing gains and losses over six trading sessions, on a scale from 0 to 100."),
        ("macd", "MACD", "MACD 使用收盘价的 12 日和 26 日指数移动平均。柱值等于 DIF 与 9 日信号线 DEA 差值的两倍。",
         "MACD", "MACD uses 12- and 26-session exponential moving averages of closes. The histogram is twice the difference between DIF and its nine-session signal line, DEA."),
        ("volume", "量比 · 5 日", "这里的量比是当日成交量除以最近五个交易日的平均成交量，均值包含当日。",
         "Volume ratio · 5 sessions", "Volume ratio divides today's volume by the five-session average, including today."),
        ("return", "收益口径", "持仓浮盈是当前仍持有部分的市值与成本之差。按当前持仓回看采用固定数量与汇率，不能代替真实账户净值。",
         "Return basis", "Unrealized profit is the value of open positions less their cost. A fixed-holdings, fixed-FX lookback does not reconstruct account net asset value."),
    )
    return [_source("definition", title if zh else en_title, detail if zh else en_detail, metric=metric)
            for metric, title, detail, en_title, en_detail in rows]


def _portfolio(summary: dict, zh: bool) -> list[Evidence]:
    data = build_overview(summary)
    time = data.computed_at.isoformat(timespec="minutes")
    amount = f"{data.total_assets:,.2f} {data.currency}"
    coverage = {"complete": "完整", "partial": "部分", "unavailable": "缺少行情"}[data.valuation] if zh else data.valuation
    result = [_source("overview", "账户总览" if zh else "Portfolio overview",
                      f"本次可估资产 {amount}，行情覆盖为{coverage}。{time}" if zh else
                      f"Valued assets: {amount}. Quote coverage: {coverage}. {time}")]
    missing = sum(h.market_value is None for h in data.holdings)
    result.extend([
        _source("basis", "账户估值来源" if zh else "Valuation sources",
                f"估值依据是本实例的账户现金、持仓数量、报价和汇率。计算时间 {time}。" if zh else
                f"The valuation uses the instance’s cash balances, position quantities, quotes and exchange rates as of {time}."),
        _source("coverage", "持仓估值覆盖" if zh else "Position coverage",
                f"共 {len(data.holdings)} 项持仓，其中 {missing} 项缺少可用市值。报价覆盖为{coverage}。" if zh else
                f"Of {len(data.holdings)} positions, {missing} lack a valuation. Quote coverage is {coverage}."),
    ])
    if data.today_pnl.value is not None:
        pnl = f"{data.today_pnl.value:+,.2f} {data.currency}"
        result.append(_source("pnl", "今日持仓盈亏" if zh else "Today's portfolio P&L",
                              f"按当前持仓计算的今日盈亏为 {pnl}。{time}" if zh else f"Today's P&L for current holdings is {pnl}. {time}"))
    else:
        result.append(_source("pnl", "今日持仓盈亏" if zh else "Today's portfolio P&L",
                              "部分持仓缺少可比较的行情，无法计算组合今日盈亏。" if zh else
                              "Some positions lack comparable prices, so a portfolio daily P&L is unavailable."))
    positions: dict[tuple[str, str], list] = {}
    for holding in data.holdings:
        positions.setdefault((holding.market, holding.symbol), []).append(holding)
    for holdings in list(positions.values())[:32]:
        first = holdings[0]
        quantity = sum(h.quantity for h in holdings)
        value = sum(h.market_value for h in holdings) if all(h.market_value is not None for h in holdings) else None
        figure = f"{value:,.2f} {first.currency}" if value is not None else ("暂无完整报价" if zh else "unavailable")
        name = f"{first.name} · {first.market} {first.symbol}"
        result.append(_source("position", name + (" · 持仓" if zh else " · Position"),
                              f"{name} 合计持有 {quantity:g}，持仓市值 {figure}。{time}" if zh else
                              f"The combined position in {name} is {quantity:g} units, valued at {figure}. {time}"))
    for item in data.contributions[:32]:
        value = item.daily_pnl.value
        figure = (f"{value:+,.2f} CNY" if value is not None else ("暂无可比报价" if zh else "unavailable"))
        name = f"{item.target.name} · {item.target.market} {item.target.symbol}"
        result.append(_source("contribution", name, (f"{name} 的当日持仓盈亏贡献为 {figure}。" if zh else
                                      f"The daily holding contribution for {name} is {figure}.") + f" {item.quote_date or time}", observed_on=item.quote_date))
    return result


def _symbol(db, context, aggregate_klines, zh: bool) -> list[Evidence]:
    data = load_symbol_facts(db, context.market, context.symbol, "1d", aggregate_klines)
    name = f"{data.target.name} · {context.market} {context.symbol}"
    missing = "缺少可核对的数据" if zh else "unavailable"
    quote = data.quote
    if quote:
        labels = {"fresh": "最新报价", "delayed": "延迟报价", "stale": "过期报价", "unknown": "时效未知"}
        freshness = labels[quote.freshness] if zh else quote.freshness
        observation = f"{quote.price:g} {data.currency} · {quote.date or missing} · {quote.source or missing} · {freshness}"
    else:
        observation = missing
    rows = [_source("quote", name, f"{name} 最新可见报价：{observation}。" if zh else f"Latest observed price for {name}: {observation}.", observed_on=quote.date if quote else None)]
    tech = data.technicals_daily
    if tech:
        for metric, label, value in (("rsi", "RSI6", tech.rsi6), ("macd", "MACD", tech.macd_hist),
                                     ("volume", "量比" if zh else "Volume ratio", tech.volume_ratio_5d)):
            reading = f"{value:.4f}".rstrip("0").rstrip(".") if value is not None else missing
            rows.append(_source("indicator", f"{name} · {label}", f"{label} = {reading} · {tech.as_of} · {data.bars_source or missing}",
                                metric=metric, observed_on=tech.as_of))
    close_label = "收盘价" if zh else "Closing price"
    for bar in data.bars:
        rows.append(_source("close", f"{name} · {close_label}",
                            f"{bar.date} · {name} · {close_label} {bar.close:g} {data.currency} · {data.bars_source or missing}",
                            observed_on=bar.date))
    observed = f"{quote.date}" if quote and quote.date else missing
    vendor = quote.source if quote and quote.source else missing
    unavailable = ([("报价" if zh else "quote")] if not quote else []) + [
        label for label, value in (("RSI6", tech.rsi6 if tech else None), ("MACD", tech.macd_hist if tech else None),
                                   ("量比" if zh else "volume ratio", tech.volume_ratio_5d if tech else None)) if value is None]
    rows.extend([
        _source("basis", name, f"报价来源 {vendor} · {observed}；日线来源 {data.bars_source or missing}，共 {len(data.bars)} 条。"
                if zh else f"Quote source: {vendor}, {observed}. Daily bars: {data.bars_source or missing} ({len(data.bars)} records)."),
        _source("coverage", name, (f"缺少的字段：{'、'.join(unavailable)}。" if unavailable else "当前报价和三项日线技术读数都可查。")
                if zh else (f"Unavailable fields: {', '.join(unavailable)}." if unavailable else "The quote and all three daily indicators are available.")),
    ])
    return rows


def _saved_task(db, device_id: str, task_id: UUID, zh: bool) -> list[Evidence]:
    row = db.get(MobileFactTask, str(task_id))
    if row is None or row.device_id != device_id or row.state != "complete" or not row.result:
        raise api_error(404, "mobile_assistant_not_found", "这份事实简报不可用。")
    snap = snapshot_adapter.validate_python(row.result)
    at = datetime.fromtimestamp(row.finished_at or row.created_at, UTC).isoformat(timespec="minutes")
    title = "已保存的事实简报" if zh else "Saved fact brief"
    if snap.phase == "premarket":
        records = [_source("disclosure", f"{item.target.name} · {item.target.symbol}", f"{item.date} · {item.kind} · {item.source}", observed_on=item.date)
                   for item in snap.disclosures.items[:32]]
    elif snap.phase == "intraday":
        records = [_source("quote", f"{item.target.name} · {item.target.symbol}",
                           f"{item.target.name} · {item.target.symbol} · {item.quote.price:g} {item.currency} · {item.quote.date or at} · {item.quote.source or '—'}",
                           observed_on=item.quote.date) for item in snap.observations[:32] if item.quote]
    else:
        records = [_source("contribution", f"{item.target.name} · {item.target.symbol}",
                           f"{item.target.name} · {item.target.symbol} · {item.daily_pnl.value:+,.2f} {snap.currency} · {item.quote_date or at}",
                           observed_on=item.quote_date) for item in snap.contributions[:32] if item.daily_pnl.value is not None]
    phase = {"premarket": "盘前公告", "intraday": "盘中行情", "postmarket": "盘后持仓贡献"}[snap.phase] if zh else snap.phase
    coverage = {"ready": "可用", "partial": "部分可用", "empty": "无记录", "unavailable": "缺少数据"}[snap.state] if zh else snap.state
    records.extend([
        _source("basis", title, f"来源为本设备保存的{phase}事实简报，保存时间 {at}。" if zh else f"Source: this device’s saved {phase} fact brief, recorded {at}."),
        _source("coverage", title, f"该简报的来源状态为{coverage}，可引用的记录有 {len(records)} 条。" if zh else
                f"Source coverage is {coverage}, with {len(records)} records available to cite."),
    ])
    return records


_LOOKBACK = re.compile(r"按(?:当前|现有)持仓回(?:看|溯)|固定持仓回(?:看|溯)|持仓回看|fixed.holdings lookback|holdings lookback", re.I)


def requests_lookback(question: str) -> bool:
    return bool(_LOOKBACK.search(question))


def _lookback(summary, zh):
    history = build_portfolio_lookback(summary)
    result = []
    sources = ", ".join(history.sources) or "—"
    for point in history.points:
        value = f"{point.assets:,.2f} {history.currency}" if point.assets is not None else ("缺少报价" if zh else "incomplete coverage")
        result.append(_source("lookback", "按当前持仓回看" if zh else "Fixed-holdings lookback",
                              f"{point.date} · {value} · 按当前持仓数量、现金与固定汇率回看，覆盖 {point.covered_positions}/{history.position_count} 项持仓 · {sources}"
                              if zh else f"{point.date} · {value} · current quantities and cash with fixed FX, coverage {point.covered_positions}/{history.position_count} · {sources}",
                              observed_on=point.date))
    return result


def gather_sources(db: Session, device_id: str, raw_context: dict | None, locale: str, portfolio, aggregate_klines, question: str = "") -> list[Evidence]:
    context = context_adapter.validate_python(raw_context)
    zh = locale == "zh-CN"
    records: list[Evidence] = []
    if isinstance(context, PortfolioContext):
        summary = portfolio(db)
        records = _lookback(summary, zh) if requests_lookback(question) else _portfolio(summary, zh)
    elif isinstance(context, SymbolContext):
        records = _symbol(db, context, aggregate_klines, zh)
    elif isinstance(context, TaskContext):
        records = _saved_task(db, device_id, context.task_id, zh)
    return records + _definitions(zh)
