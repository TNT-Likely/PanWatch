"""TradingAgents 兼容补丁与迁移回归测试。"""

from src.modules.automation.tradingagents.runtime_support import apply_compat_patches


def test_normalize_symbol_cn_suffix_patch():
    """A 股裸代码补 Yahoo 后缀（生产曾因 600276 裸代码 404）；非 A 股路径不变。"""
    apply_compat_patches()
    from tradingagents.dataflows.symbol_utils import normalize_symbol
    from tradingagents.dataflows import y_finance

    assert normalize_symbol("600276") == "600276.SS"
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("300750") == "300750.SZ"
    assert normalize_symbol("688981") == "688981.SS"
    # 非 A 股走原逻辑
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("600519.SH") == "600519.SS"
    assert normalize_symbol("700.HK") == "0700.HK"
    # 模块级绑定副本也被 patch（y_finance 内部 7 处引用走新逻辑）
    assert y_finance.normalize_symbol("600276") == "600276.SS"


def test_m128_tradingagents_timeout_migration():
    """迁移 _m128：仅把 seed 旧默认 15 提到 30，显式定制值(如 20)不动。"""
    import json

    from sqlalchemy import create_engine, text

    from src.platform.persistence.migrations import _m128_tradingagents_timeout_default

    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE agent_configs (id INTEGER PRIMARY KEY, name TEXT UNIQUE, config TEXT)")
        )
        conn.execute(
            text("INSERT INTO agent_configs (name, config) VALUES ('tradingagents', :cfg)"),
            {"cfg": json.dumps({"timeout_minutes": 15, "debate_rounds": 1})},
        )
        _m128_tradingagents_timeout_default(conn)
        cfg = json.loads(
            conn.execute(
                text("SELECT config FROM agent_configs WHERE name='tradingagents'")
            ).scalar()
        )
        assert cfg["timeout_minutes"] == 30
        assert cfg["debate_rounds"] == 1  # 其余键不动

        # 显式定制值(20)不被覆盖
        conn.execute(
            text("UPDATE agent_configs SET config = :cfg WHERE name='tradingagents'"),
            {"cfg": json.dumps({"timeout_minutes": 20})},
        )
        _m128_tradingagents_timeout_default(conn)
        cfg = json.loads(
            conn.execute(
                text("SELECT config FROM agent_configs WHERE name='tradingagents'")
            ).scalar()
        )
        assert cfg["timeout_minutes"] == 20
