"""腾讯行情多市场字段解析回归（2026-09-28 qt.gtimg.cn 真实样本）。

三市场字段布局互不相同（取证详情见 vendors/tencent.py 模块注释）：
- CN 回归锁死原有 A 股解析（成交额取 35 复合[2]、38 换手、39 PE、44/45 市值亿、49 量比）
- HK 成交额取 37(元)、换手率由流通市值推导、49 非量比
- US 30/31=涨跌额/%、9/33=高/低、36=成交额(USD)、38=PE、43/44=市值(亿 USD)、换手率推导
"""

from marketdata.vendors.tencent import _parse_line


def _line(prefix: str, fields: dict[int, str], min_len: int = 60) -> str:
    parts = [""] * min_len
    for idx, val in fields.items():
        parts[idx] = val
    return f'v_{prefix}="' + "~".join(parts) + '";'


# 2026-09-28 盘中 hk02628 中国人寿关键位（省略与断言无关的位）
HK_LINE = _line("hk02628", {
    1: "中国人寿", 2: "02628", 3: "28.180", 4: "28.580", 5: "28.580",
    6: "19826514.0", 31: "-0.400", 32: "-1.40", 33: "29.060", 34: "28.060",
    35: "28.180", 37: "564061230.260", 38: "0", 39: "4.67",
    44: "2096.9231", 45: "7964.9939", 46: "CHINA LIFE", 49: "20.189",
})

# 2026-09-28 盘中 sh600276 恒瑞医药关键位
CN_LINE = _line("sh600276", {
    1: "恒瑞医药", 2: "600276", 3: "44.63", 4: "44.86", 5: "44.86",
    6: "321946", 31: "-0.23", 32: "-0.51", 33: "45.22", 34: "44.35",
    35: "44.63/321946/1436928219", 36: "321946", 37: "143693", 38: "0.50",
    39: "38.34", 44: "2846.95", 45: "2962.18", 49: "0.59",
}, min_len=87)

# 2026-09-25 收盘 usAAPL Apple 关键位（US 布局：30/31=涨跌额/%，9/33=高/低，36=额，38=PE，43/44=市值）
US_LINE = _line("usAAPL", {
    2: "AAPL.OQ", 3: "341.07", 4: "335.92", 5: "336.04", 6: "30002507",
    9: "341.38", 30: "5.15", 31: "1.53", 32: "341.67", 33: "334.53", 34: "USD",
    36: "10179398058", 38: "39.11", 43: "49745.43085", 44: "49776.36973",
    45: "Apple Inc.", 49: "360",
}, min_len=70)


def test_cn_parse_regression_unchanged():
    q = _parse_line(CN_LINE, "CN")
    assert q is not None
    assert q.symbol == "600276"
    assert q.current_price == 44.63
    assert q.prev_close == 44.86
    assert q.turnover == 1436928219.0  # 35 复合[2]，元
    assert q.turnover_rate == 0.50
    assert q.pe_ratio == 38.34
    assert q.circulating_market_value == 2846.95
    assert q.total_market_value == 2962.18
    assert q.volume_ratio == 0.59


def test_hk_turnover_from_field37_not_zero():
    q = _parse_line(HK_LINE, "HK")
    assert q is not None
    assert q.symbol == "02628"
    assert q.current_price == 28.18
    # 成交额在 37 位（元），35 无复合结构——修复前恒 0
    assert q.turnover == 564061230.26
    assert q.total_market_value == 7964.9939  # 亿 HKD
    assert q.circulating_market_value == 2096.9231
    assert q.pe_ratio == 4.67
    assert q.volume_ratio is None  # HK 布局 49 非量比
    assert q.change_pct == -1.40


def test_hk_turnover_rate_derived_from_circulating_mv():
    q = _parse_line(HK_LINE, "HK")
    # 流通股数 = 2096.9231亿 / 28.18 = 74.4222 亿股；换手 = 19826514 / 74.4222e8 * 100 ≈ 0.2664%
    assert q.turnover_rate is not None
    assert abs(q.turnover_rate - 0.2664) < 0.001


def test_hk_turnover_rate_none_when_circulating_missing():
    line = HK_LINE.replace("~2096.9231~", "~~")
    q = _parse_line(line, "HK")
    assert q is not None
    assert q.turnover_rate is None  # 数据不足置 None，绝不造 0


def test_us_fields_per_us_layout():
    q = _parse_line(US_LINE, "US")
    assert q is not None
    assert q.symbol == "AAPL"  # 去掉 .OQ 后缀
    assert q.current_price == 341.07
    assert q.prev_close == 335.92
    assert q.change_amount == 5.15  # 算术推导 341.07-335.92（与腾讯 30 位实测值一致）
    assert abs(q.change_pct - 1.5331) < 0.001  # 5.15/335.92
    assert q.high_price == 341.67  # USD 锚 -2（AAPL 振幅位 42=2.13 与此高低对算术互证）
    assert q.low_price == 334.53  # USD 锚 -1
    assert q.turnover == 10179398058.0  # USD 锚 +2，USD
    assert q.pe_ratio == 39.11  # USD 锚 +4
    assert q.total_market_value == 49776.36973  # USD 锚 +10，亿 USD（Apple 双源核验）
    assert q.circulating_market_value == 49745.43085  # USD 锚 +9
    # 换手率推导：流通股数 = 49745.43亿/341.07 = 145.85 亿股；30002507/145.85e8*100 ≈ 0.2057%
    assert q.turnover_rate is not None
    assert abs(q.turnover_rate - 0.2057) < 0.0005


def test_us_high_low_sanity_guard():
    """USD-2 位若低于现价（锚漂移），最高价置 None 而不是输出错误值。"""
    parts = US_LINE.rstrip('";').split('="', 1)[1].split("~")
    parts[32] = "300.00"  # 低于现价 341.07，显然不是日内最高
    q = _parse_line('v_usAAPL="' + "~".join(parts) + '";', "US")
    assert q is not None
    assert q.high_price is None
    assert q.low_price == 334.53  # 低位不受影响


def test_us_variable_length_layout_shift_robust():
    """美股为变长布局：在 28 位附近插入一个空字段模拟整体移位（USD 锚 34→35），解析结果必须不变。"""
    parts = US_LINE.rstrip('";').split('="', 1)[1].split("~")
    shifted = 'v_usAAPL="' + "~".join(parts[:30] + [""] + parts[30:]) + '";'
    base = _parse_line(US_LINE, "US")
    moved = _parse_line(shifted, "US")
    assert moved is not None and base is not None
    assert moved.turnover == base.turnover == 10179398058.0
    assert moved.pe_ratio == base.pe_ratio == 39.11
    assert moved.total_market_value == base.total_market_value == 49776.36973
    assert moved.circulating_market_value == base.circulating_market_value == 49745.43085
    assert moved.turnover_rate == base.turnover_rate


def test_unknown_market_falls_back_to_cn_layout():
    # fetch_raw(指数)以 market="" 调用,指数为 CN 布局
    q = _parse_line(CN_LINE, "")
    assert q is not None
    assert q.turnover == 1436928219.0
    assert q.volume_ratio == 0.59
