"""TWSE official closing index snapshot."""

import logging

import httpx

logger = logging.getLogger(__name__)


def twse_index() -> dict | None:
    try:
        response = httpx.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX", timeout=10)
        response.raise_for_status()
        response.encoding = "utf-8"
        for item in response.json():
            if item.get("指數") == "發行量加權股價指數":
                price = float(str(item["收盤指數"]).replace(",", ""))
                change = float(str(item["漲跌點數"]).replace(",", ""))
                if item.get("漲跌") == "-":
                    change = -change
                return {
                    "symbol": "TAIEX", "name": "加權指數", "market": "TW",
                    "current_price": price, "change_pct": float(item["漲跌百分比"]),
                    "change_amount": change, "prev_close": price - change,
                    "spark": [], "as_of": str(item.get("日期", "")),
                }
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning("台股加權指數取得失敗: %s", exc)
    return None
