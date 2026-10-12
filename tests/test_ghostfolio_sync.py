"""Ghostfolio 单向同步:map_market / compute_holdings 纯函数 + sync_now DB 往返。"""

import pytest
import requests

from src.modules.ghostfolio import service
from src.modules.ghostfolio.service import compute_holdings, fetch_export, map_market

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
        {"accountId": "a2", "type": "BUY", "quantity": "0.05", "unitPrice": "65000", "fee": "0",
         "date": "2026-01-07T10:00:00+11:00",
         "symbolProfile": {"symbol": "BTC", "dataSource": "COINGECKO", "name": "Bitcoin", "currency": "USD"}},
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
    holdings = compute_holdings(EXPORT)
    cba = holdings[("Stake", "CBA", "AU")]
    assert cba.quantity == 10 and cba.cost == 1005.0  # fee included, DIVIDEND ignored
    ivv = holdings[("Stake", "IVV", "AU")]
    assert ivv.quantity == 3.5 and abs(ivv.cost - 345.1) < 1e-9
    aapl = holdings[("CommSec", "AAPL", "US")]
    assert aapl.quantity == 1 and aapl.cost == 190.0  # avg-cost 减记卖出
    assert ("CommSec", "BTC", "US") not in holdings  # COINGECKO skipped
    # Ruling 1: Ghostfolio .HK 基码补零到 5 位(PanWatch 港股约定 00700)
    tencent = holdings[("IBKR", "00700", "HK")]
    assert tencent.quantity == 100


def _make_db(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from src.platform.persistence.models import Account, GhostfolioConfig, Position, Stock

    engine = create_engine(f"sqlite:///{tmp_path / 'gf.db'}")
    Account.__table__.create(engine)
    Stock.__table__.create(engine)
    Position.__table__.create(engine)
    GhostfolioConfig.__table__.create(engine)
    return sessionmaker(bind=engine)(), engine


def test_sync_now_disabled_does_not_fetch(tmp_path, monkeypatch):
    """enabled=False 时不得发起任何导出请求(定时与手动同一条路径)。"""
    db, engine = _make_db(tmp_path)
    try:
        db.add(service.GhostfolioConfig(base_url="http://gf.test", api_token="tok", enabled=False))
        db.commit()

        def _boom(base, token):
            raise AssertionError("fetch must not be called when disabled")

        monkeypatch.setattr(service, "fetch_export", _boom)
        res = service.sync_now(db)
        assert res.status == "disabled"
        assert service.get_or_create_config(db).last_status == "disabled"
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
    h = compute_holdings(FLAT_EXPORT)
    vgs = h[("Pearler (Taxable - DCA Strategy)", "VGS", "AU")]
    assert vgs.quantity == 85 and abs(vgs.cost - 85 * 11.63) < 1e-9
    assert vgs.name == "VGS"
    aapl = h[("Pearler (Taxable - DCA Strategy)", "AAPL", "US")]
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
