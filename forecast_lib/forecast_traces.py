# 预测中间数据采集层 — 把 4 个模型各自预测 + LLM 情绪打分的原始数据全部结构化
# 落库,供回测和事后报告生成使用。
#
# 设计原则:
# 1. 不阻塞主流程 — 写入失败只 log, 不影响预测结果
# 2. 全部结构化 — 不写文本, 全部 JSON 字段, 方便事后 LLM 抽取
# 3. 一行一次预测 (prediction_runs), 一行一模型输出 (model_outputs),
#    一行一次 LLM 调用 (sentiment_evals)
#    三张表外键串联, 任意环节都能溯源
#
# schema:
#   prediction_runs(id, symbol, stock_name, last_close, last_date, target_date,
#                   pred_days, final_direction, final_expected_pct,
#                   final_target_price, final_stop_loss, task_id, created_at,
#                   models_summary, sentiment_adj_total) — 每次预测一行
#   prediction_model_outputs(id, run_id, model_name, model_pred_close,
#                            model_pred_direction, model_confidence,
#                            model_weight, run_time_ms, raw_output_json) — 每模型一行
#   prediction_sentiment_evals(id, run_id, source, events_text,
#                              score, reason, adjustment_pct, prompt, response,
#                              latency_ms, error) — 每次 LLM 情绪打分一行
#   backtest_results(id, symbol, run_at, window_days, horizon_days,
#                    direction_accuracy_pct, llm_adjustment_win_pct,
#                    model_hits_json, report_id, samples_json) — 每次回测一行
#   prediction_reports(id, run_id, symbol, report_kind, dashboard_md,
#                      detail_md, model_used, tokens_used, created_at, status) — 报告表
#   backtest_reports(id, backtest_id, symbol, dashboard_md, detail_md,
#                    model_used, tokens_used, created_at, status) — 报告表
#
# 全部外键 ON DELETE CASCADE: 删 run 自动清 dependent 行
from __future__ import annotations

import json
import logging
import os
import sqlite3 as _sqlite3
import time
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_DB = os.path.expanduser("~/.panwatch_forecast.db")


def _ensure_tables() -> None:
    """建表 + 索引 (轻量 migration, sqlite ALTER 加列兼容)。"""
    conn = _sqlite3.connect(_HISTORY_DB)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS prediction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                stock_name TEXT DEFAULT '',
                last_close REAL,
                last_date TEXT,
                target_date TEXT DEFAULT '',
                pred_days INTEGER,
                final_direction TEXT DEFAULT '',
                final_expected_pct REAL,
                final_target_price REAL,
                final_stop_loss REAL,
                task_id TEXT DEFAULT '',
                models_summary TEXT DEFAULT '{}',
                sentiment_adj_total REAL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_pred_runs_symbol ON prediction_runs(symbol, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pred_runs_task ON prediction_runs(task_id);

            CREATE TABLE IF NOT EXISTS prediction_model_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                model_pred_close REAL,
                model_pred_direction TEXT DEFAULT '',
                model_confidence REAL DEFAULT 0.0,
                model_weight REAL DEFAULT 1.0,
                run_time_ms INTEGER DEFAULT 0,
                raw_output_json TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (run_id) REFERENCES prediction_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_mo_run ON prediction_model_outputs(run_id);

            CREATE TABLE IF NOT EXISTS prediction_sentiment_evals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                source TEXT DEFAULT 'llm',
                events_text TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                adjustment_pct REAL DEFAULT 0.0,
                prompt TEXT DEFAULT '',
                response TEXT DEFAULT '',
                latency_ms INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (run_id) REFERENCES prediction_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_se_run ON prediction_sentiment_evals(run_id);

            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                run_at TEXT DEFAULT (datetime('now', 'localtime')),
                window_days INTEGER DEFAULT 120,
                horizon_days INTEGER DEFAULT 5,
                models_tested INTEGER DEFAULT 0,
                direction_accuracy_pct REAL DEFAULT 0.0,
                llm_adjustment_win_pct REAL,
                model_hits_json TEXT DEFAULT '{}',
                samples_json TEXT DEFAULT '[]',
                source TEXT DEFAULT 'live'
            );
            CREATE INDEX IF NOT EXISTS idx_bt_symbol ON backtest_results(symbol, run_at DESC);

            CREATE TABLE IF NOT EXISTS prediction_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                report_kind TEXT DEFAULT 'detail',
                dashboard_md TEXT DEFAULT '',
                detail_md TEXT DEFAULT '',
                model_used TEXT DEFAULT '',
                tokens_used INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (run_id) REFERENCES prediction_runs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_pr_run ON prediction_reports(run_id);
            CREATE INDEX IF NOT EXISTS idx_pr_symbol ON prediction_reports(symbol, created_at DESC);

            CREATE TABLE IF NOT EXISTS backtest_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backtest_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                dashboard_md TEXT DEFAULT '',
                detail_md TEXT DEFAULT '',
                model_used TEXT DEFAULT '',
                tokens_used INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (backtest_id) REFERENCES backtest_results(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_br_bt ON backtest_reports(backtest_id);
            CREATE INDEX IF NOT EXISTS idx_br_symbol ON backtest_reports(symbol, created_at DESC);
        """)
        conn.commit()
    finally:
        conn.close()


_ensure_tables()


# ── 写入辅助 ────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    c = _sqlite3.connect(_HISTORY_DB)
    c.row_factory = _sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        raise
    finally:
        c.close()


def _jsonb(d: Any) -> str:
    return json.dumps(d or {}, ensure_ascii=False, default=str)


def _safe_write(fn):
    """装饰器: 写库失败只 log, 不向外抛 — 埋点永远不能阻塞主流程。"""

    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.warning(f"prediction_traces 写入失败 ({fn.__name__}): {e}")
            return None

    return wrapper


# ── 写入入口 ────────────────────────────────────────────────────────────

@_safe_write
def start_run(
    symbol: str,
    stock_name: str,
    last_close: float,
    last_date: str,
    target_date: str,
    pred_days: int,
    task_id: str = "",
) -> int:
    """开一次预测, 建 prediction_runs 主行, 返回 run_id。"""
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO prediction_runs
               (symbol, stock_name, last_close, last_date, target_date, pred_days, task_id)
               VALUES (?,?,?,?,?,?,?)""",
            (symbol, stock_name, last_close, last_date, target_date, pred_days, task_id),
        )
        return int(cur.lastrowid or 0)


@_safe_write
def record_model_output(
    run_id: int,
    model_name: str,
    pred_close: float | None,
    pred_direction: str,
    confidence: float,
    weight: float,
    run_time_ms: int,
    raw_output: Any = None,
) -> int:
    """记录单个模型的输出 + 耗时 + 原始结构化数据。"""
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO prediction_model_outputs
               (run_id, model_name, model_pred_close, model_pred_direction,
                model_confidence, model_weight, run_time_ms, raw_output_json)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                run_id,
                model_name,
                pred_close,
                pred_direction,
                confidence,
                weight,
                run_time_ms,
                _jsonb(raw_output),
            ),
        )
        return int(cur.lastrowid or 0)


@_safe_write
def record_sentiment_eval(
    run_id: int,
    source: str,
    events_text: str,
    score: int,
    reason: str,
    adjustment_pct: float,
    prompt: str,
    response: str,
    latency_ms: int,
    error: str = "",
) -> int:
    """记录一次 LLM 情绪打分 (含 prompt + raw response + 耗时)。"""
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO prediction_sentiment_evals
               (run_id, source, events_text, score, reason, adjustment_pct,
                prompt, response, latency_ms, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (run_id, source, events_text, score, reason, adjustment_pct,
             prompt, response, latency_ms, error),
        )
        return int(cur.lastrowid or 0)


@_safe_write
def finalize_run(
    run_id: int,
    final_direction: str,
    final_expected_pct: float,
    final_target_price: float,
    final_stop_loss: float,
    models_summary: dict,
    sentiment_adj_total: float,
    capital_score: float = 0.0,
) -> None:
    """预测出最终结论后, 落最终态 + 各模型贡献汇总 + 回算各模型置信度/权重。

    capital_score: 资金面评分(-1~+1), 参与权重回算。
    """
    with _conn() as c:
        c.execute(
            """UPDATE prediction_runs SET
                final_direction=?, final_expected_pct=?, final_target_price=?,
                final_stop_loss=?, models_summary=?, sentiment_adj_total=?
               WHERE id=?""",
            (final_direction, final_expected_pct, final_target_price,
             final_stop_loss, _jsonb(models_summary), sentiment_adj_total, run_id),
        )
        # 回算各模型置信度/权重: 与最终投票方向一致的模型权重高, 否则低
        # 资金面偏多 → 一致模型权重加成; 偏空 → 降权
        cap_bonus = 1.0 + max(0.0, capital_score) * 0.3   # 偏多最多+30%
        cap_penalty = 1.0 - max(0.0, -capital_score) * 0.3  # 偏空最多-30%
        try:
            rows = c.execute(
                "SELECT id, model_pred_direction FROM prediction_model_outputs WHERE run_id=?",
                (run_id,),
            ).fetchall()
            n_total = len(rows)
            n_consistent = sum(1 for r in rows if (r[1] or "neutral") == final_direction)
            for r in rows:
                consistent = (r[1] or "neutral") == final_direction
                # 置信度: 方向一致 0.8, 否则 0.3; 全部一致时降至 0.6(避免过度自信)
                if n_total > 0 and n_consistent == n_total:
                    conf = 0.6
                else:
                    conf = 0.8 if consistent else 0.3
                # 权重: 方向一致模型均分(至少 0.1), 不一致 0.1
                if consistent:
                    weight = max(0.1, 1.0 / n_consistent) if n_consistent > 0 else 0.1
                    # 资金面加成/惩罚
                    weight = weight * (cap_bonus if capital_score > 0 else cap_penalty)
                    weight = round(min(1.0, max(0.05, weight)), 3)
                else:
                    weight = 0.1
                c.execute(
                    "UPDATE prediction_model_outputs SET model_confidence=?, model_weight=? WHERE id=?",
                    (conf, weight, r[0]),
                )
        except Exception as e:
            print(f"[finalize_run] 回算置信度/权重失败: {e}")


# ── 读取 ────────────────────────────────────────────────────────────────

def get_run(run_id: int) -> dict | None:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    row = conn.execute("SELECT * FROM prediction_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_model_outputs(run_id: int) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM prediction_model_outputs WHERE run_id=? ORDER BY id",
        (run_id,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["raw_output_json"] = json.loads(d["raw_output_json"])
        except Exception:
            pass
        out.append(d)
    return out


def list_sentiment_evals(run_id: int) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM prediction_sentiment_evals WHERE run_id=? ORDER BY id",
        (run_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_runs_for_symbol(symbol: str, limit: int = 50) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM prediction_runs WHERE symbol=? ORDER BY id DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_backtest_result(
    symbol: str,
    window_days: int,
    horizon_days: int,
    models_tested: int,
    direction_accuracy_pct: float,
    llm_adjustment_win_pct: float | None,
    model_hits: dict,
    samples: list,
    source: str = "live",
) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO backtest_results
               (symbol, window_days, horizon_days, models_tested,
                direction_accuracy_pct, llm_adjustment_win_pct,
                model_hits_json, samples_json, source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                symbol, window_days, horizon_days, models_tested,
                direction_accuracy_pct, llm_adjustment_win_pct,
                _jsonb(model_hits), _jsonb(samples), source,
            ),
        )
        return int(cur.lastrowid or 0)


def get_backtest(backtest_id: int) -> dict | None:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    row = conn.execute("SELECT * FROM backtest_results WHERE id=?", (backtest_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for k in ("model_hits_json", "samples_json"):
        try:
            d[k] = json.loads(d[k])
        except Exception:
            pass
    return d


def list_backtests_for_symbol(symbol: str, limit: int = 20) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM backtest_results WHERE symbol=? ORDER BY id DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("model_hits_json", "samples_json"):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
        out.append(d)
    return out


# ── 报告 ────────────────────────────────────────────────────────────────

def save_prediction_report(
    run_id: int,
    symbol: str,
    report_kind: str,
    dashboard_md: str,
    detail_md: str,
    model_used: str,
    tokens_used: int,
    status: str = "success",
    error: str = "",
) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO prediction_reports
               (run_id, symbol, report_kind, dashboard_md, detail_md,
                model_used, tokens_used, status, error)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, symbol, report_kind, dashboard_md, detail_md,
             model_used, tokens_used, status, error),
        )
        return int(cur.lastrowid or 0)


def get_prediction_report(report_id: int) -> dict | None:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    row = conn.execute(
        "SELECT * FROM prediction_reports WHERE id=?", (report_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_reports_for_symbol(symbol: str, limit: int = 20) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM prediction_reports WHERE symbol=? ORDER BY id DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_backtest_report(
    backtest_id: int,
    symbol: str,
    dashboard_md: str,
    detail_md: str,
    model_used: str,
    tokens_used: int,
    status: str = "success",
    error: str = "",
) -> int:
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO backtest_reports
               (backtest_id, symbol, dashboard_md, detail_md,
                model_used, tokens_used, status, error)
               VALUES (?,?,?,?,?,?,?,?)""",
            (backtest_id, symbol, dashboard_md, detail_md,
             model_used, tokens_used, status, error),
        )
        return int(cur.lastrowid or 0)


def get_backtest_report(report_id: int) -> dict | None:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    row = conn.execute(
        "SELECT * FROM backtest_reports WHERE id=?", (report_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_backtest_reports_for_symbol(symbol: str, limit: int = 20) -> list[dict]:
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM backtest_reports WHERE symbol=? ORDER BY id DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 墙上钟 ──────────────────────────────────────────────────────────────

class Timer:
    """毫秒级计时器, 用法: with Timer() as t: ... ; t.ms"""

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *a):
        self.ms = int((time.monotonic() - self._t0) * 1000)

    @property
    def ms(self) -> int:
        return getattr(self, "_ms", 0)

    @ms.setter
    def ms(self, v: int):
        self._ms = v


# ── 模型包装器 ──────────────────────────────────────────────────────────
# 每个模型 *_predict() 返回值结构各异, 这里按启发式提取:
# - 末个预测点的 close 作为 pred_close
# - 方向: pred_close > last_close → up, 否则 down
# - confidence: 后面被 algorithm 层覆盖(从 raw_output_json 读)

def _pick_pred_close(model_result: Any, last_close: float) -> float | None:
    """从 4 个模型返回结果中尽量稳定地挑出「末个预测点」。

    4 个模型现状:
    - kronos_predict: list[dict] 每项 {date, open, close, high, low, mean, ...}
    - xgboost_predict: list[float] 长度 = pred_len
    - linreg_predict: 同 xgboost
    - lag_llama_predict: list[dict] 每项 {date, close, ...}
    """
    if model_result is None:
        return None
    if isinstance(model_result, list) and model_result:
        last = model_result[-1]
        if isinstance(last, dict):
            for k in ("close", "mean", "predicted_close", "pred"):
                if k in last and last[k] is not None:
                    try:
                        return float(last[k])
                    except Exception:
                        pass
        elif isinstance(last, (int, float)):
            return float(last)
    if isinstance(model_result, dict):
        # 优先取 median 序列末值(Kronos/Lag-Llama 返回 {"median":[...]})
        if "median" in model_result and isinstance(model_result["median"], list) and model_result["median"]:
            m = model_result["median"][-1]
            if m is not None:
                try:
                    return float(m)
                except Exception:
                    pass
        if "mean" in model_result and isinstance(model_result["mean"], (int, float)):
            return float(model_result["mean"])
        if "pred" in model_result:
            p = model_result["pred"]
            if isinstance(p, list) and p:
                return _pick_pred_close(p, last_close)
    return None


def run_model_with_trace(
    run_id: int,
    model_name: str,
    model_fn,
    *args,
    weight: float = 1.0,
    last_close: float = 0.0,
    **kwargs,
) -> Any:
    """调模型 + 计时 + 写库; 返回原模型结果(不改变调用方语义)。"""
    from forecast_traces import Timer, record_model_output
    with Timer() as t:
        try:
            result = model_fn(*args, **kwargs)
        except Exception as e:
            record_model_output(
                run_id, model_name, None, "error", 0.0, weight, t.ms,
                {"error": str(e)},
            )
            raise
    pred_close = _pick_pred_close(result, last_close)
    # sanity clip: 预测价偏离基准超过 ±40% 视为模型异常(如 Lag-Llama 外推爆炸), 截断
    if pred_close is not None and last_close:
        lo, hi = last_close * 0.6, last_close * 1.4
        if pred_close < lo or pred_close > hi:
            pred_close = max(lo, min(hi, pred_close))
    direction = "up" if pred_close is not None and last_close and pred_close > last_close else (
        "down" if pred_close is not None and last_close and pred_close < last_close else "neutral"
    )
    record_model_output(
        run_id, model_name, pred_close, direction, 0.0, weight, t.ms,
        {"result_excerpt": (result[:2] if isinstance(result, list) else result) if result else None},
    )
    return result
