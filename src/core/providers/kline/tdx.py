"""通达信 K 线 Provider — 通过 Go helper 调用 injoyai/tdx。

通过编译后的 Go 二进制(tools/tdx-kline/tdx-kline)直连通达信行情服务器。

约束:
- 仅 A 股(CN 市场),港股/美股不支持
- 软依赖:未编译 Go binary 时,fetch 返回明确错误
- 默认使用公开行情服务器 124.71.187.122:7709,可通过 config 自定义 host
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess

from src.collectors.kline_collector import KlineData
from src.core.providers.base import KlineProvider, ProviderRequest, ProviderResponse
from src.core.cn_symbol import get_cn_exchange

logger = logging.getLogger(__name__)

# 默认二进制路径(容器内路径)
_DEFAULT_BINARY = "tools/tdx-kline/tdx-kline"

# 默认通达信行情服务器
_DEFAULT_HOST = "124.71.187.122:7709"

# 单次最大拉取条数
_MAX_DAYS = 800

# Go binary 默认超时(秒)
_DEFAULT_TIMEOUT = 5


def _tdx_symbol(symbol: str) -> str:
    """将纯 A 股代码转为通达信前缀格式。

    sh600519 / sz000001 / bj...
    """
    exchange = get_cn_exchange(symbol)
    return f"{exchange.lower()}{symbol}"


class TdxKlineProvider(KlineProvider):
    name = "tdx"
    supports_markets = {"CN"}  # 仅 A 股

    def __init__(self, config: dict | None = None):
        super().__init__(config=config)
        self._binary = (self.config or {}).get("binary", _DEFAULT_BINARY)
        self._host = (self.config or {}).get("host", _DEFAULT_HOST)
        self._timeout = int((self.config or {}).get("timeout_sec", _DEFAULT_TIMEOUT))
        self._init_error = ""

        # 检查二进制是否存在
        if not os.path.isfile(self._binary):
            self._init_error = (
                f"通达信 helper 不存在({self._binary}),"
                f"请先编译 tools/tdx-kline: "
                f"cd tools/tdx-kline && GOOS=linux GOARCH=amd64 go build -o tdx-kline ."
            )

    def _days(self, req: ProviderRequest) -> int:
        for k, v in req.extra:
            if k == "days":
                try:
                    return max(1, min(int(v), _MAX_DAYS))
                except Exception:
                    return 60
        return 60

    def _ktype(self, req: ProviderRequest) -> str:
        """从 extra 中提取 K 线类型，默认 day"""
        for k, v in req.extra:
            if k == "type":
                if v in ("5m", "15m", "30m", "60m"):
                    return v
        return "day"

    async def fetch(self, req: ProviderRequest) -> ProviderResponse:
        if self._init_error:
            return ProviderResponse(success=False, error=self._init_error)
        if not req.symbols:
            return ProviderResponse(success=True, data=[])
        if len(req.symbols) > 1:
            return ProviderResponse(
                success=False, error="TdxKlineProvider 仅支持单 symbol"
            )
        if req.market != "CN":
            return ProviderResponse(
                success=False, error="TdxKlineProvider 仅支持 CN 市场"
            )

        symbol = req.symbols[0]
        days = self._days(req)
        ktype = self._ktype(req)
        tdx_sym = _tdx_symbol(symbol)
        logger.info(
            "TdxKlineProvider 调用 helper: binary=%s symbol=%s tdx_sym=%s host=%s days=%d type=%s",
            self._binary, symbol, tdx_sym, self._host, days, ktype,
        )

        def _run_helper() -> ProviderResponse:
            """同步函数: 在 asyncio.to_thread 中执行。"""
            try:
                proc = subprocess.run(
                    [
                        self._binary,
                        "--symbol", tdx_sym,
                        "--days", str(days),
                        "--host", self._host,
                        "--timeout", str(self._timeout),
                        "--type", ktype,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self._timeout + 2,  # 额外给 2s 缓冲
                )
            except FileNotFoundError:
                return ProviderResponse(
                    success=False,
                    error=f"通达信 helper 不存在({self._binary})",
                )
            except subprocess.TimeoutExpired:
                return ProviderResponse(
                    success=False,
                    error=f"通达信接口超时({self._host}, {self._timeout}s)",
                )
            except Exception as e:
                return ProviderResponse(
                    success=False, error=f"通达信 helper 执行失败: {e}"
                )

            if proc.returncode != 0:
                err_msg = proc.stderr.strip() or "未知错误"
                return ProviderResponse(
                    success=False, error=f"通达信接口调用失败: {err_msg}"
                )

            # 解析 JSON: Go helper 内部日志也输出到 stdout(含 ANSI 颜色码)
            stdout = proc.stdout
            # 找内容为 "[{" 开始的 JSON 数组(可能前有 ANSI 码)
            idx = stdout.find('[{"date"')
            if idx < 0:
                idx = stdout.rfind("[")
            if idx >= 0:
                # 只取 JSON 部分(从 [ 到匹配的 ])，避免尾部日志干扰
                decoder = json.JSONDecoder()
                try:
                    raw, _ = decoder.raw_decode(stdout, idx)
                except json.JSONDecodeError as e:
                    return ProviderResponse(
                        success=False,
                        error=f"通达信返回数据解析失败: {e}",
                    )
            else:
                return ProviderResponse(
                    success=False,
                    error=f"通达信未返回有效 JSON: {stdout[:200]}",
                )

            if not raw:
                return ProviderResponse(
                    success=False,
                    error=f"通达信未返回数据(symbol={symbol})",
                )

            klines: list[KlineData] = []
            for item in raw:
                try:
                    klines.append(
                        KlineData(
                            date=str(item["date"]),
                            open=float(item["open"]),
                            close=float(item["close"]),
                            high=float(item["high"]),
                            low=float(item["low"]),
                            volume=float(item["volume"]),
                        )
                    )
                except (KeyError, ValueError, TypeError) as e:
                    logger.debug(f"通达信 JSON 条目解析失败: {e}, item={item}")
                    continue

            if not klines:
                return ProviderResponse(
                    success=False,
                    error=f"通达信数据解析后为空(symbol={symbol})",
                )

            # 按日期升序排列
            klines.sort(key=lambda k: k.date)

            return ProviderResponse(success=True, data=klines[-days:])

        return await asyncio.to_thread(_run_helper)

    async def health_check(self) -> bool:
        if self._init_error:
            return False
        try:
            resp = await self.fetch(
                ProviderRequest(
                    symbols=("600519",), market="CN", extra=(("days", 5),)
                )
            )
            return resp.success and not resp.is_empty
        except Exception:
            return False