"""AU 估值回归:10 股 × 100 AUD 经 AUD/CNY 汇率折算,绝不再 1:1 当 CNY。"""
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.portfolio.api import accounts as acct
from src.platform.persistence import models as M
from src.platform.persistence.database import Base


def _session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(session, market="AU", symbol="VAS", quantity=10, cost_price=80.0):
    acc = M.Account(name="GF", available_funds=1000.0, enabled=True)
    session.add(acc)
    session.flush()
    stock = M.Stock(symbol=symbol, name=symbol, market=market, asset_type="stock")
    session.add(stock)
    session.flush()
    session.add(
        M.Position(
            account_id=acc.id,
            stock_id=stock.id,
            quantity=quantity,
            cost_price=cost_price,
            invested_amount=quantity * cost_price,
        )
    )
    session.commit()
    return stock


def _quote(stock, price):
    return {
        f"{stock.market}:{stock.symbol}": {
            "symbol": stock.symbol,
            "market": stock.market,
            "name": stock.name,
            "current_price": price,
            "change_pct": 1.0,
            "prev_close": price / 1.01,
        }
    }


def test_au_position_valued_via_aud_rate(monkeypatch):
    monkeypatch.setattr(acct, "get_aud_cny_rate", lambda: 4.70)
    s = _session()
    stock = _seed(s)
    quotes = _quote(stock, 100.0)
    with patch.object(acct, "_fetch_quotes_for_stocks", return_value=quotes):
        rows = acct._gather_holdings(s)
    row = next(r for r in rows if r["symbol"] == "VAS")
    assert row["currency"] == "AUD"
    assert row["valuation_status"] == "complete"
    assert row["fx"] == 4.70
    assert abs(row["market_value"] - 100.0 * 10 * 4.70) < 1e-6  # 4700 CNY,不是 1000


def test_missing_fx_marks_incomplete(monkeypatch):
    monkeypatch.setattr(
        acct, "get_aud_cny_rate", lambda: (_ for _ in ()).throw(RuntimeError("fx down"))
    )
    s = _session()
    stock = _seed(s)
    quotes = _quote(stock, 100.0)
    with patch.object(acct, "_fetch_quotes_for_stocks", return_value=quotes):
        rows = acct._gather_holdings(s)
    row = rows[0]
    assert row["valuation_status"] == "incomplete"
    assert row["market_value"] is None  # 不计入任何合计,绝不用成本/1:1 冒充实值
    assert row["unrealized_pnl"] is None


def test_cn_hk_us_valuation_unchanged(monkeypatch):
    monkeypatch.setattr(acct, "get_hkd_cny_rate", lambda: 0.92)
    monkeypatch.setattr(acct, "get_usd_cny_rate", lambda: 7.25)
    s = _session()
    hk = _seed(s, market="HK", symbol="0700", quantity=200, cost_price=250.0)
    us = _seed(s, market="US", symbol="AAPL", quantity=5, cost_price=180.0)
    cn = _seed(s, market="CN", symbol="600519", quantity=1, cost_price=1400.0)
    quotes = {**_quote(hk, 300.0), **_quote(us, 200.0), **_quote(cn, 1500.0)}
    with patch.object(acct, "_fetch_quotes_for_stocks", return_value=quotes):
        rows = acct._gather_holdings(s)
    by = {r["symbol"]: r for r in rows}
    assert by["0700"]["currency"] == "HKD"
    assert by["0700"]["fx"] == 0.92
    assert abs(by["0700"]["market_value"] - 300.0 * 200 * 0.92) < 1e-6
    assert abs(by["AAPL"]["market_value"] - 200.0 * 5 * 7.25) < 1e-6
    assert by["600519"]["currency"] == "CNY"
    assert abs(by["600519"]["market_value"] - 1500.0 * 1 * 1.0) < 1e-6


def test_cross_listed_namesakes_fetch_distinct_quotes(monkeypatch):
    s = _session()
    au = _seed(s, market="AU", symbol="CBA", quantity=10, cost_price=80.0)
    us = _seed(s, market="US", symbol="CBA", quantity=10, cost_price=30.0)

    def fake_md(symbols, market):
        if market == "AU":
            return [{"symbol": "CBA", "market": "AU", "name": "CBA", "current_price": 100.0}]
        return [{"symbol": "CBA", "market": "US", "name": "CBA", "current_price": 30.0}]

    monkeypatch.setattr(acct, "md_quote_rows", fake_md)
    quotes = acct._fetch_quotes_for_stocks([au, us])
    assert quotes["AU:CBA"]["current_price"] == 100.0
    assert quotes["US:CBA"]["current_price"] == 30.0


def test_summary_reports_currency_and_fx_metadata(monkeypatch):
    monkeypatch.setattr(acct, "get_aud_cny_rate", lambda: 4.70)
    s = _session()
    stock = _seed(s)
    quotes = _quote(stock, 100.0)
    with patch.object(acct, "_fetch_quotes_for_stocks", return_value=quotes):
        out = acct.get_portfolio_summary(db=s)
    acc = out["accounts"][0]
    assert acc["valuation_status"] == "complete"
    assert acc["unvalued"] == []
    assert out["total"]["valuation_complete"] is True
    assert out["exchange_rates"]["AUD_CNY"] == 4.70
    assert "AUD_CNY" in out["fx_as_of"]
    pos = acc["positions"][0]
    assert pos["currency"] == "AUD"
    assert abs(pos["market_value_cny"] - 4700.0) < 1e-2


def test_summary_marks_incomplete_when_fx_down(monkeypatch):
    monkeypatch.setattr(
        acct, "get_aud_cny_rate", lambda: (_ for _ in ()).throw(RuntimeError("fx down"))
    )
    s = _session()
    stock = _seed(s)
    quotes = _quote(stock, 100.0)
    with patch.object(acct, "_fetch_quotes_for_stocks", return_value=quotes):
        out = acct.get_portfolio_summary(db=s)
    acc = out["accounts"][0]
    assert acc["valuation_status"] == "incomplete"
    assert acc["unvalued"] == ["VAS"]
    assert out["total"]["valuation_complete"] is False
    pos = acc["positions"][0]
    assert pos["market_value"] == 1000.0  # 原生 AUD 市值仍在
    assert pos["market_value_cny"] is None  # CNY 估值缺失,不用 1:1/成本冒充
    assert pos["exchange_rate"] is None
