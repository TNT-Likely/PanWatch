"""FTShare vendor:龙虎榜 / 融资融券(东财 datacenter 的免费备源)。

FTShare MCP 是 streamable HTTP 端点(https://market.ft.tech/gateway/mcp,免 key),
与 wudao 同款 JSON-RPC 协议:POST + SSE 响应(data: 行),initialize 拿 Mcp-Session-Id,
后续 tools/call 必须带该 header(不带/过期 → 400)。

实测(2026-08-07,云服务器直连):
- ft_abnormal_trading_details(date=YYYYMMDD): 当日全部上榜明细,含 close/change_rate/
  turnover(成交额,元)/top_buyers/top_sellers(各前5席位, buy/sell/net 字符串元)。
  symbol 带交易所后缀(001267.XSHE / 603738.XSHG / 920117.BJSE)。
- ft_margin_trading_details(stock=600519.SH, page_size=N): 单票两融历史,最新在前。
  字段: margin_trading_balance(融资余额)/margin_trading_buying_amount(融资买入)/
  margin_trading_repayment_amount(融资偿还)/securities_lending_balance_volume(融券余量股)/
  securities_lending_selling_volume(融券卖出量)/securities_lending_repayment_volume(融券偿还量)/
  total_balance(两融余额)。
- 注意: ft_stock_holders_number(股东户数)实测全格式返回空,工具数据源本身有问题,不接入;
  margin 工具(不带 _details 的)实测 data=None,用 ft_margin_trading_details。

错误处理:任何失败返回 [],不阻断降级链(与东财 vendor 一致)。
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from marketdata.types import DragonTigerItem, MarginItem
from marketdata.vendors.base import DragonTigerVendor, MarginVendor

logger = logging.getLogger(__name__)

_FTSHARE_URL = "https://market.ft.tech/gateway/mcp"
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_sse(raw: str) -> dict | None:
    """SSE 响应解析:取最后一个 data: 行的 JSON。兼容纯 JSON 响应。"""
    texts = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            texts.append(line[5:].strip())
    payload = texts[-1] if texts else raw
    if not payload:
        return None
    try:
        return json.loads(payload)
    except Exception:
        logger.debug(f"FTShare 响应 JSON 解析失败: {payload[:200]}")
        return None


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _symbol_to_ftshare(code: str) -> str:
    """A 股 6 位代码 → ftshare 风格(sz 深市 / sh 沪市 / bj 北交所)。"""
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _strip_suffix(symbol: str) -> str:
    """001267.XSHE → 001267;兼容无后缀输入。"""
    return symbol.split(".")[0] if symbol else ""


class FtshareMCPClient:
    """FTShare MCP 轻量客户端:initialize 一次拿 session id,后续调用复用。

    线程安全(模块级单例,PanWatch 多 Agent 并发取数共享)。
    """

    _lock = threading.Lock()
    _session_id: str | None = None

    def __init__(self, url: str = _FTSHARE_URL, timeout: float = 20.0):
        self.url = url
        self.timeout = timeout

    def _post(self, body: dict, headers: dict | None = None) -> dict | None:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers or dict(_HEADERS),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid and not self._session_id:
                    with self._lock:
                        if not self._session_id:
                            self._session_id = sid
                return _parse_sse(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            logger.warning(f"FTShare HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            return None
        except Exception as e:
            logger.warning(f"FTShare 请求失败: {type(e).__name__}: {e}")
            return None

    def _rpc(self, method: str, params: dict | None = None, _id: int = 1) -> dict | None:
        body = {"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}
        headers = dict(_HEADERS)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = self._post(body, headers)
        # session 过期(400)时重新 initialize 再试一次
        if resp is None and self._session_id:
            with self._lock:
                self._session_id = None
            self.initialize()
            headers["Mcp-Session-Id"] = self._session_id or ""
            resp = self._post(body, headers)
        return resp

    def initialize(self) -> bool:
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "panwatch-marketdata", "version": "1.0"},
                },
            }
        )
        return bool(resp and resp.get("result"))

    def call_tool(self, name: str, args: dict) -> list | None:
        """调工具,返回 structuredContent.data(list);失败返回 None。"""
        if not self._session_id:
            if not self.initialize():
                return None
        resp = self._rpc("tools/call", {"name": name, "arguments": args}, _id=2)
        if not resp:
            return None
        result = resp.get("result") or {}
        if result.get("isError"):
            logger.warning(f"FTShare 工具 {name} 返回错误: {json.dumps(result, ensure_ascii=False)[:300]}")
            return None
        sc = result.get("structuredContent") or {}
        data = sc.get("data")
        return data if isinstance(data, list) else None


# 模块级单例(线程安全 + session 复用)
_client = FtshareMCPClient()


def _get_client(config: dict | None) -> FtshareMCPClient:
    global _client
    url = (config or {}).get("url")
    if url and url != _client.url:
        _client = FtshareMCPClient(url=url)
    return _client


# ============================== 龙虎榜(市场级) ==============================


class FtshareDragonTigerVendor(DragonTigerVendor):
    """龙虎榜备源:市场级,fetch 忽略 symbols,按 config["date"](YYYY-MM-DD)过滤。

    与东财 vendor 同约定:调用方未显式给 date 时返回 [](不猜今天)。
    ftshare 无 name/reason/换手率字段,给 None;net_buy 由前5买卖席位移位汇总近似。
    """

    name = "ftshare"
    supports_markets = {"CN"}

    def fetch(self, symbols: list, config: dict) -> list[DragonTigerItem]:
        date = (config or {}).get("date")
        if not date:
            return []
        # YYYY-MM-DD → YYYYMMDD
        compact = date.replace("-", "")
        client = _get_client(config)
        rows = client.call_tool("ft_abnormal_trading_details", {"date": compact})
        if not rows:
            return []

        out: list[DragonTigerItem] = []
        for row in rows:
            try:
                buyers = row.get("top_buyers") or []
                sellers = row.get("top_sellers") or []
                buy_amt = sum(_to_float(b.get("buy")) or 0.0 for b in buyers if isinstance(b, dict))
                sell_amt = sum(_to_float(s.get("sell")) or 0.0 for s in sellers if isinstance(s, dict))
                out.append(
                    DragonTigerItem(
                        trade_date=date,
                        symbol=_strip_suffix(str(row.get("symbol") or "")),
                        name="",
                        reason=None,
                        close=_to_float(row.get("close")),
                        change_pct=(_to_float(row.get("change_rate")) or 0.0) * 100,
                        net_buy=buy_amt - sell_amt,
                        buy_amt=buy_amt,
                        sell_amt=sell_amt,
                        turnover_pct=None,  # ftshare 的 turnover 是成交额(元),非换手率
                    )
                )
            except Exception as e:
                logger.debug(f"FTShare 龙虎榜行解析失败: {e}")
                continue
        return out


# ============================== 融资融券(按 symbol) ==============================


class FtshareMarginVendor(MarginVendor):
    """融资融券备源:按 symbol 逐只请求,取最新一条(ft_margin_trading_details 降序)。"""

    name = "ftshare"
    supports_markets = {"CN"}

    def fetch(self, symbols: list, config: dict) -> list[MarginItem]:
        if not symbols:
            return []
        client = _get_client(config)
        out: list[MarginItem] = []
        for sym in symbols:
            try:
                rows = client.call_tool(
                    "ft_margin_trading_details",
                    {"stock": _symbol_to_ftshare(sym.code), "page_size": 1},
                )
                if not rows:
                    continue
                row = rows[0]
                out.append(
                    MarginItem(
                        date=str(row.get("date") or "")[:10],
                        symbol=sym.code,
                        rz_balance=_to_float(row.get("margin_trading_balance")),
                        rz_buy=_to_float(row.get("margin_trading_buying_amount")),
                        rz_repay=_to_float(row.get("margin_trading_repayment_amount")),
                        rq_balance=None,  # ftshare 只有融券余量(股),无融券余额金额
                        rq_sell_vol=_to_float(row.get("securities_lending_selling_volume")),
                        rq_repay_vol=_to_float(row.get("securities_lending_repayment_volume")),
                        total_balance=_to_float(row.get("total_balance")),
                    )
                )
            except Exception as e:
                logger.debug(f"FTShare 两融取数异常 symbol={sym.code}: {e}")
                continue
        return out


# ============================== 百度财经日历(市场级) ==============================


def fetch_financial_calendar(
    start_date: str,
    end_date: str,
    *,
    category: str | None = None,
    config: dict | None = None,
) -> list[dict]:
    """百度财经日历(ft_baidu_financial_calendar)。

    市场级,按日期范围查询,不绑定个股。返回统一结构的事件列表。

    Args:
        start_date / end_date: YYYY-MM-DD(跨度 ≤3 天,ftshare 限制)
        category: 可选 economic / ipo / report_time / trade_reminder;None 返回全部
        config: 透传 datasource config(url 等)
    Returns:
        [
          {
            "title": str, "stat_date": "YYYY-MM-DD", "time": "HH:MM",
            "region": str, "category": str, "star": int(重要性 1-3),
            "former_val": str, "indicate_val": str, "market_value": str,
            "pub_val": str, "positive": str, "negative": str,
          }, ...
        ]
        失败返回 []
    """
    if not start_date or not end_date:
        return []
    args: dict = {"start_date": start_date, "end_date": end_date}
    if category:
        args["category"] = category
    client = _get_client(config)
    rows = client.call_tool("ft_baidu_financial_calendar", args)
    if not rows:
        return []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "title": str(row.get("title") or ""),
                "stat_date": str(row.get("stat_date") or ""),
                "time": str(row.get("time") or ""),
                "region": str(row.get("region") or ""),
                "category": str(row.get("category") or ""),
                "star": _to_int_star(row.get("star")),
                "former_val": str(row.get("former_val") or ""),
                "indicate_val": str(row.get("indicate_val") or ""),
                "market_value": str(row.get("market_value") or ""),
                "pub_val": str(row.get("pub_val") or ""),
                "positive": str(row.get("positive") or ""),
                "negative": str(row.get("negative") or ""),
            }
        )
    return out


def _to_int_star(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
