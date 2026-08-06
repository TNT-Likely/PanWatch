"""预测回测 API:代理到独立预测引擎服务(:8010)。

PanWatch 前端预测页调用本 API,后端转发到 Hermes 主机的
forecast_server.py(:8010),避免前端直连内部端口。
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

# 预测引擎地址: 优先环境变量,否则自动探测主机 IP
# (容器内 127.0.0.1 是容器自己,必须用主机 IP;Linux Docker 无 host.docker.internal)
def _detect_engine_url() -> str:
    import os

    env = os.getenv("FORECAST_ENGINE_URL")
    if env:
        return env
    # 从默认网关推断主机 IP(容器内 /proc/net/route)
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":  # default route
                    ip_int = int(parts[2], 16)
                    host_ip = f"{(ip_int & 0xFF)}.{(ip_int >> 8 & 0xFF)}.{(ip_int >> 16 & 0xFF)}.{(ip_int >> 24 & 0xFF)}"
                    return f"http://{host_ip}:8010"
    except Exception:
        pass
    return "http://127.0.0.1:8010"


FORECAST_ENGINE_URL = _detect_engine_url()


@router.get("/forecast/predict")
async def forecast_predict(
    symbol: str = Query(..., description="6位A股代码"),
    days: int = Query(5, ge=1, le=20, description="预测天数"),
):
    """多模型预测(Kronos+XGBoost+回归)。"""
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/predict",
                params={"symbol": symbol, "days": days},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("预测请求失败")
        raise HTTPException(500, f"预测失败: {e}")


@router.get("/forecast/backtest")
async def forecast_backtest(
    symbol: str = Query(..., description="6位A股代码"),
):
    """历史预测回测(方向命中率)。"""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/backtest",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("回测请求失败")
        raise HTTPException(500, f"回测失败: {e}")


@router.get("/forecast/health")
async def forecast_health():
    """预测引擎健康检查。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{FORECAST_ENGINE_URL}/health")
            r.raise_for_status()
            return r.json()
    except Exception:
        return {"status": "unreachable", "engine_url": FORECAST_ENGINE_URL}
