"""TDX Kline Provider 编译与基本功能测试。"""
from __future__ import annotations
import sys
import asyncio

sys.path.insert(0, ".")

from src.core.providers.base import ProviderRequest
from src.core.providers.kline.tdx import TdxKlineProvider

print("[PASS] 导入 TdxKlineProvider / ProviderRequest 成功")

# 1) 实例化(无 config)
p = TdxKlineProvider(config={})
print(f"[PASS] 实例化成功, init_error={p._init_error!r}")
print(f"       name={p.name!r}, supports_markets={p.supports_markets}")

# 2) 空 symbol → 空列表
req_empty = ProviderRequest(symbols=(), market="CN")
resp_empty = asyncio.run(p.fetch(req_empty))
assert resp_empty.success and resp_empty.data == []
print("[PASS] 空 symbol → success=True, data=[]")

# 3) CN 单 symbol (可能 pytdx 未安装或网络连不上)
req = ProviderRequest(symbols=("600519",), market="CN", extra=(("days", 30),))
resp = asyncio.run(p.fetch(req))
if resp.success:
    print(f"[PASS] fetch 返回成功, 数据条数={len(resp.data) if resp.data else 0}")
else:
    print(f"[INFO] fetch 返回 error={resp.error!r}")
    allowed = ("pytdx", "通达信", "tdx", "connect", "refused", "timeout", "网络")
    assert any(kw in resp.error.lower() for kw in allowed), f"异常错误: {resp.error}"
    print(f"[PASS] fetch 错误信息合理: {resp.error}")

# 4) 非 CN 市场 → 拒绝
req_hk = ProviderRequest(symbols=("00700",), market="HK", extra=())
resp_hk = asyncio.run(p.fetch(req_hk))
assert not resp_hk.success
assert "仅支持 CN" in resp_hk.error
print(f"[PASS] 非 CN 市场拒绝正确: {resp_hk.error}")

# 5) 多 symbol → 拒绝
req_multi = ProviderRequest(symbols=("600519", "000001"), market="CN")
resp_multi = asyncio.run(p.fetch(req_multi))
assert not resp_multi.success
assert "单 symbol" in resp_multi.error
print(f"[PASS] 多 symbol 拒绝正确: {resp_multi.error}")

# 6) health_check 不抛异常
try:
    healthy = asyncio.run(p.health_check())
    print(f"[PASS] health_check 执行完毕, result={healthy}")
except Exception as e:
    print(f"[FAIL] health_check 抛异常: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# 7) 验证 orchestrator 已注册 tdx
from src.core.providers.orchestrator import get_kline_orchestrator
orch = get_kline_orchestrator()
registered = orch.registered_providers()
assert "tdx" in registered, f"tdx 未注册, 已注册: {registered}"
print(f"[PASS] tdx 已在 orchestrator 中注册, 全部 providers: {registered}")

print("\n=== 全部验证通过 ===")