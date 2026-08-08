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


def calc_capital_score(capital_flow: list, last_close: float = 0) -> float:
    """量化资金面信号 → 评分(-1~+1)。

    基于近 N 日主力净流入(亿):
    - 连续净流入天数占比
    - 合计净流入力度(日均3亿以上封顶)
    - 近1日方向加权(转弱惩罚)
    返回: -1(强烈净流出) ~ +1(强烈净流入)
    """
    if not capital_flow:
        return 0.0
    nets = [r.get("main_net", 0) for r in capital_flow if isinstance(r, dict)]
    if not nets:
        return 0.0
    n = len(nets)
    pos_ratio = sum(1 for x in nets if x > 0) / n
    total = sum(nets)
    magnitude = min(1.0, abs(total) / (n * 3.0))
    latest = nets[-1]
    score = (pos_ratio - (1 - pos_ratio)) * magnitude
    if latest < 0 and pos_ratio > 0.5:
        score *= 0.7
    return round(max(-1.0, min(1.0, score)), 3)


def build_recommendation(symbol: str, last_close: float, final: np.ndarray,
                         direction: str, expected_pct: float,
                         kronos: dict, lag: dict | None, sentiment: dict,
                         capital_score: float = 0.0) -> dict:
    """生成操作建议: 基于方向+幅度+置信区间+情绪面+资金面。

    capital_score: 资金面评分(-1~+1), 由 calc_capital_score 计算。
                    >0 主力持续净流入(偏多), <0 净流出(偏空)。
    """
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

    # 资金面联动: 主力净流入确认/背离
    cap_bull = capital_score > 0.15      # 明显偏多
    cap_bear = capital_score < -0.15     # 明显偏空
    if cap_bull and direction == "up":
        # 资金确认看多 → 升档
        if action == "可关注":
            action, tone = "积极关注", "strong_buy"
        elif action == "持有观察":
            action, tone = "可关注", "buy"
    elif cap_bear and direction == "up":
        # 资金背离看多 → 降档 + 风险提示
        if action == "积极关注":
            action, tone = "可关注", "buy"
        elif action == "可关注":
            action, tone = "持有观察", "hold"
    elif cap_bear and direction == "down":
        # 资金确认看空 → 降档更坚决
        if action == "谨慎/减仓":
            action, tone = "规避", "strong_sell"

    if sentiment_adj < -0.5 and direction == "up":
        action = f"{action}(情绪面偏空,谨慎)"
        tone = "hold"
    elif sentiment_adj > 0.5 and direction == "down":
        action = f"{action}(情绪面偏多,勿恐慌)"
        tone = "hold"

    risk_note = ""
    if spread_pct > 15:
        risk_note = f"置信区间宽({spread_pct:.0f}%),不确定性高"
    if cap_bear and direction == "up":
        risk_note = (risk_note + "; " if risk_note else "") + "资金面净流出与看多背离,警惕诱多"

    # 置信度: 基于模型离散度 + 资金面确认
    base_conf = "高" if spread_pct < 8 else "中" if spread_pct < 15 else "低"
    if cap_bull and direction == "up":
        # 资金确认 → 升半档(中→高, 低→中)
        base_conf = {"中": "高", "低": "中"}.get(base_conf, base_conf)
    elif cap_bear:
        # 资金背离/偏空 → 降半档(高→中, 中→低)
        base_conf = {"高": "中", "中": "低"}.get(base_conf, base_conf)

    return {
        "action": action,
        "tone": tone,
        "confidence": base_conf,
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
