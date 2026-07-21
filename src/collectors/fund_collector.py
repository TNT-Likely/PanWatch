"""基金数据采集器（东方财富）"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime

import httpx

from src.core.marketdata_client import md_quote_rows

FUND_HOLDINGS_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
FUND_DETAIL_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    cleaned = _TAG_RE.sub("", text or "")
    return html.unescape(cleaned).strip()


def _to_float(text: str) -> float | None:
    s = (text or "").strip().replace(",", "")
    if not s:
        return None
    if s.endswith("%"):
        s = s[:-1]
    try:
        return float(s)
    except Exception:
        return None


def _extract_content_payload(raw: str) -> str:
    # 典型返回: var apidata={content:"...",arryear:[...]}
    m = re.search(r"content\s*:\s*\"([\s\S]*?)\"\s*,\s*arryear", raw)
    if not m:
        return ""
    payload = m.group(1)
    payload = payload.replace('\\"', '"').replace("\\/", "/").replace("\\n", "")
    return payload


def fetch_fund_top_holdings(fund_code: str, topline: int = 10) -> list[dict]:
    params = {
        "type": "jjcc",
        "code": str(fund_code).strip(),
        "topline": str(topline),
        "year": "",
        "month": "",
        "_": str(int(datetime.now().timestamp() * 1000)),
    }
    with httpx.Client() as client:
        resp = client.get(FUND_HOLDINGS_URL, params=params, timeout=15)
        raw = resp.text

    content = _extract_content_payload(raw)
    if not content:
        return []

    table = re.search(r"<table[\s\S]*?</table>", content)
    if not table:
        return []
    table_html = table.group(0)

    headers = [_strip_html(x) for x in re.findall(r"<th[^>]*>([\s\S]*?)</th>", table_html)]
    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", table_html)

    if not rows or not headers:
        return []

    code_idx = next((i for i, h in enumerate(headers) if "代码" in h), -1)
    name_idx = next((i for i, h in enumerate(headers) if "名称" in h), -1)
    weight_idx = next((i for i, h in enumerate(headers) if "净值比例" in h or "占净值" in h), -1)

    result: list[dict] = []
    for row in rows:
        cells = [_strip_html(x) for x in re.findall(r"<td[^>]*>([\s\S]*?)</td>", row)]
        if not cells:
            continue

        code = cells[code_idx] if code_idx >= 0 and code_idx < len(cells) else ""
        name = cells[name_idx] if name_idx >= 0 and name_idx < len(cells) else ""
        weight_text = cells[weight_idx] if weight_idx >= 0 and weight_idx < len(cells) else ""
        if not code or not name:
            continue

        result.append({
            "code": code,
            "name": name,
            "weight": _to_float(weight_text),
            "weight_text": weight_text,
            "change_pct": None,
        })
        if len(result) >= topline:
            break

    # 追加持仓股票当日涨跌幅（使用 PanWatch 的 marketdata 包）
    # 提取 A 股（6位数字）和港股（5位数字）代码
    cn_codes = [x["code"] for x in result if re.fullmatch(r"\d{6}", str(x["code"]))]
    hk_codes = [x["code"] for x in result if re.fullmatch(r"\d{5}", str(x["code"]))]

    quote_map: dict[str, dict] = {}

    # 获取 A 股行情
    if cn_codes:
        try:
            rows = md_quote_rows(cn_codes, "CN")
            for r in rows:
                quote_map[str(r.get("symbol") or "")] = r
        except Exception:
            pass

    # 获取港股行情
    if hk_codes:
        try:
            rows = md_quote_rows(hk_codes, "HK")
            for r in rows:
                quote_map[str(r.get("symbol") or "")] = r
        except Exception:
            pass

    for item in result:
        code = str(item.get("code") or "")
        q = quote_map.get(code)
        if q:
            item["change_pct"] = q.get("change_pct")

    return result


def _downsample(points: list[dict], max_points: int = 180) -> list[dict]:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    out = []
    i = 0.0
    while int(i) < len(points):
        out.append(points[int(i)])
        i += step
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def fetch_fund_performance(fund_code: str) -> dict:
    with httpx.Client() as client:
        resp = client.get(FUND_DETAIL_URL.format(code=str(fund_code).strip()), timeout=15)
        raw = resp.text

    # Data_ACWorthTrend: [[ts, 累计净值], ...]
    m = re.search(r"Data_ACWorthTrend\s*=\s*(\[[\s\S]*?\]);", raw)
    points: list[dict] = []
    since_return_pct: float | None = None
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list) and data:
                base = float(data[0][1]) if data[0] and len(data[0]) > 1 else None
                for row in data:
                    if not isinstance(row, list) or len(row) < 2:
                        continue
                    ts = int(row[0])
                    value = float(row[1])
                    ret = None
                    if base and base != 0:
                        ret = (value / base - 1.0) * 100.0
                    points.append({"ts": ts, "value": value, "return_pct": ret})
                if points and points[0].get("value"):
                    first = float(points[0]["value"])
                    last = float(points[-1]["value"])
                    if first != 0:
                        since_return_pct = (last / first - 1.0) * 100.0
        except Exception:
            points = []

    points = _downsample(points, max_points=220)
    return {
        "points": points,
        "since_return_pct": since_return_pct,
    }
