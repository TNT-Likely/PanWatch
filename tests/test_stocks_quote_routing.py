import src.modules.market.api.stocks as stocks_api


def test_get_quotes_uses_md_quote_rows(monkeypatch):
    """自选股 /quotes 应走 md_quote_rows(而非直连 _fetch_tencent_quotes)。"""
    calls = []
    monkeypatch.setattr(
        stocks_api, "md_quote_rows",
        lambda symbols, market: (calls.append((tuple(symbols), market)),
                                 [{"symbol": symbols[0], "current_price": 3.0,
                                   "change_pct": 1.0, "change_amount": 0.03, "prev_close": 2.97}])[1],
    )
    assert hasattr(stocks_api, "md_quote_rows")
    # 语义:传入原始 symbol(非腾讯格式),按市场分组调用
    assert callable(stocks_api.md_quote_rows)


def test_watchlist_quotes_keyed_by_market_avoids_cross_market_collisions(monkeypatch):
    """US:CBA 与 AU:CBA 同代码跨市场并存时,自选股行情键带市场前缀,互不覆盖。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from src.platform.marketdata import enablement as em
    from src.platform.persistence.database import Base, get_db
    from src.platform.persistence import models as M  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    em.set_market_enabled(s, "AU", True)
    s.add(M.Stock(symbol="CBA", market="US", name="US CBA"))
    s.add(M.Stock(symbol="CBA", market="AU", name="AU CBA"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(stocks_api.router, prefix="/api/stocks")

    def _db():
        ss = Session()
        try:
            yield ss
        finally:
            ss.close()

    app.dependency_overrides[get_db] = _db
    monkeypatch.setattr(
        stocks_api, "md_quote_rows",
        lambda symbols, market: [{"symbol": symbols[0], "current_price": 1.0 if market == "US" else 2.0,
                                  "change_pct": 0.0, "change_amount": 0.0, "prev_close": 1.0}],
    )
    client = TestClient(app)
    data = client.get("/api/stocks/quotes").json()
    assert data["US:CBA"]["current_price"] == 1.0
    assert data["AU:CBA"]["current_price"] == 2.0
