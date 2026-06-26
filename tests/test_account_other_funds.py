"""账户现金、其他资产分类与币种测试"""

from src.web.api import accounts as accounts_api
from src.web.models import Account, Position, Stock


def test_account_other_funds_defaults_to_zero(db):
    """新建账户时 other_funds 默认为 0"""
    account = Account(name="测试账户", available_funds=10000)
    db.add(account)
    db.commit()
    db.refresh(account)
    assert float(account.other_funds or 0) == 0
    assert (account.other_fund_items or []) == []
    assert str(account.base_currency or "CNY").upper() == "CNY"
    assert float(account.initial_funds or 0) == 0


def test_account_create_with_other_fund_items(db):
    """创建账户时可设置带标签的其他资产，初始资金自动等于现金+其他"""
    created = accounts_api.create_account(
        accounts_api.AccountCreate(
            name="理财账户",
            available_funds=50000,
            other_fund_items=[
                accounts_api.OtherFundItem(label="理财", amount=80000),
                accounts_api.OtherFundItem(label="存款", amount=40000),
            ],
            base_currency="CNY",
        ),
        db,
    )
    assert created.available_funds == 50000
    assert created.other_funds == 120000
    assert len(created.other_fund_items) == 2
    assert created.other_fund_items[0].label == "理财"
    assert created.initial_funds == 170000


def test_account_create_defaults_initial_funds_to_cash_plus_other(db):
    """创建账户时初始资金自动等于现金+其他"""
    created = accounts_api.create_account(
        accounts_api.AccountCreate(
            name="默认初始",
            available_funds=30000,
            other_fund_items=[accounts_api.OtherFundItem(label="理财", amount=20000)],
        ),
        db,
    )
    assert created.initial_funds == 50000
    assert created.other_funds == 20000


def test_account_update_other_fund_items(db):
    """更新账户时可修改其他资产分类"""
    created = accounts_api.create_account(
        accounts_api.AccountCreate(name="更新测试", available_funds=1000),
        db,
    )
    updated = accounts_api.update_account(
        created.id,
        accounts_api.AccountUpdate(
            other_fund_items=[accounts_api.OtherFundItem(label="国债", amount=5000)],
        ),
        db,
    )
    assert updated.other_funds == 5000
    assert updated.other_fund_items[0].label == "国债"
    assert updated.initial_funds == 6000


def test_portfolio_summary_includes_other_funds_in_total_assets(db):
    """持仓汇总应将其他资产计入总资产"""
    account = Account(
        name="汇总测试",
        available_funds=10000,
        other_funds=50000,
        other_fund_items=[{"label": "理财", "amount": 50000}],
        enabled=True,
    )
    db.add(account)
    db.commit()

    payload = accounts_api.get_portfolio_summary(include_quotes=False, db=db)
    assert payload["total"]["other_funds"] == 50000
    assert payload["total"]["total_assets"] == 60000
    assert payload["accounts"][0]["other_fund_items"][0]["label"] == "理财"
    assert payload["accounts"][0]["initial_funds"] == 60000


def test_portfolio_summary_initial_funds_equals_total_assets_minus_pnl(db, monkeypatch):
    """持仓汇总中初始资金应等于总资产减盈亏"""
    monkeypatch.setattr(accounts_api, "get_hkd_cny_rate", lambda: 1.0)
    account = Account(
        name="盈亏测试",
        available_funds=20000,
        other_funds=0,
        initial_funds=0,
        base_currency="CNY",
        enabled=True,
    )
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    db.add(account)
    db.add(stock)
    db.commit()

    position = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=100.0,
        quantity=100,
        invested_amount=10000.0,
        status="open",
    )
    db.add(position)
    db.commit()

    quotes = {
        "600519": {
            "current_price": 110.0,
            "change_pct": 0.0,
            "prev_close": 110.0,
        }
    }
    monkeypatch.setattr(accounts_api, "_fetch_quotes_for_stocks", lambda stocks: quotes)

    payload = accounts_api.get_portfolio_summary(include_quotes=True, db=db)
    acc = payload["accounts"][0]
    assert acc["total_assets"] == 31000
    assert acc["total_pnl"] == 1000
    assert acc["initial_funds"] == 30000


def test_portfolio_summary_converts_foreign_currency_funds_to_cny(db, monkeypatch):
    """外币账户资金应按汇率折算后计入总资产"""
    monkeypatch.setattr(accounts_api, "get_hkd_cny_rate", lambda: 0.9)
    account = Account(
        name="港股账户",
        available_funds=10000,
        other_funds=0,
        initial_funds=10000,
        base_currency="HKD",
        enabled=True,
    )
    db.add(account)
    db.commit()

    payload = accounts_api.get_portfolio_summary(include_quotes=False, db=db)
    assert payload["accounts"][0]["base_currency"] == "HKD"
    assert payload["total"]["available_funds"] == 9000
    assert payload["total"]["total_assets"] == 9000


def test_create_position_defaults_initial_funds_to_cost_times_qty(db):
    """新建持仓时投入资金默认成本×数量"""
    account = Account(name="持仓账户", available_funds=100000, enabled=True)
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    db.add(account)
    db.add(stock)
    db.commit()

    result = accounts_api.create_position(
        accounts_api.PositionCreate(
            account_id=account.id,
            stock_id=stock.id,
            cost_price=10.5,
            quantity=100,
        ),
        db,
    )
    assert result["invested_amount"] == 1050.0

    db.refresh(account)
    assert account.initial_funds == 100000
