"""预测回测 API:代理到独立预测引擎服务(:8010)。

PanWatch 前端预测页调用本 API,后端转发到 Hermes 主机的
forecast_server.py(:8010),避免前端直连内部端口。
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

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


def _log_forecast(level: str, msg: str, task_id: str = "", **extra):
    """把预测日志写入 PanWatch 统一日志表(带 agent_name=forecast)。

    用 log_context 绑定 agent_name + trace_id,前端日志中心可筛选"预测"链路。
    """
    try:
        from src.core.log_context import log_context

        with log_context(
            trace_id=task_id or f"forecast-{msg[:20]}",
            agent_name="forecast",
        ):
            getattr(logger, level)(msg, extra=extra)
    except Exception:
        # 日志写入失败不影响主流程
        try:
            getattr(logger, level)(msg)
        except Exception:
            pass


@router.get("/forecast/predict")
async def forecast_predict(
    symbol: str = Query(..., description="6位A股代码"),
    days: int = Query(5, ge=1, le=20, description="预测天数"),
    task_id: str = Query("", description="预测任务ID"),
    target_date: str = Query("", description="预测目标日期 YYYY-MM-DD"),
):
    """多模型预测(Kronos+XGBoost+回归)。"""
    _log_forecast("info", f"预测开始: {symbol} {days}天", task_id=task_id)
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/predict",
                params={"symbol": symbol, "days": days, "task_id": task_id, "target_date": target_date},
            )
            r.raise_for_status()
            data = r.json()
            direction = data.get("direction", "?")
            expected = data.get("expected_pct", 0)
            _log_forecast(
                "info",
                f"预测完成: {symbol} → {data.get('prediction', [])} ({direction} {expected:+.1f}%)",
                task_id=task_id,
            )
            return data
    except httpx.HTTPStatusError as e:
        _log_forecast("error", f"预测失败: {symbol} HTTP {e.response.status_code}", task_id=task_id)
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        _log_forecast("error", f"预测失败: {symbol} 引擎未启动", task_id=task_id)
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("预测请求失败")
        _log_forecast("error", f"预测异常: {symbol} {e}", task_id=task_id)
        raise HTTPException(500, f"预测失败: {e}")


@router.get("/forecast/predict/status")
async def forecast_predict_status(
    task_id: str = Query(..., description="预测任务ID"),
):
    """查询预测任务进度与日志。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/predict/status",
                params={"task_id": task_id},
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("预测状态查询失败")
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/forecast/history")
async def forecast_history(
    symbol: str = Query("", description="股票代码过滤"),
    limit: int = Query(50, ge=1, le=200),
):
    """历史预测列表(供回查)。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/forecast/history",
                params={"symbol": symbol, "limit": limit},
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("历史查询失败")
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/forecast/card")
async def forecast_card(
    symbol: str = Query(..., description="6位A股代码"),
    task_id: str = Query("", description="预测任务ID"),
):
    """预测结果图片卡片(PNG)。"""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/forecast/card",
                params={"symbol": symbol, "task_id": task_id},
            )
            r.raise_for_status()
            return Response(
                content=r.content,
                media_type="image/png",
                headers={"Content-Disposition": f'inline; filename="forecast_{symbol}.png"'},
            )
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("卡片生成失败")
        raise HTTPException(500, f"卡片生成失败: {e}")


@router.get("/forecast/backtest")
async def forecast_backtest(
    symbol: str = Query(..., description="6位A股代码"),
):
    """历史预测回测(方向命中率)。"""
    _log_forecast("info", f"回测开始: {symbol}")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/backtest",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            data = r.json()
            _log_forecast(
                "info",
                f"回测完成: {symbol} 命中率 {data.get('direction_accuracy_pct', 0)}% ({data.get('direction_hits', 0)}/{data.get('windows_tested', 0)})",
            )
            return data
    except httpx.HTTPStatusError as e:
        _log_forecast("error", f"回测失败: {symbol} HTTP {e.response.status_code}")
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        _log_forecast("error", f"回测失败: {symbol} 引擎未启动")
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("回测请求失败")
        _log_forecast("error", f"回测异常: {symbol} {e}")
        raise HTTPException(500, f"回测失败: {e}")


@router.get("/forecast/models")
async def forecast_models():
    """预测引擎模型清单(设置页展示)。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{FORECAST_ENGINE_URL}/forecast/models")
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("模型清单查询失败")
        raise HTTPException(500, f"查询失败: {e}")


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


@router.get("/forecast/report/generate")
async def forecast_report_generate(
    symbol: str = Query(..., description="6位A股代码"),
    task_id: str = Query("", description="可选预测任务ID"),
):
    """生成预测报告 双格式(dashboard短版 + detail完整版)。"""
    _log_forecast("info", f"报告生成开始: {symbol}", task_id=task_id)
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/report/generate",
                params={"symbol": symbol, "task_id": task_id},
            )
            r.raise_for_status()
            data = r.json()
            _log_forecast("info", f"报告生成完成: {symbol} report_id={data.get('report_id')}", task_id=task_id)
            return data
    except httpx.HTTPStatusError as e:
        _log_forecast("error", f"报告生成失败: {symbol} HTTP {e.response.status_code}", task_id=task_id)
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        _log_forecast("error", f"报告生成失败: {symbol} 引擎未启动", task_id=task_id)
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("报告生成失败")
        _log_forecast("error", f"报告生成异常: {symbol} {e}", task_id=task_id)
        raise HTTPException(500, f"报告生成失败: {e}")


@router.get("/forecast/report/backtest")
async def forecast_report_backtest(
    symbol: str = Query(..., description="6位A股代码"),
):
    """生成回测报告 双格式。"""
    _log_forecast("info", f"回测报告生成开始: {symbol}")
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/report/backtest",
                params={"symbol": symbol},
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        _log_forecast("error", f"回测报告失败: {symbol} HTTP {e.response.status_code}")
        raise HTTPException(e.response.status_code, "预测引擎错误")
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("回测报告生成失败")
        _log_forecast("error", f"回测报告异常: {symbol} {e}")
        raise HTTPException(500, f"回测报告失败: {e}")


@router.get("/forecast/report/list")
async def forecast_report_list(
    symbol: str = Query("", description="股票代码过滤"),
    limit: int = Query(20, ge=1, le=100),
):
    """预测报告列表。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/report/list",
                params={"symbol": symbol, "limit": limit},
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("报告列表查询失败")
        raise HTTPException(500, f"查询失败: {e}")


@router.get("/forecast/report/get")
async def forecast_report_get(
    report_id: int = Query(..., description="报告ID"),
):
    """获取单条预测报告详情。"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/report/get",
                params={"report_id": report_id},
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("报告查询失败")
        raise HTTPException(500, f"查询失败: {e}")


@router.post("/forecast/report/push")
async def forecast_report_push(payload: dict):
    """推送报告到企微(经 Hermes webhook 中转)。"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"{FORECAST_ENGINE_URL}/report/push",
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            # 包成 ApiResponse 外壳, 符合前端 fetchAPI 约定
            return {"code": 0, "data": data, "message": ""}
    except httpx.ConnectError:
        return {"code": 503, "data": None, "message": "预测引擎未启动(需在主机运行 forecast_server.py)"}
    except Exception as e:
        logger.exception("报告推送失败")
        return {"code": 500, "data": None, "message": f"推送失败: {e}"}


@router.get("/stocks/search")
async def stocks_search(
    q: str = Query(..., description="股票名称或代码"),
    limit: int = Query(10, ge=1, le=20),
):
    """股票名称/代码搜索。"""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(
                f"{FORECAST_ENGINE_URL}/stocks/search",
                params={"q": q, "limit": limit},
            )
            r.raise_for_status()
            return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "预测引擎未启动(需在主机运行 forecast_server.py)")
    except Exception as e:
        logger.exception("股票搜索失败")
        raise HTTPException(500, f"搜索失败: {e}")
