"""TradingAgents toolkit 并发数据隔离回归测试。

根因 bug:_PANWATCH_DATA_CACHE 曾是模块级全局 dict,两只标的并发深度分析时
(asyncio.to_thread worker 线程)互相覆盖 —— 广汽 601238 的报告混入赛力斯 601127
的 K线/价格。改成 ContextVar 后,每个并发任务(copy_context)拿独立副本,互不串台。

本测试用 contextvars.copy_context() 模拟两个并发任务,直接复现并验证修复。
注意:只测 _serve_from_panwatch / _stock_meta_header 的直接路径,不经过
_patched_route_to_vendor(避免触发 _emit_toolkit_log → log_context/DB)。
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace

from src.agents.tradingagents import toolkit_adapter as ta


def _stock(symbol: str, name: str):
    return SimpleNamespace(symbol=symbol, name=name, market=SimpleNamespace(value="CN"))


def _kline(date: str, close: float):
    return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


def _data(symbol: str, name: str, close: float):
    return {
        "stock": _stock(symbol, name),
        "quote": {"name": name, "current_price": close},
        "klines": [_kline("2026-05-01", close), _kline("2026-05-02", close + 1)],
    }


GAC = _data("601238", "广汽集团", 9.50)        # 广汽
SERES = _data("601127", "赛力斯", 83.26)        # 赛力斯


def test_stock_meta_header_uses_current_context():
    """_stock_meta_header 读当前 context 的 stock,而非进程全局。"""
    def _run():
        with ta.panwatch_data_context(GAC):
            return ta._stock_meta_header("601238")
    header = contextvars.copy_context().run(_run)
    assert "广汽集团" in header
    assert "赛力斯" not in header
    assert "9.50" in header


def test_two_concurrent_contexts_do_not_cross_talk():
    """复现生产 bug:任务A(广汽)运行中,任务B(赛力斯)注入数据,
    A 后续工具调用必须仍读到广汽 —— 旧的全局 dict 实现这里会串成赛力斯。"""
    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()

    # A 先进入 context(模拟 worker A 开始,数据已注入但还没跑完工具)
    ctx_a.run(lambda: ta._PANWATCH_DATA.set(dict(GAC)))
    # B 随后进入 context(并发任务 B 启动)—— 旧实现此处会覆盖全局
    ctx_b.run(lambda: ta._PANWATCH_DATA.set(dict(SERES)))

    # A 继续跑工具调用:get_stock_data(601238) 必须返回广汽 K线/价格
    out_a = ctx_a.run(lambda: ta._serve_from_panwatch("get_stock_data", "601238", {}, args=("601238",)))
    out_b = ctx_b.run(lambda: ta._serve_from_panwatch("get_stock_data", "601127", {}, args=("601127",)))

    assert "广汽集团" in out_a and "赛力斯" not in out_a
    assert "9.5" in out_a            # 广汽收盘价
    assert "83.26" not in out_a      # 不含赛力斯价格

    assert "赛力斯" in out_b and "广汽集团" not in out_b


def test_context_restored_after_exit():
    """panwatch_data_context 退出后,当前 context 的数据还原为空。"""
    def _run():
        assert ta._cache() == {}
        with ta.panwatch_data_context(SERES):
            assert ta._cache().get("stock").symbol == "601127"
        # 退出后还原
        return ta._cache()
    assert contextvars.copy_context().run(_run) == {}


def test_nested_contexts_restore_outer():
    """嵌套 context:内层退出后外层数据恢复(token reset 语义)。"""
    def _run():
        with ta.panwatch_data_context(GAC):
            assert ta._cache().get("stock").symbol == "601238"
            with ta.panwatch_data_context(SERES):
                assert ta._cache().get("stock").symbol == "601127"
            # 内层退出,外层广汽恢复
            assert ta._cache().get("stock").symbol == "601238"
    contextvars.copy_context().run(_run)


# ---------------------------------------------------------------------------
# 行业/主题新闻关键词搜索(B 功能:get_news 非 ticker 词 → 实时搜中文新闻)
# ---------------------------------------------------------------------------

def test_keyword_news_formats():
    """行业/主题词搜中文新闻:格式化含关键词 + 标题"""
    from unittest.mock import patch
    from datetime import datetime
    from src.collectors.news_collector import NewsItem

    def fake(kw):
        return [NewsItem(source="em", external_id="1", title=f"{kw}动态", content="", publish_time=datetime(2026, 5, 30))]
    with patch("src.core.marketdata_client.md_news_by_keyword", fake):
        r = ta._serve_keyword_news("汽车行业")
    assert "汽车行业" in r and "动态" in r


def test_keyword_news_empty_warns_no_fabrication():
    """搜不到行业新闻时返回防编造提示(避免 LLM 凭空编)"""
    from unittest.mock import patch

    def fake_empty(kw):
        return []
    with patch("src.core.marketdata_client.md_news_by_keyword", fake_empty):
        r = ta._serve_keyword_news("某冷门主题")
    assert "未搜到" in r and "不要编造" in r


if __name__ == "__main__":
    import unittest
    unittest.main()
