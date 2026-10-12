"""Ghostfolio 单向同步:map_market / compute_holdings 纯函数 + sync_now DB 往返。"""

import pytest
import requests

from src.modules.ghostfolio import service
from src.modules.ghostfolio.service import ExportValidationError, compute_holdings, fetch_export, map_market

EXPORT = {
    "accounts": [
        {"id": "a1", "name": "Stake", "currency": "AUD"},
        {"id": "a2", "name": "CommSec", "currency": "AUD"},
        {"id": "a3", "name": "IBKR", "currency": "HKD"},
    ],
    "activities": [
        {"accountId": "a1", "type": "BUY", "quantity": "10", "unitPrice": "100", "fee": "5",
         "date": "2026-01-02T10:00:00+11:00",
         "symbolProfile": {"symbol": "CBA.AX", "dataSource": "YAHOO",
                           "name": "Commonwealth Bank of Australia", "currency": "AUD"}},
        {"accountId": "a1", "type": "DIVIDEND", "quantity": "0", "unitPrice": "42", "fee": "0",
         "date": "2026-01-03T10:00:00+11:00",
         "symbolProfile": {"symbol": "CBA.AX", "dataSource": "YAHOO",
                           "name": "Commonwealth Bank of Australia", "currency": "AUD"}},
        {"accountId": "a1", "type": "BUY", "quantity": "3.5", "unitPrice": "98.6", "fee": "0",
         "date": "2026-01-04T10:00:00+11:00",
         "symbolProfile": {"symbol": "IVV.AX", "dataSource": "YAHOO",
                           "name": "iShares S&P 500 ETF", "currency": "AUD"}},
        {"accountId": "a2", "type": "BUY", "quantity": "2", "unitPrice": "190", "fee": "0",
         "date": "2026-01-05T10:00:00+11:00",
         "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO", "name": "Apple Inc.", "currency": "USD"}},
        {"accountId": "a2", "type": "SELL", "quantity": "1", "unitPrice": "210", "fee": "0",
         "date": "2026-01-06T10:00:00+11:00",
         "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO", "name": "Apple Inc.", "currency": "USD"}},
        {"accountId": "a3", "type": "BUY", "quantity": "100", "unitPrice": "38.5", "fee": "0",
         "date": "2026-01-08T10:00:00+11:00",
         "symbolProfile": {"symbol": "0700.HK", "dataSource": "YAHOO",
                           "name": "Tencent Holdings", "currency": "HKD"}},
    ],
}


def test_map_market():
    assert map_market("CBA.AX", "YAHOO") == "AU"
    assert map_market("0700.HK", "YAHOO") == "HK"
    assert map_market("AAPL", "YAHOO") == "US"
    assert map_market("BTC", "COINGECKO") is None


def test_compute_holdings():
    holdings, _ = compute_holdings(EXPORT)
    cba = holdings[("a1", "CBA", "AU")]
    assert cba.quantity == 10 and cba.cost == 1005.0  # fee included, DIVIDEND ignored
    ivv = holdings[("a1", "IVV", "AU")]
    assert ivv.quantity == 3.5 and abs(ivv.cost - 345.1) < 1e-9
    aapl = holdings[("a2", "AAPL", "US")]
    assert aapl.quantity == 1 and aapl.cost == 190.0  # avg-cost 减记卖出
    # Ruling 1: Ghostfolio .HK 基码补零到 5 位(PanWatch 港股约定 00700)
    tencent = holdings[("a3", "00700", "HK")]
    assert tencent.quantity == 100


def test_compute_holdings_sorts_by_date():
    """乱序导出按日期重排后再聚合:先买后卖的平均成本核算不因顺序漂移。"""
    shuffled = {"accounts": EXPORT["accounts"],
                "activities": list(reversed(EXPORT["activities"]))}
    holdings, skipped = compute_holdings(shuffled)
    aapl = holdings[("a2", "AAPL", "US")]
    assert aapl.quantity == 1 and aapl.cost == 190.0  # 若未排序,SELL 先于 BUY 会留下 2 股
    assert skipped == {"DIVIDEND": 1}


def test_compute_holdings_basic_iso_date_sorts_with_extended_dates():
    """基本格式日期按日排序,不因字符串格式改变先买后卖顺序。"""
    buy = {"accountId": "a2", "type": "BUY", "quantity": "2", "unitPrice": "190",
           "date": "20260105", "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO"}}
    sell = {"accountId": "a2", "type": "SELL", "quantity": "1", "unitPrice": "210",
            "date": "2026-01-06T09:00:00+11:00",
            "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO"}}
    export = {"accounts": EXPORT["accounts"], "activities": [sell, buy]}
    service.validate_export(export)
    holdings, _ = compute_holdings(export)
    assert holdings[("a2", "AAPL", "US")].quantity == 1
    assert holdings[("a2", "AAPL", "US")].cost == 190.0


def test_compute_holdings_same_day_keeps_export_order():
    """同日交易保持导出原序:先卖与先买应得到不同持仓。"""
    buy = {"accountId": "a2", "type": "BUY", "quantity": "2", "unitPrice": "190",
           "date": "2026-01-05T09:00:00+11:00",
           "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO", "currency": "USD"}}
    sell = {"accountId": "a2", "type": "SELL", "quantity": "1", "unitPrice": "210",
            "date": "2026-01-05T17:00:00+11:00",
            "symbolProfile": {"symbol": "AAPL", "dataSource": "YAHOO", "currency": "USD"}}
    forward, _ = compute_holdings({"accounts": EXPORT["accounts"], "activities": [buy, sell]})
    backward, _ = compute_holdings({"accounts": EXPORT["accounts"], "activities": [sell, buy]})
    assert forward[("a2", "AAPL", "US")].quantity == 1
    assert forward[("a2", "AAPL", "US")].cost == 190.0
    assert backward[("a2", "AAPL", "US")].quantity == 2
    assert backward[("a2", "AAPL", "US")].cost == 380.0


def test_compute_holdings_rejects_unmappable_trade():
    """不能映射的买卖活动必须中止,不得静默丢弃。"""
    for activity_type in ("BUY", "SELL"):
        export = {"accounts": EXPORT["accounts"], "activities": [
            EXPORT["activities"][0],
            {"accountId": "a2", "type": activity_type, "quantity": "1", "unitPrice": "100",
             "date": "2026-01-09T10:00:00+11:00",
             "symbolProfile": {"symbol": "BTC", "dataSource": "COINGECKO", "currency": "USD"}},
        ]}
        with pytest.raises(ExportValidationError, match="BTC"):
            compute_holdings(export)


def _mixed_currency_export():
    return {
        "accounts": [{"id": "m1", "name": "Mix"}],
        "activities": [
            {"accountId": "m1", "type": "BUY", "quantity": "1", "unitPrice": "100", "fee": "0",
             "date": "2026-01-02T10:00:00+11:00",
             "symbolProfile": {"symbol": "CBA.AX", "dataSource": "YAHOO",
                               "name": "CBA", "currency": "AUD"}},
            {"accountId": "m1", "type": "BUY", "quantity": "1", "unitPrice": "100", "fee": "0",
             "date": "2026-01-03T10:00:00+11:00",
             "symbolProfile": {"symbol": "CBA.AX", "dataSource": "YAHOO",
                               "name": "CBA", "currency": "USD"}},
        ],
    }


def test_compute_holdings_rejects_mixed_currency():
    """同一(账户,标的)出现多种货币成本 → 视为数据质量问题,整体中止。"""
    with pytest.raises(ExportValidationError):
        compute_holdings(_mixed_currency_export())


def test_compute_holdings_reports_skipped_types_and_currency():
    """非交易活动按类型计数;交易持仓保留成本计价币种。"""
    export = {"accounts": EXPORT["accounts"], "activities": EXPORT["activities"] + [
        {"accountId": "a1", "type": "TRANSFER", "date": "2026-01-09T10:00:00+11:00"},
        {"accountId": "a1", "type": "TRANSFER", "date": "2026-01-10T10:00:00+11:00"},
        {"accountId": "a1", "type": "SPLIT", "date": "2026-01-11T10:00:00+11:00"},
    ]}
    holdings, skipped = compute_holdings(export)
    assert holdings[("a1", "CBA", "AU")].currency == "AUD"
    assert holdings[("a2", "AAPL", "US")].currency == "USD"
    assert skipped == {"DIVIDEND": 1, "TRANSFER": 2, "SPLIT": 1}


def _make_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.platform.persistence.models import Account, GhostfolioConfig, GhostfolioAccountMap, Position, Stock

    engine = create_engine(f"sqlite:///{tmp_path / 'gf.db'}")
    Account.__table__.create(engine)
    Stock.__table__.create(engine)
    Position.__table__.create(engine)
    GhostfolioConfig.__table__.create(engine)
    GhostfolioAccountMap.__table__.create(engine)
    return sessionmaker(bind=engine)(), engine


def test_sync_mixed_currency_aborts_without_changing_positions(tmp_path, monkeypatch):
    """货币冲突记录中止状态,现有持仓不变。"""
    from src.platform.persistence.models import Account, GhostfolioAccountMap, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        cfg = service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True)
        acc = Account(name="GF: Mix")
        stock = Stock(symbol="CBA", name="CBA", market="AU")
        db.add_all([cfg, acc, stock])
        db.flush()
        db.add(GhostfolioAccountMap(gf_account_id="m1", account_id=acc.id))
        pos = Position(account_id=acc.id, stock_id=stock.id, quantity=7,
                       cost_price=88, invested_amount=616)
        db.add(pos)
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: _mixed_currency_export())

        with pytest.raises(ExportValidationError, match="mixed currencies"):
            service.sync_now(db)
        db.refresh(cfg)
        db.refresh(pos)
        assert cfg.last_status.startswith("aborted: mixed currencies")
        assert cfg.last_sync_at is not None
        assert (pos.quantity, pos.cost_price, pos.invested_amount) == (7, 88, 616)
        assert db.query(Position).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_sync_invalid_date_aborts_without_changing_positions(tmp_path, monkeypatch):
    """无效交易日期中止同步,保留现有持仓。"""
    from copy import deepcopy
    from src.platform.persistence.models import Account, GhostfolioAccountMap, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        cfg = service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True)
        acc = Account(name="GF: Stake")
        stock = Stock(symbol="CBA", name="CBA", market="AU")
        db.add_all([cfg, acc, stock])
        db.flush()
        db.add(GhostfolioAccountMap(gf_account_id="a1", account_id=acc.id))
        pos = Position(account_id=acc.id, stock_id=stock.id, quantity=7,
                       cost_price=88, invested_amount=616)
        db.add(pos)
        db.commit()
        bad_export = deepcopy(EXPORT)
        bad_export["activities"][0]["date"] = "not-a-date"
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: bad_export)

        with pytest.raises(ExportValidationError, match=r"activity\[0\].*CBA.AX"):
            service.sync_now(db)
        db.refresh(cfg)
        db.refresh(pos)
        assert cfg.last_status.startswith("aborted:")
        assert cfg.last_sync_at is not None
        assert (pos.quantity, pos.cost_price, pos.invested_amount) == (7, 88, 616)
        assert db.query(Position).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_sync_invalid_date_with_malformed_symbol_profile_aborts(tmp_path, monkeypatch):
    """标的资料格式错误时,无效日期仍须记录中止状态。"""
    from copy import deepcopy
    from src.platform.persistence.models import Account, GhostfolioAccountMap, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        cfg = service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True)
        acc = Account(name="GF: Stake")
        stock = Stock(symbol="CBA", name="CBA", market="AU")
        db.add_all([cfg, acc, stock])
        db.flush()
        db.add(GhostfolioAccountMap(gf_account_id="a1", account_id=acc.id))
        pos = Position(account_id=acc.id, stock_id=stock.id, quantity=7,
                       cost_price=88, invested_amount=616)
        db.add(pos)
        db.commit()
        bad_export = deepcopy(EXPORT)
        bad_export["activities"][0]["date"] = "not-a-date"
        bad_export["activities"][0]["symbolProfile"] = "bad-profile"
        bad_export["activities"][0]["symbol"] = "CBA.AX"
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: bad_export)

        with pytest.raises(ExportValidationError, match=r"activity\[0\].*CBA.AX"):
            service.sync_now(db)
        db.refresh(cfg)
        db.refresh(pos)
        assert cfg.last_status.startswith("aborted:")
        assert (pos.quantity, pos.cost_price, pos.invested_amount) == (7, 88, 616)
        assert db.query(Position).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_preview_mixed_currency_returns_invalid_export(tmp_path, monkeypatch):
    """预览遇货币冲突返回错误而非部分持仓。"""
    from src.modules.ghostfolio import api

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: _mixed_currency_export())
        result = api.preview(db=db)
        assert result["error"].startswith("invalid export: mixed currencies")
        assert "holdings" not in result
    finally:
        db.close()
        engine.dispose()


def test_same_named_upstream_accounts_stay_distinct(tmp_path, monkeypatch):
    """两个同名上游账户必须保持两个独立本地账户(不可变 ID 识别,而非名称)。"""
    from src.platform.persistence.models import Position

    export = {
        "accounts": [
            {"id": "x1", "name": "Stake", "currency": "AUD"},
            {"id": "x2", "name": "Stake", "currency": "AUD"},
        ],
        "activities": [
            {"accountId": "x1", "type": "BUY", "quantity": "10", "unitPrice": "100", "fee": "0",
             "date": "2026-01-02T10:00:00+11:00",
             "symbolProfile": {"symbol": "CBA.AX", "dataSource": "YAHOO",
                               "name": "Commonwealth Bank of Australia", "currency": "AUD"}},
            {"accountId": "x2", "type": "BUY", "quantity": "5", "unitPrice": "100", "fee": "0",
             "date": "2026-01-02T10:00:00+11:00",
             "symbolProfile": {"symbol": "VAS.AX", "dataSource": "YAHOO",
                               "name": "Vanguard Australian Shares", "currency": "AUD"}},
        ],
    }
    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda b, t: export)
        res = service.sync_now(db)
        assert res.accounts == 2
        from src.platform.persistence.models import Stock
        cba = db.query(Position).join(Stock).filter(Stock.symbol == "CBA").one()
        vas = db.query(Position).join(Stock).filter(Stock.symbol == "VAS").one()
        assert cba.account_id != vas.account_id
        assert db.query(service.GhostfolioAccountMap).count() == 2
    finally:
        db.close()
        engine.dispose()


def test_account_emptying_requires_confirm(tmp_path, monkeypatch):
    """valid 导出但某账户整体清空:默认保留并计数,confirm_empty=True 才真正删除。"""
    from src.platform.persistence.models import Account, Position

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda b, t: EXPORT)
        service.sync_now(db)
        stake = db.query(Account).filter_by(name="GF: Stake").one()
        assert db.query(Position).count() == 4

        stake_emptied = {"accounts": EXPORT["accounts"],
                         "activities": [a for a in EXPORT["activities"] if a["accountId"] != "a1"]}
        monkeypatch.setattr(service, "fetch_export", lambda b, t: stake_emptied)
        res = service.sync_now(db)
        assert res.pending_removal_count == 2 and res.removed == 0
        assert res.pending_removals == [
            {"account_id": stake.id, "account_name": stake.name, "symbol": "CBA", "market": "AU"},
            {"account_id": stake.id, "account_name": stake.name, "symbol": "IVV", "market": "AU"},
        ]
        assert db.query(Position).count() == 4  # Stake 的 CBA/IVV 仍保留
        assert res.status == service.get_or_create_config(db).last_status == "pending account-emptying removals"

        res2 = service.sync_now(db, confirm_empty=True)
        assert res2.removed == 2 and db.query(Position).count() == 2
    finally:
        db.close()
        engine.dispose()


def test_mixed_removals_and_renamed_mapped_account(tmp_path, monkeypatch):
    """重命名映射账户仍须受清空确认保护;其他账户的单标的删除照常执行。"""
    from src.platform.persistence.models import Account, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        mixed_export = {"accounts": EXPORT["accounts"], "activities": EXPORT["activities"] + [{
            "accountId": "a2", "type": "BUY", "quantity": "1", "unitPrice": "10", "fee": "0",
            "date": "2026-01-09T10:00:00+11:00",
            "symbolProfile": {"symbol": "MSFT", "dataSource": "YAHOO", "name": "Microsoft",
                              "currency": "USD"}}]}
        monkeypatch.setattr(service, "fetch_export", lambda b, t: mixed_export)
        service.sync_now(db)
        stake = db.query(Account).filter_by(name="GF: Stake").one()
        stake.name = "Renamed Stake"
        db.commit()
        reduced = {"accounts": EXPORT["accounts"], "activities": [
            a for a in mixed_export["activities"] if a["accountId"] != "a1"
            and (a.get("symbolProfile") or {}).get("symbol") != "AAPL"]}
        monkeypatch.setattr(service, "fetch_export", lambda b, t: reduced)
        res = service.sync_now(db)
        assert res.removed == 1 and res.pending_removal_count == 2
        assert res.pending_removals == [
            {"account_id": stake.id, "account_name": "Renamed Stake", "symbol": "CBA", "market": "AU"},
            {"account_id": stake.id, "account_name": "Renamed Stake", "symbol": "IVV", "market": "AU"},
        ]
        assert db.query(Position).filter_by(account_id=stake.id).count() == 2
        assert db.query(Position).join(Stock).filter(Stock.symbol == "AAPL").count() == 0
        res2 = service.sync_now(db, confirm_empty=True)
        assert res2.removed == 2 and db.query(Position).filter_by(account_id=stake.id).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_sync_api_confirm_empty_query(tmp_path, monkeypatch):
    """HTTP 查询参数控制整账户清空,响应列出待确认的账户与标的。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.modules.ghostfolio import api
    from src.platform.persistence.database import get_db
    from src.platform.persistence.models import Account, Position

    db, engine = _make_db(tmp_path)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/ghostfolio")
    app.dependency_overrides[get_db] = lambda: db
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda b, t: EXPORT)
        service.sync_now(db)
        stake = db.query(Account).filter_by(name="GF: Stake").one()
        reduced = {"accounts": EXPORT["accounts"],
                   "activities": [a for a in EXPORT["activities"] if a["accountId"] != "a1"]}
        monkeypatch.setattr(service, "fetch_export", lambda b, t: reduced)
        with TestClient(app) as client:
            response = client.post("/api/ghostfolio/sync")
            assert response.status_code == 200
            data = response.json()
            assert data["pending_removal_count"] == 2 and data["removed"] == 0
            assert data["pending_removals"] == [
                {"account_id": stake.id, "account_name": stake.name, "symbol": "CBA", "market": "AU"},
                {"account_id": stake.id, "account_name": stake.name, "symbol": "IVV", "market": "AU"},
            ]
            assert db.query(Position).count() == 4
            confirmed = client.post("/api/ghostfolio/sync?confirm_empty=true")
            assert confirmed.status_code == 200
            assert confirmed.json()["removed"] == 2 and db.query(Position).count() == 2
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_sync_now_disabled_does_not_fetch(tmp_path, monkeypatch):
    """enabled=False 时不得发起任何导出请求(定时与手动同一条路径)。"""
    from src.platform.persistence.models import Account, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=False))
        account = Account(name="GF: Existing")
        stock = Stock(symbol="IVV", name="iShares", market="AU")
        db.add_all([account, stock])
        db.flush()
        db.add(Position(account_id=account.id, stock_id=stock.id, cost_price=65.79,
                        quantity=163, invested_amount=10723))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)

        def _boom(base, token):
            raise AssertionError("fetch must not be called when disabled")

        monkeypatch.setattr(service, "fetch_export", _boom)
        res = service.sync_now(db)
        assert res.status == "disabled"
        assert service.get_or_create_config(db).last_status == "disabled"
        assert db.query(Account).one().name == "GF: Existing"
        pos = db.query(Position).one()
        assert (pos.quantity, pos.cost_price, pos.invested_amount) == (163, 65.79, 10723)
    finally:
        db.close()
        engine.dispose()


def test_sync_now_global_flag_disabled_with_enabled_config(tmp_path, monkeypatch):
    """部署级开关关闭时,已启用的配置也不得同步。"""
    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "0")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", False)
        monkeypatch.setattr(service, "fetch_export",
                            lambda base, token: (_ for _ in ()).throw(AssertionError("must not fetch")))
        res = service.sync_now(db)
        assert res.status == "disabled"
        assert service.get_or_create_config(db).last_status == "disabled"
    finally:
        db.close()
        engine.dispose()


def test_sync_now_both_flags_enabled(tmp_path, monkeypatch):
    """部署和配置同时启用时才允许同步。"""
    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        fetched = []

        def _fetch(base, token):
            fetched.append(base)
            return EXPORT

        monkeypatch.setattr(service, "fetch_export", _fetch)
        res = service.sync_now(db)
        assert res.status == "ok"
        assert fetched == ["http://gf.test"]
        assert res.created == 4
    finally:
        db.close()
        engine.dispose()


def test_preview_disabled_does_not_fetch(tmp_path, monkeypatch):
    """预览入口在任一开关关闭时不得请求导出。"""
    from src.modules.ghostfolio import api

    db, engine = _make_db(tmp_path)
    try:
        cfg = service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True)
        db.add(cfg)
        db.commit()
        monkeypatch.setattr(service, "fetch_export",
                            lambda base, token: (_ for _ in ()).throw(AssertionError("must not fetch")))
        for global_enabled, config_enabled in ((False, True), (True, False)):
            monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1" if global_enabled else "0")
            monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", global_enabled)
            cfg.enabled = config_enabled
            db.commit()
            assert api.preview(db=db) == {"error": "disabled"}
    finally:
        db.close()
        engine.dispose()


def test_run_scheduled_sync_honors_disabled(tmp_path, monkeypatch):
    """定时入口每次运行都重新检查用户开关。"""
    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=False))
        db.commit()
        monkeypatch.setattr("src.platform.persistence.database.SessionLocal", lambda: db)
        monkeypatch.setattr(service, "fetch_export",
                            lambda base, token: (_ for _ in ()).throw(AssertionError("must not fetch")))
        service.run_scheduled_sync()  # 不抛错、不请求
    finally:
        engine.dispose()


def test_register_ghostfolio_sync_skips_when_disabled(tmp_path, monkeypatch):
    """enabled=False 时不向调度器注册任何任务。"""
    import server as server_mod

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=False))
        db.commit()
        monkeypatch.setattr("src.platform.persistence.database.SessionLocal", lambda: db)
        added: list = []

        class _Sched:
            def add_job(self, *a, **k):
                added.append(a)

        server_mod.register_ghostfolio_sync(_Sched())
        assert added == []
    finally:
        engine.dispose()


def test_sync_now_upserts_and_removes(tmp_path, monkeypatch):
    from src.modules.ghostfolio import service
    from src.platform.persistence.models import Account, Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: EXPORT)

        res = service.sync_now(db)
        assert res.accounts == 3 and res.created == 4 and res.removed == 0
        assert res.skipped_types == {"DIVIDEND": 1}

        names = {a.name for a in db.query(Account).all()}
        assert names == {"GF: Stake", "GF: CommSec", "GF: IBKR"}

        cba = db.query(Position).join(Stock).filter(Stock.symbol == "CBA").one()
        assert cba.quantity == 10 and abs(cba.cost_price - 100.5) < 1e-9
        ivv = db.query(Position).join(Stock).filter(Stock.symbol == "IVV").one()
        assert ivv.quantity == 3.5
        aapl = db.query(Position).join(Stock).filter(Stock.symbol == "AAPL").one()
        assert aapl.quantity == 1
        tencent = db.query(Position).join(Stock).filter(Stock.symbol == "00700").one()
        assert tencent.quantity == 100
        assert db.query(Stock).filter_by(symbol="IVV", market="AU").one().name == "iShares S&P 500 ETF"

        # 幂等重跑:全部 update,无新增
        res2 = service.sync_now(db)
        assert res2.created == 0 and res2.updated == 4 and res2.removed == 0

        # 移除腿:导出里不再有 IVV → 其持仓删除,CBA 不受影响
        export_no_ivv = {
            "accounts": EXPORT["accounts"],
            "activities": [a for a in EXPORT["activities"]
                           if (a.get("symbolProfile") or {}).get("symbol") != "IVV.AX"],
        }
        monkeypatch.setattr(service, "fetch_export", lambda base, token: export_no_ivv)
        res3 = service.sync_now(db)
        assert res3.removed == 1
        assert db.query(Position).join(Stock).filter(Stock.symbol == "IVV").count() == 0
        assert db.query(Position).join(Stock).filter(Stock.symbol == "CBA").one().quantity == 10

        cfg = service.get_or_create_config(db)
        assert cfg.last_status == "ok"
    finally:
        db.close()
        engine.dispose()


FLAT_EXPORT = {
    "accounts": [{"id": "p1", "name": "Pearler (Taxable - DCA Strategy)"}],
    "activities": [
        {"accountId": "p1", "type": "BUY", "quantity": 85, "unitPrice": 11.63, "fee": 0,
         "date": "2026-01-02T10:00:00+11:00",
         "symbol": "VGS.AX", "dataSource": "YAHOO", "currency": "AUD"},
        {"accountId": "p1", "type": "BUY", "quantity": 2, "unitPrice": 336.5, "fee": 1.95,
         "date": "2026-01-03T10:00:00+11:00",
         "symbol": "AAPL", "dataSource": "YAHOO", "currency": "USD"},
        {"accountId": "p1", "type": "SELL", "quantity": 1, "unitPrice": 340, "fee": 0,
         "date": "2026-01-04T10:00:00+11:00",
         "symbol": "AAPL", "dataSource": "YAHOO", "currency": "USD"},
        {"accountId": "p1", "type": "DIVIDEND", "quantity": 0, "unitPrice": 42, "fee": 0,
         "date": "2026-01-05T10:00:00+11:00",
         "symbol": "VGS.AX", "dataSource": "YAHOO", "currency": "AUD"},
    ],
}


def test_validate_export_rejects_incomplete_shapes():
    """不完整/非法导出必须被识别:空对象、空账户、缺 activities、非法数量、未知账户。"""
    from src.modules.ghostfolio.service import ExportValidationError, validate_export

    with pytest.raises(ExportValidationError):
        validate_export({})  # 完全为空(HTTP 200 但无内容)
    with pytest.raises(ExportValidationError):
        validate_export({"accounts": [], "activities": []})  # 空账户列表视为部分导出
    with pytest.raises(ExportValidationError):
        validate_export({"accounts": [{"id": "a1"}]})  # 缺 activities 键
    with pytest.raises(ExportValidationError):
        validate_export({"accounts": [{"id": "a1"}], "activities": [
            {"accountId": "a1", "type": "BUY", "quantity": "x",
             "unitPrice": "1", "date": "2026-01-02"}]})  # 数量非数值
    with pytest.raises(ExportValidationError):
        validate_export({"accounts": [{"id": "a1"}], "activities": [
            {"accountId": "a2", "type": "BUY", "quantity": "1",
             "unitPrice": "1", "date": "2026-01-02"}]})  # 活动引用未知账户
    with pytest.raises(ExportValidationError):
        validate_export({"accounts": [{"id": "a1"}], "activities": [
            {"accountId": "a1", "type": "BUY", "quantity": "1",
             "unitPrice": "1"}]})  # BUY 缺日期
    validate_export(EXPORT)  # 完整导出通过
    validate_export(FLAT_EXPORT)  # 扁平形态同样通过


@pytest.mark.parametrize("quantity", ["NaN", "Infinity", "-5"])
def test_sync_rejects_invalid_quantity_without_removal(tmp_path, monkeypatch, quantity):
    """异常数量不得使持仓过滤后被误删。"""
    from src.platform.persistence.models import Position, Stock

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: EXPORT)
        service.sync_now(db)
        cba = db.query(Position).join(Stock).filter(Stock.symbol == "CBA").one()
        original = (cba.quantity, cba.cost_price, cba.invested_amount)
        malformed = {"accounts": EXPORT["accounts"], "activities": [
            {**act, "quantity": quantity} if act is EXPORT["activities"][0] else act
            for act in EXPORT["activities"]]}
        monkeypatch.setattr(service, "fetch_export", lambda base, token: malformed)
        with pytest.raises(ExportValidationError, match=r"activity\[0\].*quantity"):
            service.sync_now(db)
        db.refresh(cba)
        assert (cba.quantity, cba.cost_price, cba.invested_amount) == original
        assert db.query(Position).count() == 4
        assert service.get_or_create_config(db).last_status.startswith("aborted:")
    finally:
        db.close()
        engine.dispose()


def test_sync_now_aborts_on_malformed_export_preserves_positions(tmp_path, monkeypatch):
    """畸形但 HTTP 成功的导出 {} 不得删除任何既有持仓(评审复现场景)。"""
    from src.platform.persistence.models import Position

    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=True))
        db.commit()
        monkeypatch.setenv("GHOSTFOLIO_ENABLED", "1")
        monkeypatch.setattr(service, "GHOSTFOLIO_ENABLED", True)
        monkeypatch.setattr(service, "fetch_export", lambda base, token: EXPORT)
        service.sync_now(db)
        before = db.query(Position).count()
        assert before == 4

        monkeypatch.setattr(service, "fetch_export", lambda base, token: {})
        with pytest.raises(service.ExportValidationError):
            service.sync_now(db)
        assert db.query(Position).count() == before  # 持仓原样保留
        assert service.get_or_create_config(db).last_status.startswith("aborted:")
    finally:
        db.close()
        engine.dispose()


def test_compute_holdings_accepts_flat_export_shape():
    h, _ = compute_holdings(FLAT_EXPORT)
    vgs = h[("p1", "VGS", "AU")]
    assert vgs.quantity == 85 and abs(vgs.cost - 85 * 11.63) < 1e-9
    assert vgs.name == "VGS"
    aapl = h[("p1", "AAPL", "US")]
    assert aapl.quantity == 1
    assert abs(aapl.cost - 337.475) < 1e-9


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_fetch_export_direct_bearer_ok(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append((url, headers["Authorization"]))
        return _Resp(200, {"accounts": [], "activities": []})

    monkeypatch.setattr(service.requests, "get", fake_get)
    assert fetch_export("http://gf.test", "PAT") == {"accounts": [], "activities": []}
    assert seen == [("http://gf.test/api/v1/export", "Bearer PAT")]


def test_fetch_export_exchanges_pat_on_401(monkeypatch):
    seen = []

    def fake_get(url, headers=None, timeout=None):
        seen.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer PAT":
            return _Resp(401, {"statusCode": 401, "message": "Unauthorized"})
        return _Resp(200, EXPORT)

    def fake_post(url, json=None, timeout=None):
        seen.append(("POST", url))
        return _Resp(200, {"authToken": "JWT42"})

    monkeypatch.setattr(service.requests, "get", fake_get)
    monkeypatch.setattr(service.requests, "post", fake_post)

    assert fetch_export("http://gf.test/", "PAT") == EXPORT
    assert seen == [
        "Bearer PAT",
        ("POST", "http://gf.test/api/v1/auth/anonymous"),
        "Bearer JWT42",
    ]


def test_fetch_export_exchange_rejected_raises(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(401, {"statusCode": 401})

    def fake_post(url, json=None, timeout=None):
        return _Resp(403, {"statusCode": 403, "message": "Forbidden"})

    monkeypatch.setattr(service.requests, "get", fake_get)
    monkeypatch.setattr(service.requests, "post", fake_post)

    with pytest.raises(requests.HTTPError):
        fetch_export("http://gf.test", "PAT")


def test_fetch_export_exchange_missing_token_raises(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _Resp(401, {"statusCode": 401})

    def fake_post(url, json=None, timeout=None):
        return _Resp(200, {"statusCode": 200})

    monkeypatch.setattr(service.requests, "get", fake_get)
    monkeypatch.setattr(service.requests, "post", fake_post)

    with pytest.raises(ValueError):
        fetch_export("http://gf.test", "PAT")
