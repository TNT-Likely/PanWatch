"""因子自校准(M2):把孤立的 IC/IR 接进因子权重的轻量标定闭环。

把 `factor_eval.evaluate_factor_ic` 算出的每因子 IC/IR,符号感知地转成目标权重,
EMA 平滑 + clamp 后写入 `FactorWeight`(并审计到 `FactorWeightHistory`)。
镜像 `strategy_engine.rebalance_strategy_weights` 的机制,但作用于因子级。

设计要点(见 .docs/factor-self-calibration-design-2026-06-20.md):
- IC 必须测在「原始因子」上(快照存 raw,权重只在合成时乘),否则闭环自我强化失真。
- 惩罚因子(risk/crowd)IC 预期为负:用 −IC 驱动,惩罚有效→提权,失效→降权。
- 只吃「持有期已走完」的 outcome(point-in-time),防未来函数。
"""

from __future__ import annotations

import logging

from src.core.factor_eval import evaluate_factor_ic
from src.core.factor_weights import (
    CALIBRATABLE_FACTORS,
    MARKETS,
    PENALTY_FACTORS,
    get_factor_weights,
)
from src.core.timezone import utc_now
from src.web.database import SessionLocal
from src.web.models import FactorWeight, FactorWeightHistory

logger = logging.getLogger(__name__)

# 归一化基准:一个「不错」的 IR / 一个「有意义」的单期 IC。
IR_REF = 0.5
IC_REF = 0.05


def compute_target(factor_code: str, ic, ir, *, beta: float = 0.4) -> float | None:
    """由 IC/IR 算目标权重;优先 IR(更稳),fallback IC;惩罚因子翻符号。

    返回 None 表示信息不足(IC、IR 均缺失),应跳过该因子。
    term 归一化并 clamp 到 [-1, 1];target = 1 + beta·term。
    """
    if ir is not None:
        term = ir / IR_REF
    elif ic is not None:
        term = ic / IC_REF
    else:
        return None
    if factor_code in PENALTY_FACTORS:
        term = -term  # 惩罚因子:IC 越负越该信
    term = max(-1.0, min(1.0, term))
    return 1.0 * (1.0 + beta * term)


def blend(old: float, target: float, *, alpha: float = 0.35,
          lo: float = 0.5, hi: float = 1.5) -> float:
    """EMA 平滑(防跳变)+ clamp 到 [lo, hi]。"""
    new = old * (1.0 - alpha) + target * alpha
    return max(lo, min(hi, new))


def calibrate_factor_weights(
    market: str, *, alpha: float = 0.35, beta: float = 0.4,
    clamp: tuple[float, float] = (0.5, 1.5),
    min_samples: int = 20, horizon: int = 5, days: int = 90, db=None,
) -> dict:
    """对单个市场跑一轮因子权重标定,写 FactorWeight + FactorWeightHistory。

    门控:is_pinned / auto_calibrate=False / 样本不足 / IC 缺失 → 跳过(不改权重)。
    每次都把最近观测(last_ic/ir/sample_size)写入 FactorWeight.meta 供 API 展示;
    History 只记录「实际发生的调整」(reason=auto),避免冷启动期审计噪声。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        ic_result = evaluate_factor_ic(
            days=days, horizon=horizon, min_samples=min_samples, market=market, db=db
        )
        factors = ic_result.get("factors", {})
        get_factor_weights(market, db=db)  # 确保 5 个因子行存在

        lo, hi = float(clamp[0]), float(clamp[1])
        changed = 0
        rows_changed: list[dict] = []

        for code in CALIBRATABLE_FACTORS:
            row = (
                db.query(FactorWeight)
                .filter(FactorWeight.factor_code == code, FactorWeight.market == market)
                .first()
            )
            old = float(row.weight)
            stats = factors.get(code, {})
            ic = stats.get("ic")
            ir = stats.get("ir")
            n = int(stats.get("sample_size", 0))

            # 记录最近一次观测(供 API 展示),无论是否调整。
            row.meta = {
                **(row.meta or {}),
                "last_ic": ic, "last_ir": ir, "last_sample_size": n,
                "last_calibrated_at": utc_now().isoformat(),
            }

            if row.is_pinned or not row.auto_calibrate:
                continue
            if n < min_samples or ic is None:
                continue
            target = compute_target(code, ic, ir, beta=beta)
            if target is None:
                continue
            new = round(blend(old, target, alpha=alpha, lo=lo, hi=hi), 4)
            if abs(new - old) < 0.01:
                continue

            row.weight = new
            row.reason = f"auto(ic={ic}, ir={ir}, n={n})"
            row.effective_from = utc_now()
            row.updated_at = utc_now()
            db.add(FactorWeightHistory(
                factor_code=code, market=market, old_weight=old, new_weight=new,
                ic=ic, ir=ir, sample_size=n, reason="auto",
                meta={"target": round(target, 4), "alpha": alpha},
            ))
            changed += 1
            rows_changed.append({
                "factor_code": code, "old_weight": old, "new_weight": new, "sample_size": n,
            })

        db.commit()
        return {"market": market, "checked": len(CALIBRATABLE_FACTORS),
                "changed": changed, "rows": rows_changed}
    except Exception as e:  # pragma: no cover - 防御性
        logger.warning(f"[因子标定] market={market} 失败: {e}")
        db.rollback()
        return {"market": market, "checked": 0, "changed": 0, "rows": [], "error": str(e)}
    finally:
        if own:
            db.close()


def calibrate_all_markets(*, db=None, **kwargs) -> dict[str, dict]:
    """对所有市场(CN/HK/US)各跑一轮因子标定;供调度器每日 outcome 评估后调用。

    kwargs 透传给 calibrate_factor_weights(alpha/beta/clamp/min_samples/horizon/days)。
    """
    own = db is None
    db = db or SessionLocal()
    try:
        return {m: calibrate_factor_weights(m, db=db, **kwargs) for m in MARKETS}
    finally:
        if own:
            db.close()
