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

    兼容两种结构:
    - 每日序列: [{date, main_net}, ...]  (旧)
    - 聚合结构: [{date:"当日", main_net}, {date:"近5日", main_net}]  (panwatch-tdx 新)

    逻辑:
    - 当日主力净流入 → 当日方向(+)
    - 近5日合计 → 趋势方向(+)
    - 两者皆正 → 偏多(接近 +1); 背离 → 打折
    返回: -1(强烈净流出) ~ +1(强烈净流入)
    """
    if not capital_flow:
        return 0.0
    items = [r for r in capital_flow if isinstance(r, dict)]
    if not items:
        return 0.0

    # 找"当日"和"近N日"两条
    today = None
    period = None
    for r in items:
        label = str(r.get("date", ""))
        if "当日" in label or "近" not in label:
            # 优先精确"当日", 否则取非"近X日"的那条
            if today is None or label == "当日":
                today = r.get("main_net", 0)
        if "近" in label and "日" in label:
            period = r.get("main_net", 0)

    # 兜底: 若没有"近X日"标签, 用全部合计
    if period is None:
        period = sum(r.get("main_net", 0) for r in items)

    today_dir = 1.0 if (today or 0) > 0 else (-1.0 if (today or 0) < 0 else 0.0)
    period_dir = 1.0 if (period or 0) > 0 else (-1.0 if (period or 0) < 0 else 0.0)

    # 基础分: 当日方向占 0.4, 期间方向占 0.6
    score = today_dir * 0.4 + period_dir * 0.6

    # 强度: 期间净流入占流通市值比(无市值时按绝对额温和归一)
    if last_close and period:
        # 无法获得流通市值, 用绝对额: 5日合计 >3亿视为强
        intensity = min(1.0, abs(period) / 3.0e8)
        score *= (0.5 + 0.5 * intensity)
    else:
        # 无价格信息, 仅方向
        score *= 0.8

    # 背离惩罚: 当日与期间方向相反
    if today_dir and period_dir and today_dir != period_dir:
        score *= 0.6

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
