"""Directory completeness and raw overseas data protocol tests (mocked HTTP)."""

import io
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import Mock
from datetime import timedelta

import pytest
from marketdata import equities, research, MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig


def cn_rows():
    return [{"symbol": f"sh{600000+i}", "name": "Ordinary"} for i in range(997)] + [
        {"symbol": "sh688001", "name": "*ST STAR"},
        {"symbol": "sz300001", "name": "ChiNext"},
        {"symbol": "bj920001", "name": "BSE"},
    ]


def test_cn_all_pages_and_all_boards_not_a_small_fallback(monkeypatch):
    rows = cn_rows()

    def get(url, **kw):
        return (
            1000
            if url.endswith("Count")
            else rows[(kw["params"]["page"] - 1) * 100 : kw["params"]["page"] * 100]
        )

    monkeypatch.setattr(equities, "market_get", get)
    catalog = equities.fetch_cn_catalog()
    assert len(catalog.members) == catalog.raw_count == 1000
    assert {m["board"] for m in catalog.members} == {"MAIN", "STAR", "CHINEXT", "BSE"}
    assert (
        next(m for m in catalog.members if m["board"] == "STAR")["price_limit"] == 0.2
    )


@pytest.mark.parametrize("fault", ["missing_page", "duplicate", "changed_count"])
def test_cn_partial_catalog_never_accepted(monkeypatch, fault):
    calls = 0

    def get(url, **kw):
        nonlocal calls
        if url.endswith("Count"):
            calls += 1
            return 1001 if fault == "changed_count" and calls > 1 else 1000
        rows = cn_rows()
        page = kw["params"]["page"]
        result = rows[(page - 1) * 100 : page * 100]
        if page == 2 and fault == "missing_page":
            result = []
        if page == 2 and fault == "duplicate":
            result = rows[:100]
        return result

    monkeypatch.setattr(equities, "market_get", get)
    with pytest.raises(ValueError):
        equities.fetch_cn_catalog()


def test_hk_reads_actual_rows_despite_wrong_dimension_and_excludes_etf(monkeypatch):
    headers = [
        "Stock Code",
        "Name of Securities",
        "Category",
        "Sub-Category",
        "Board Lot",
        "Trading Currency",
    ]
    rows = (
        [["Updated as at 14/10/2026"], headers]
        + [
            [
                str(i + 1),
                "Company",
                "Equity",
                "Main Board",
                "1,000",
                "RMB" if i == 0 else "HKD",
            ]
            for i in range(1000)
        ]
        + [["09999", "ETF", "Exchange Traded Products", "ETF", "100", "HKD"]]
    )
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:F8"/><sheetData>'
    for i, row in enumerate(rows, 1):
        xml += (
            "<row>"
            + "".join(
                f'<c r="{chr(65+j)}{i}" t="inlineStr"><is><t>{value}</t></is></c>'
                for j, value in enumerate(row)
            )
            + "</row>"
        )
    xml += "</sheetData></worksheet>"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", xml)
    monkeypatch.setattr(equities, "market_get", lambda *a, **k: data.getvalue())
    catalog = equities.fetch_hk_catalog()
    assert len(catalog.members) == 1000 and catalog.raw_count == 1001
    assert catalog.members[0]["currency"] == "CNY" and catalog.members[0]["lot"] == 1000
    assert catalog.excluded == {"Exchange Traded Products": 1}


def test_us_common_adr_included_derivatives_etfs_test_excluded(monkeypatch):
    header = "Symbol|Security Name|Test Issue|ETF|Exchange|ACT Symbol"
    ordinary = [f"QA{i}|Company Common Stock|N|N|N|QB{i}" for i in range(1000)]
    extras = [
        "ADR|American Depositary Shares|N|N|N|ADR",
        "WARR|Warrants to purchase Common Stock|N|N|N|WARR",
        "ETF|Index|N|Y|N|ETF",
        "TEST|Test Common Stock|Y|N|N|TEST",
        "PREF|Preferred Stock|N|N|N|PREF",
        "FUND|Closed End Fund Common Stock|N|N|N|FUND",
        "DEBT|Company Notes due 2030|N|N|N|DEBT",
        "RIGHT|Subscription Rights for Common Stock|N|N|N|RIGHT",
        "LP|Limited Partnership Common Units|N|N|N|LP",
    ]
    text = "\n".join(
        [header, *ordinary, *extras, "File Creation Time: 101320261800|||||"]
    )
    monkeypatch.setattr(equities, "market_get", lambda *a, **k: text)
    with pytest.raises(ValueError, match="duplicate"):
        equities.fetch_us_catalog()  # ADR appears in both files.

    def get(url, **kw):
        return (
            text
            if url.endswith("nasdaqlisted.txt")
            else "\n".join([header, *ordinary, "File Creation Time: 101320261800|||||"])
        )

    monkeypatch.setattr(equities, "market_get", get)
    catalog = equities.fetch_us_catalog()
    assert len(catalog.members) == 2002
    assert any(m["symbol"] == "US_ADR" for m in catalog.members)
    assert not any(
        m["symbol"]
        in ("US_WARR", "US_PREF", "US_ETF", "US_TEST", "US_FUND", "US_DEBT", "US_RIGHT")
        for m in catalog.members
    )


@pytest.mark.parametrize(
    "market,symbol,ticker,tz,currency",
    [
        ("HK", "HK_00700", "0700.HK", "Asia/Hong_Kong", "HKD"),
        ("US", "US_BRK.B", "BRK-B", "America/New_York", "USD"),
    ],
)
def test_overseas_raw_adjclose_volume_timezone_and_source_proxy(
    monkeypatch, market, symbol, ticker, tz, currency
):
    stamp = int(datetime(2026, 10, 13, 9, 30, tzinfo=ZoneInfo(tz)).timestamp())
    get = Mock(
        return_value={
            "chart": {
                "result": [
                    {
                        "timestamp": [stamp],
                        "meta": {"currency": currency},
                        "indicators": {
                            "quote": [
                                {
                                    "open": [10],
                                    "high": [12],
                                    "low": [9],
                                    "close": [11],
                                    "volume": [234],
                                }
                            ],
                            "adjclose": [{"adjclose": [22]}],
                        },
                    }
                ]
            }
        }
    )
    monkeypatch.setattr(research, "market_get", get)
    md = MarketData(
        StaticConfigProvider(
            {
                "kline": [
                    SourceConfig(
                        vendor="yahoo", config={"proxy": "http://127.0.0.1:9999"}
                    )
                ]
            }
        )
    )
    result = md.research_history(
        symbol, vendor="yahoo", market=market, completed_through="2026-10-13"
    )
    assert result.bars[0]["volume"] == 234 and result.bars[0]["factor"] == 2
    assert result.bars[0]["date"] == "2026-10-13" and result.currency == currency
    assert get.call_args.kwargs["proxy"] == "http://127.0.0.1:9999"
    assert get.call_args.args[0].endswith(ticker)


@pytest.mark.parametrize("fault", ["few", "excessive", "latest"])
def test_ambiguous_ohlc_is_discarded_with_evidence_not_repaired(monkeypatch, fault):
    start = datetime(2025, 10, 1, 9, 30, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    stamps = [int((start + timedelta(days=i)).timestamp()) for i in range(100)]
    quote = {
        key: [value] * 100
        for key, value in (
            ("open", 10),
            ("high", 12),
            ("low", 9),
            ("close", 11),
            ("volume", 10000),
        )
    }
    bad = (
        list(range(6)) if fault == "excessive" else [99] if fault == "latest" else [25]
    )
    for index in bad:
        quote["high"][index] = 10
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "meta": {"currency": "HKD"},
                    "indicators": {
                        "quote": [quote],
                        "adjclose": [{"adjclose": [11] * 100}],
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(research, "market_get", lambda *a, **k: payload)
    if fault == "excessive":
        with pytest.raises(ValueError, match="quality_insufficient"):
            research.fetch_overseas_history("HK_00026", "HK")
    else:
        history = research.fetch_overseas_history("HK_00026", "HK")
        assert len(history.bars) == 99
        assert history.quality_warnings == {"invalid_ohlc": 1}
        assert len(history.rejected_dates) == 1
        assert all(b["high"] == 12 for b in history.bars)  # Never clamp high to close.
