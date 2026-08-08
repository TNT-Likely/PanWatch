# 通过 stdio JSON-RPC 调 zhitu MCP 拿近日主力资金流(桥接, 供 8010 独立进程使用)
import json
import subprocess
from typing import Optional

_ZHITU_SERVER = "/home/ubuntu/.hermes/mcp_servers/zhitu/server.py"
_PYTHON = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"
_TIMEOUT = 25  # MCP 启动+响应超时(秒)


def _rpc(p, method: str, params=None, idn: int = 1):
    msg = {"jsonrpc": "2.0", "id": idn, "method": method}
    if params is not None:
        msg["params"] = params
    p.stdin.write((json.dumps(msg) + "\n").encode())
    p.stdin.flush()
    line = p.stdout.readline()
    return json.loads(line) if line else {}


def fetch_capital_flow(symbol: str, days: int = 5) -> list:
    """调 zhitu_capital_flow 拿近 days 日主力资金流。

    返回: [{date, main_net(亿), ddcf, zddy}, ...] (按日期升序)
    失败返回 []
    """
    code = f"{symbol}.SZ" if symbol.startswith(("0", "3")) else f"{symbol}.SH"
    # 不传 start/end, 用 latest 取最近 days 个交易日
    p = None
    try:
        p = subprocess.Popen(
            [_PYTHON, _ZHITU_SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=False,
        )
        _rpc(p, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "panwatch-bridge", "version": "1.0"},
        }, idn=1)
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n").encode())
        p.stdin.flush()
        r = _rpc(p, "tools/call", {
            "name": "zhitu_capital_flow",
            "arguments": {"code": code, "latest": days},
        }, idn=2)
        content = r.get("result", {}).get("content", [])
        txt = content[0]["text"] if content else "{}"
        data = json.loads(txt)
        rows = data.get("data", [])
        out = []
        for row in rows:
            zmb = (row.get("zmbtdcje", 0) or 0) + (row.get("zmbddcje", 0) or 0)  # 主买特大+大单
            zms = (row.get("zmsstdcje", 0) or 0) + (row.get("zmsddcje", 0) or 0)  # 主卖特大+大单
            main_net = round((zmb - zms) / 1e8, 2)  # 亿
            out.append({
                "date": row.get("t", ""),
                "main_net": main_net,
                "ddcf": row.get("ddcf"),
                "zddy": row.get("zddy"),
            })
        # zhitu 返回是降序(最新在前), 转升序便于展示
        out.sort(key=lambda x: x["date"])
        return out
    except Exception:
        return []
    finally:
        if p:
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "002361"
    print(json.dumps(fetch_capital_flow(sym, 5), ensure_ascii=False, indent=2))
