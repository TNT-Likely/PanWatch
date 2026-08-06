# 工具: 任务/推荐/方向标签
import json, time, uuid, threading
from datetime import datetime
import numpy as np

# 任务进度存储
_tasks = {}
_tasks_lock = threading.Lock()



def _log(task_id: str, msg: str):
    """向任务追加日志。"""
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t:
            t["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")



def _set_status(task_id: str, status: str):
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t:
            t["status"] = status



def new_task() -> str:
    task_id = uuid.uuid4().hex[:12]
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "logs": [], "result": None}
    return task_id



def direction_label(direction: str) -> str:
    return {"up": "看多", "down": "看空", "flat": "横盘"}.get(direction, direction)



def build_recommendation(symbol: str, last_close: float, final: np.ndarray,
                         direction: str, expected_pct: float,
                         kronos: dict, lag: dict | None, sentiment: dict) -> dict:
    """生成操作建议: 基于方向+幅度+置信区间+情绪面。"""
    spread_pct = 0
    if kronos:
        p5 = kronos.get("p5", [])
        p95 = kronos.get("p95", [])
        if p5 and p95:
            spread_pct = (p95[0] - p5[0]) / last_close * 100

    sentiment_adj = (sentiment or {}).get("adjustment_pct", 0) or 0

    if direction == "up":
        if expected_pct >= 8:
            action, tone = "积极关注", "strong_buy"
        elif expected_pct >= 3:
            action, tone = "可关注", "buy"
        else:
            action, tone = "持有观察", "hold"
    elif direction == "down":
        if expected_pct <= -8:
            action, tone = "规避", "strong_sell"
        elif expected_pct <= -3:
            action, tone = "谨慎/减仓", "sell"
        else:
            action, tone = "观望", "hold"
    else:
        action, tone = "观望", "hold"

    if sentiment_adj < -0.5 and direction == "up":
        action = f"{action}(情绪面偏空,谨慎)"
        tone = "hold"
    elif sentiment_adj > 0.5 and direction == "down":
        action = f"{action}(情绪面偏多,勿恐慌)"
        tone = "hold"

    risk_note = ""
    if spread_pct > 15:
        risk_note = f"置信区间宽({spread_pct:.0f}%),不确定性高"

    return {
        "action": action,
        "tone": tone,
        "confidence": "高" if spread_pct < 8 else "中" if spread_pct < 15 else "低",
        "risk_note": risk_note,
        "target_price": round(float(final[-1]), 2),
        "expected_pct": round(expected_pct, 2),
        "stop_loss": round(last_close * 0.95, 2),
        "summary": (
            f"{direction_label(direction)} {abs(expected_pct):.1f}%,目标{round(float(final[-1]), 2)},"
            f"止损参考{round(last_close * 0.95, 2)}"
            + (f";{risk_note}" if risk_note else "")
        ),
    }