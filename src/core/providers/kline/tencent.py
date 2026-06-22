"""腾讯 K 线 Provider:包装现有 `KlineCollector.get_klines`。

注意:`KlineCollector.get_klines` 内部已经做了 tencent → eastmoney/yfinance(US) / eastmoney(CN/HK) / stooq(US)
的硬编码 fallback,所以 TencentKlineProvider 实际是"含本地 fallback 的 Tencent 链路"。
Orchestrator 级别再串 tushare / yfinance 是更上层的兜底。
"""

from __future__ import annotations

import asyncio
import logging

from src.collectors.kline_collector import KlineCollector
from src.core.providers.base import KlineProvider, ProviderRequest, ProviderResponse
from src.models.market import MarketCode

logger = logging.getLogger(__name__)


class TencentKlineProvider(KlineProvider):
    name = "tencent"
    supports_markets = {"CN", "HK", "US"}

    def _days(self, req: ProviderRequest) -> int:
        for k, v in req.extra:
            if k == "days":
                try:
                    return int(v)
                except Exception:
                    return 60
        return 60

    async def fetch(self, req: ProviderRequest) -> ProviderResponse:
        if not req.symbols:
            return ProviderResponse(success=True, data=[])

        try:
            market_code = MarketCode(req.market)
        except ValueError:
            return ProviderResponse(success=False, error=f"unsupported market: {req.market}")

        # 当前 Orchestrator 单 symbol 用,批量按 symbol 串行(K 线接口本身就是单只)
        if len(req.symbols) > 1:
            return ProviderResponse(
                success=False,
                error="TencentKlineProvider only supports single symbol per request",
            )

        symbol = req.symbols[0]
        days = self._days(req)
        try:
            klines = await asyncio.to_thread(
                KlineCollector(market_code).get_klines, symbol, days
            )
        except Exception as e:
            return ProviderResponse(success=False, error=str(e))

        return ProviderResponse(success=True, data=klines)

    async def health_check(self) -> bool:
        try:
            resp = await self.fetch(
                ProviderRequest(symbols=("600519",), market="CN", extra=(("days", 20),))
            )
            return resp.success and not resp.is_empty
        except Exception:
            return False
