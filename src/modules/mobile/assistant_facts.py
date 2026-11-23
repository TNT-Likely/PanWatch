"""Project only facts already available through the device API into a chat turn."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.orm import Session

from src.modules.mobile.fact_snapshots import FactSnapshot, FactTarget
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


def _source(title: str, detail: str) -> Source:
    return Source(title=title, detail=detail)


def _definitions(zh: bool) -> list[Source]:
    if zh:
        return [
            _source("RSI · 6", "RSI6 比较最近六个交易日收盘价的平均上涨与下跌幅度，读数范围是 0 至 100。"),
            _source("MACD", "MACD 使用收盘价的 12 日和 26 日指数移动平均。柱值等于 DIF 与 9 日信号线 DEA 差值的两倍。"),
            _source("量比 · 5 日", "这里的量比是当日成交量除以最近五个交易日的平均成交量，均值包含当日。"),
            _source("收益口径", "持仓浮盈是当前仍持有部分的市值与成本之差。按当前持仓回看采用固定数量与汇率，不能代替真实账户净值。"),
        ]
    return [
        _source("RSI · 6", "RSI6 compares average closing gains and losses over six trading sessions, on a scale from 0 to 100."),
        _source("MACD", "MACD uses 12- and 26-session exponential moving averages of closes. The histogram is twice the difference between DIF and its nine-session signal line, DEA."),
        _source("Volume ratio · 5 sessions", "Volume ratio divides today's volume by the five-session average, including today."),
        _source("Return basis", "Unrealized profit is the value of open positions less their cost. A fixed-holdings, fixed-FX lookback does not reconstruct account net asset value."),
    ]


def _portfolio(summary: dict, zh: bool) -> list[Source]:
    data = build_overview(summary)
    time = data.computed_at.isoformat(timespec="minutes")
    amount = f"{data.total_assets:,.2f} {data.currency}"
    coverage = {"complete": "完整", "partial": "部分", "unavailable": "缺少行情"}[data.valuation] if zh else data.valuation
    result = [_source("账户总览" if zh else "Portfolio overview",
                      f"本次可估资产 {amount}，行情覆盖为{coverage}。{time}" if zh else
                      f"Valued assets: {amount}. Quote coverage: {coverage}. {time}")]
    if data.today_pnl.value is not None:
        pnl = f"{data.today_pnl.value:+,.2f} {data.currency}"
        result.append(_source("今日持仓盈亏" if zh else "Today's portfolio P&L",
                              f"按当前持仓计算的今日盈亏为 {pnl}。{time}" if zh else f"Today's P&L for current holdings is {pnl}. {time}"))
    else:
        result.append(_source("今日持仓盈亏" if zh else "Today's portfolio P&L",
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
        result.append(_source(name + (" · 持仓" if zh else " · Position"),
                              f"{name} 合计持有 {quantity:g}，持仓市值 {figure}。{time}" if zh else
                              f"The combined position in {name} is {quantity:g} units, valued at {figure}. {time}"))
    for item in data.contributions[:32]:
        value = item.daily_pnl.value
        figure = (f"{value:+,.2f} CNY" if value is not None else ("暂无可比报价" if zh else "unavailable"))
        name = f"{item.target.name} · {item.target.market} {item.target.symbol}"
        result.append(_source(name, (f"{name} 的当日持仓盈亏贡献为 {figure}。" if zh else
                                      f"The daily holding contribution for {name} is {figure}.") + f" {item.quote_date or time}"))
    return result


def _symbol(db, context, aggregate_klines, zh: bool) -> list[Source]:
    data = load_symbol_facts(db, context.market, context.symbol, "1d", aggregate_klines)
    name = f"{data.target.name} · {context.market} {context.symbol}"
    missing = "缺少可核对的数据" if zh else "unavailable"
    quote = data.quote
    if quote:
        observation = f"{quote.price:g} {data.currency} · {quote.date or missing} · {quote.source or missing} · {quote.freshness}"
    else:
        observation = missing
    rows = [_source(name, f"{name} 最新可见报价：{observation}。" if zh else f"Latest observed price for {name}: {observation}.")]
    tech = data.technicals_daily
    if tech:
        for label, value in (("RSI6", tech.rsi6), ("MACD", tech.macd_hist), ("量比" if zh else "Volume ratio", tech.volume_ratio_5d)):
            reading = f"{value:.4f}".rstrip("0").rstrip(".") if value is not None else missing
            rows.append(_source(f"{name} · {label}", f"{label} = {reading} · {tech.as_of} · {data.bars_source or missing}"))
    return rows


def _saved_task(db, device_id: str, task_id: UUID, zh: bool) -> list[Source]:
    row = db.get(MobileFactTask, str(task_id))
    if row is None or row.device_id != device_id or row.state != "complete" or not row.result:
        raise api_error(404, "mobile_assistant_not_found", "这份事实简报不可用。")
    snap = snapshot_adapter.validate_python(row.result)
    at = datetime.fromtimestamp(row.finished_at or row.created_at, UTC).isoformat(timespec="minutes")
    title = "已保存的事实简报" if zh else "Saved fact brief"
    if snap.phase == "premarket":
        return [_source(f"{item.target.name} · {item.target.symbol}", f"{item.date} · {item.kind} · {item.source}")
                for item in snap.disclosures.items[:32]] or [_source(title, f"{at} · {snap.state} · 0")]
    if snap.phase == "intraday":
        return [_source(f"{item.target.name} · {item.target.symbol}", f"{item.quote.price:g} {item.currency} · {item.quote.date or at} · {item.quote.source or '—'}")
                for item in snap.observations[:32] if item.quote] or [_source(title, f"{at} · {snap.state}")]
    return [_source(f"{item.target.name} · {item.target.symbol}", f"{item.daily_pnl.value:+,.2f} {snap.currency} · {item.quote_date or at}")
            for item in snap.contributions[:32] if item.daily_pnl.value is not None] or [_source(title, f"{at} · {snap.state}")]


def gather_sources(db: Session, device_id: str, raw_context: dict | None, locale: str, portfolio, aggregate_klines) -> list[Source]:
    context = context_adapter.validate_python(raw_context)
    zh = locale == "zh-CN"
    records: list[Source] = []
    if isinstance(context, PortfolioContext):
        records = _portfolio(portfolio(db), zh)
    elif isinstance(context, SymbolContext):
        records = _symbol(db, context, aggregate_klines, zh)
    elif isinstance(context, TaskContext):
        records = _saved_task(db, device_id, context.task_id, zh)
    return records + _definitions(zh)
