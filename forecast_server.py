#!/usr/bin/env python3
"""A股预测引擎服务 (:8010) — 主入口

拆分后模块:
- forecast_lib/forecast_models.py    模型层(Kronos/Lag-Llama/XGBoost/回归)
- forecast_lib/forecast_history.py   历史存储(SQLite)
- forecast_lib/forecast_sentiment.py 情绪面(LLM+公告+板块)
- forecast_lib/forecast_utils.py     工具(任务/推荐)

启动: python3 forecast_server.py (systemd: panwatch-forecast)
"""
import os
import sys
import io
import json
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "forecast_lib"))

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

# 模块导入
from forecast_models import (
    get_predictor, load_kline, kronos_predict,
    xgboost_predict, linreg_predict, lag_llama_predict,
)
from forecast_history import (
    get_stock_name, save_forecast, list_forecasts,
)
from forecast_sentiment import (
    fetch_sentiment, _load_llm_config,
)
from forecast_traces import (
    start_run, record_model_output, record_sentiment_eval,
    finalize_run, run_model_with_trace, _pick_pred_close,
    list_runs_for_symbol, list_model_outputs, list_sentiment_evals,
    save_backtest_result,
    save_prediction_report, get_prediction_report, list_reports_for_symbol,
    get_backtest, list_backtests_for_symbol,
)
from forecast_reports import generate_report, generate_backtest_report, generate_wecom_report
from forecast_utils import (
    _log, _set_status, new_task, build_recommendation,
)

app = FastAPI(title="A股预测引擎", version="0.3.0")


@app.get("/health")
def health():
    return {"status": "ok", "kronos_ready": get_predictor() is not None, "time": datetime.now().isoformat()}


@app.get("/predict")
def predict(symbol: str, days: int = 5, task_id: str = "", target_date: str = ""):
    """多模型预测: Kronos + Lag-Llama + XGBoost + 线性回归 投票。

    target_date 可选: 预测到该日期为止(自动换算交易日数)。task_id 可选(进度日志)。
    """
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(400, "symbol 需为 6 位 A 股代码")

    tid = task_id or new_task()
    if tid not in _tasks_placeholder():
        from forecast_utils import _tasks
        with _tasks_lock_placeholder():
            _tasks[tid] = {"status": "pending", "logs": [], "result": None}
    _set_status(tid, "running")
    t0 = time.monotonic()
    try:
        _log(tid, f"开始预测 {symbol}")
        df = load_kline(symbol, days=250)
        _log(tid, f"数据加载完成: {len(df)} 根K线 (baostock 不复权)")
        stock_name = get_stock_name(symbol)
        if stock_name:
            _log(tid, f"股票: {symbol} {stock_name}")
    except HTTPException as e:
        _set_status(tid, "error")
        _log(tid, f"数据加载失败: {e.detail}")
        raise
    except Exception as e:
        _set_status(tid, "error")
        _log(tid, f"数据加载失败: {e}")
        raise HTTPException(502, f"数据获取失败: {e}")

    last_close = float(df["close"].iloc[-1])
    last_date = str(df["timestamp"].iloc[-1].date())

    # 目标日期: 传入 target_date 则计算天数,否则用 days
    target_dt = None
    if target_date:
        try:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        except Exception:
            raise HTTPException(400, "target_date 格式应为 YYYY-MM-DD")
    if target_dt:
        n = 0
        cur = df["timestamp"].iloc[-1]
        while cur.date() < target_dt.date() and n < 20:
            cur += timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        days = max(1, n)
        _log(tid, f"目标日期 {target_date} → 预测 {days} 个交易日")
    else:
        _log(tid, f"预测未来 {days} 个交易日")

    # 开预测 run (拉链串联后续每步记录; target_date_str 算出来时 finalize_run 再填补)
    run_id = start_run(
        symbol, stock_name, last_close, last_date, "", days, task_id=tid
    ) or 0

    # Kronos MC
    _log(tid, "Kronos 模型推理中(MC 30 采样,约 20-30s)...")
    kronos = run_model_with_trace(run_id, "kronos", kronos_predict, df, pred_len=days, last_close=last_close) if run_id else kronos_predict(df, pred_len=days)
    _log(tid, "Kronos 完成")

    # XGBoost
    _log(tid, "XGBoost 训练预测中...")
    xgb_preds = run_model_with_trace(run_id, "xgboost", xgboost_predict, df, pred_len=days, last_close=last_close) if run_id else xgboost_predict(df, pred_len=days)
    _log(tid, "XGBoost 完成")

    # 线性回归
    _log(tid, "线性回归趋势外推中...")
    reg_preds = run_model_with_trace(run_id, "linear_reg", linreg_predict, df, pred_len=days, last_close=last_close) if run_id else linreg_predict(df, pred_len=days)
    _log(tid, "线性回归完成")

    # Lag-Llama(第4模型,时序基础模型)
    _log(tid, "Lag-Llama 推理中(首次加载约30-60s)...")
    lag = run_model_with_trace(run_id, "lag_llama", lag_llama_predict, df, pred_len=days, last_close=last_close) if run_id else lag_llama_predict(df, pred_len=days)
    if lag:
        _log(tid, "Lag-Llama 完成")
    else:
        _log(tid, "Lag-Llama 不可用(跳过,用3模型投票)")

    # 消息情绪面(黑天鹅/公告/板块共振修正)
    _log(tid, "拉取消息情绪面(公告/新闻/板块共振)...")
    sentiment = fetch_sentiment(symbol, _run_id=run_id)
    _log(tid, f"情绪面: 事件{len(sentiment['events'])}条, 修正系数 {sentiment['adjustment_pct']:+.2f}%")

    # 主力资金面(近 5 日净流入, zhitu MCP 桥接)
    _log(tid, "拉取主力资金流(zhitu)...)")
    try:
        from zhitu_bridge import fetch_capital_flow
        capital_flow = fetch_capital_flow(symbol, days=5)
        if capital_flow:
            _log(tid, f"资金面: 近{len(capital_flow)}日主力净流入 {[r['main_net'] for r in capital_flow]}")
        else:
            _log(tid, "资金面: 拉取失败, 跳过")
            capital_flow = []
    except Exception as e:
        _log(tid, f"资金面拉取异常: {e}")
        capital_flow = []

    # 投票(取中位数,含权重)
    # sanity clip: 单模型预测偏离基准 >±40% 视为异常, 截断(防 Lag-Llama 外推爆炸污染)
    def _clip_arr(arr, base, lo=0.6, hi=1.4):
        a = np.array(arr, dtype=float)
        return np.clip(a, base * lo, base * hi)

    votes = []
    # Kronos 仅返回前 2 天(pred_len>2 时被模型截断), 用其均值回填后续天, 保证维度对齐
    if kronos:
        k_med = _clip_arr(kronos["median"], last_close)
        if len(k_med) < days:
            fill = k_med.mean() if len(k_med) else last_close
            k_med = np.concatenate([k_med, np.full(days - len(k_med), fill)])
        votes.append(k_med)
    # Lag-Llama 参与投票但只取前 2 天(实测 3 天以上外推区间爆炸,仅短周期可靠)
    if lag:
        lag_med = _clip_arr(lag["median"], last_close)
        lag_vote = lag_med.copy()
        if len(lag_vote) > 2 and kronos:
            kronos_med = _clip_arr(kronos["median"], last_close)
            for i in range(2, len(lag_vote)):
                lag_vote[i] = kronos_med[i] if i < len(kronos_med) else lag_med[i]
        if len(lag_vote) < days:
            fill = lag_vote.mean() if len(lag_vote) else last_close
            lag_vote = np.concatenate([lag_vote, np.full(days - len(lag_vote), fill)])
        votes.append(lag_vote)
    if xgb_preds:
        votes.append(_clip_arr(xgb_preds, last_close))
    if reg_preds:
        votes.append(_clip_arr(reg_preds, last_close))
    if not votes:
        _set_status(tid, "error")
        _log(tid, "所有模型预测失败")
        raise HTTPException(502, "所有模型预测失败")

    final = np.median(np.array(votes), axis=0)

    # 应用情绪面修正系数(±0.5~1.5%)
    adjust_pct = sentiment["adjustment_pct"]
    if adjust_pct != 0:
        final = final * (1 + adjust_pct / 100)
        _log(tid, f"应用情绪修正 {adjust_pct:+.2f}%")

    direction = "up" if final[-1] > last_close else "down" if final[-1] < last_close else "flat"

    # 生成操作建议
    from forecast_utils import calc_capital_score
    capital_score = calc_capital_score(capital_flow, last_close)
    rec = build_recommendation(
        symbol, last_close, final, direction,
        round((float(final[-1]) / last_close - 1) * 100, 2),
        kronos, lag, sentiment,
        capital_score=capital_score,
    )

    # 计算预测目标日期(last_date 往后 days 个交易日)
    pred_dates = []
    cur_d = df["timestamp"].iloc[-1]
    while len(pred_dates) < days:
        cur_d += timedelta(days=1)
        if cur_d.weekday() < 5:
            pred_dates.append(str(cur_d.date()))
    target_date_str = pred_dates[-1] if pred_dates else last_date

    result = {
        "symbol": symbol,
        "stock_name": stock_name,
        "last_close": last_close,
        "last_date": last_date,
        "target_date": target_date_str,
        "pred_dates": pred_dates,
        "pred_days": days,
        "prediction": [round(float(x), 2) for x in final],
        "direction": direction,
        "expected_pct": round((float(final[-1]) / last_close - 1) * 100, 2),
        "recommendation": rec,
        "models": {
            "kronos": kronos,
            "lag_llama": lag,
            "xgboost": xgb_preds,
            "linreg": reg_preds,
        },
        "sentiment": {
            "events": sentiment["events"][:8],
            "market_sentiment": sentiment["market_sentiment"],
            "adjustment_pct": adjust_pct,
            "notes": sentiment["notes"],
        },
        "capital_flow": capital_flow,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }
    # 保存历史(供回查列表)
    rec["sentiment_adj"] = adjust_pct
    rec["sentiment_notes"] = json.dumps(sentiment.get("notes", []), ensure_ascii=False)
    rec["capital_flow"] = capital_flow
    rec["symbol"] = symbol
    rec["stock_name"] = stock_name
    rec["last_close"] = last_close
    rec["last_date"] = last_date
    rec["target_date"] = target_date_str
    rec["pred_days"] = days
    rec["direction"] = direction
    rec["expected_pct"] = result["expected_pct"]
    rec["prediction"] = result["prediction"]
    rec["models"] = result["models"]
    save_forecast(rec)

    # ⑥ 7-finalize: 落最终态到 prediction_runs, 包含 target_date + 4 模型贡献汇总
    if run_id:
        # 测算每个模型的末个预测点, 作为该模型贡献度
        from forecast_models import json as _j  # noqa  单纯 import 校验
        models_summary = {}
        for _name, _pred in [
            ("kronos", kronos), ("xgboost", xgb_preds),
            ("linear_reg", reg_preds), ("lag_llama", lag),
        ]:
            try:
                if _pred is None:
                    continue
                if isinstance(_pred, list) and _pred:
                    last = _pred[-1]
                    if isinstance(last, dict):
                        for k in ("close", "mean"):
                            if k in last and last[k] is not None:
                                models_summary[_name] = float(last[k])
                                break
                    elif isinstance(last, (int, float)):
                        models_summary[_name] = float(last)
            except Exception:
                pass
        rec_summary = rec.get("recommendation") or {}
        tprice = float(rec_summary.get("target_price") or 0) or None
        sloss = float(rec_summary.get("stop_loss") or 0) or None
        finalize_run(
            run_id,
            final_direction=direction,
            final_expected_pct=result["expected_pct"],
            final_target_price=tprice,
            final_stop_loss=sloss,
            models_summary=models_summary,
            sentiment_adj_total=adjust_pct,
            capital_score=capital_score,
        )
        # 顺手把 target_date 也补上 (start_run 时还未知)
        import sqlite3 as _sq
        try:
            _c = _sq.connect(os.path.expanduser("~/.panwatch_forecast.db"))
            _c.execute(
                "UPDATE prediction_runs SET target_date=? WHERE id=? AND target_date=?",
                (target_date_str, run_id, ""),
            )
            _c.commit()
            _c.close()
        except Exception:
            pass

    _log(tid, f"预测完成: {last_close} → {result['prediction'][-1]} ({result['expected_pct']:+.1f}%), 耗时 {result['elapsed_ms']}ms")
    _set_status(tid, "done")
    from forecast_utils import _tasks
    with _tasks_lock_placeholder():
        _tasks[tid]["result"] = result
    return result


def _tasks_placeholder():
    from forecast_utils import _tasks
    return _tasks


def _tasks_lock_placeholder():
    from forecast_utils import _tasks_lock
    return _tasks_lock


@app.get("/predict/status")
def predict_status(task_id: str):
    """查询任务进度与日志。"""
    from forecast_utils import _tasks
    with _tasks_lock_placeholder():
        t = _tasks.get(task_id)
        if not t:
            return {"status": "not_found", "logs": []}
        return {
            "status": t["status"],
            "logs": t["logs"],
            "result": t["result"],
        }


@app.get("/backtest")
def backtest(symbol: str, force_legacy: bool = False):
    """回测: 用历史预测推算模型 + LLM 修正的准确率, 并与实际行情对照。

    数据源: prediction_runs 表里该 symbol 的所有历史 run + K 线实际数据。
    算指标: 4 个模型各自命中率 / LLM 修正胜率 / 加权汇总命中率。
    退路: 若历史 run < 3 个 或 force_legacy=True, 跑旧 LinearRegression 滚动回测。
    """
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(400, "symbol 需为 6 位 A 股代码")

    # 1. 先尝试历史 run 回测
    if not force_legacy:
        runs = list_runs_for_symbol(symbol, limit=50)
        # 过滤有完整最终结论的 run
        valid_runs = [r for r in runs if r.get("final_direction") and r.get("last_close") and r.get("target_date")]

        if len(valid_runs) >= 3:
            try:
                df = load_kline(symbol, days=400)
            except Exception as e:
                raise HTTPException(502, f"数据获取失败: {e}")

            # 建 date -> close 索引
            price_idx = {str(df["timestamp"].iloc[i].date()): float(df["close"].iloc[i])
                         for i in range(len(df))}

            # 算每个 run 的最终结果 (拿每个模型的实际 hit)
            samples = []
            model_hits = {}  # model_name -> [hit bool*]
            llm_correct = []
            llm_wrong = []

            from datetime import datetime as _dt
            for r in valid_runs:
                target_date = r["target_date"]
                if not target_date or target_date not in price_idx:
                    continue
                actual_close = price_idx[target_date]
                base_close = float(r["last_close"])
                actual_dir = "up" if actual_close > base_close else "down" if actual_close < base_close else "flat"
                actual_pct = round((actual_close / base_close - 1) * 100, 2)

                # 4 模型逐个判 hit
                mos = list_model_outputs(r["id"])
                run_models_hit = {}
                for m in mos:
                    if not m.get("model_pred_close") or not m.get("model_pred_direction"):
                        continue
                    pred_close = float(m["model_pred_close"])
                    pred_dir = m["model_pred_direction"]
                    pred_pct = (pred_close / base_close - 1) * 100
                    # hit 定义: 实际方向与预测方向一致
                    hit = (pred_dir == actual_dir) and actual_dir != "flat"
                    run_models_hit[m["model_name"]] = {
                        "pred_close": round(pred_close, 2),
                        "pred_pct": round(pred_pct, 2),
                        "pred_dir": pred_dir,
                        "hit": hit,
                    }
                    model_hits.setdefault(m["model_name"], []).append(hit)

                # LLM 修正胜率: 4 模型投票 vs 加上 LLM 修正后 vs 实际
                evals = list_sentiment_evals(r["id"])
                llm_adj = sum(float(e["adjustment_pct"]) for e in evals)
                base_dir = r["final_direction"]
                # 假设 LLM 修正 0 是 (4 模型投票方向), 修正后是 base_dir
                # 这里只统计: LLM 正向修正 vs 实际; LLM 负向修正 vs 实际
                if llm_adj != 0 and base_dir != actual_dir:
                    # LLM 修正后判错
                    if llm_adj > 0:
                        llm_wrong.append(abs(actual_pct))
                    else:
                        llm_wrong.append(abs(actual_pct))
                samples.append({
                    "run_id": r["id"],
                    "target_date": target_date,
                    "pred_close": actual_close,  # 实际成交价
                    "actual_close": actual_close,
                    "actual_pct": actual_pct,
                    "actual_dir": actual_dir,
                    "final_pred_dir": base_dir,
                    "final_pred_pct": float(r["final_expected_pct"] or 0),
                    "models": run_models_hit,
                    "llm_adj": llm_adj,
                })

            # 聚合
            model_summary = {}
            for name, hits in model_hits.items():
                if not hits:
                    continue
                wins = sum(1 for h in hits if h)
                model_summary[name] = {
                    "samples": len(hits),
                    "hits": wins,
                    "accuracy_pct": round(wins / len(hits) * 100, 1),
                }

            total = len(model_summary.get("kronos", {}).get("hits", 0) and [True]) or 0
            # 聚合整体命中率 (用 4 模型任一命中 OR 加权平均)
            if "kronos" in model_summary:
                total = model_summary["kronos"]["samples"]
                hits = sum(1 for s in samples if any(m.get("hit") for m in s.get("models", {}).values()))

            accuracy = round(hits / total * 100, 1) if total else 0
            llm_win_pct = None
            if llm_correct or llm_wrong:
                llm_win_pct = round(len(llm_correct) / (len(llm_correct) + len(llm_wrong)) * 100, 1)

            # 保存到 backtest_results 表
            save_backtest_result(
                symbol=symbol, window_days=400, horizon_days=5,
                models_tested=len(model_summary),
                direction_accuracy_pct=accuracy,
                llm_adjustment_win_pct=llm_win_pct,
                model_hits=model_summary,
                samples=samples[-10:],
                source="runs",
            )

            return {
                "symbol": symbol,
                "source": "historical_runs",
                "runs_used": len(samples),
                "models": model_summary,
                "direction_hits": hits,
                "direction_accuracy_pct": accuracy,
                "llm_adjustment_win_pct": llm_win_pct,
                "recent_samples": samples[-10:],
            }

    # 2. 退路: 旧 LinearRegression 滚动回测
    try:
        df = load_kline(symbol, days=400)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"数据获取失败: {e}")

    window, horizon = 120, 5
    hits, total = 0, 0
    samples = []
    closes = df["close"].values

    for start in range(window, len(closes) - horizon, 5):
        hist = df.iloc[start - window:start]
        actual_future = closes[start:start + horizon]
        if len(actual_future) < horizon:
            continue
        try:
            from sklearn.linear_model import LinearRegression
            X = np.arange(len(hist)).reshape(-1, 1)
            m = LinearRegression().fit(X, hist["close"].values)
            pred = m.predict([[len(hist) + horizon - 1]])[0]
            actual = actual_future[-1]
            pred_dir = 1 if pred > hist["close"].iloc[-1] else -1
            act_dir = 1 if actual > hist["close"].iloc[-1] else -1
            total += 1
            if pred_dir == act_dir:
                hits += 1
            samples.append({
                "date": str(df["timestamp"].iloc[start - 1].date()),
                "pred_close": round(float(pred), 2),
                "actual_close": round(float(actual), 2),
                "hit": pred_dir == act_dir,
            })
        except Exception:
            continue

    accuracy = round(hits / total * 100, 1) if total else 0
    save_backtest_result(
        symbol=symbol, window_days=window, horizon_days=horizon,
        models_tested=1, direction_accuracy_pct=accuracy,
        llm_adjustment_win_pct=None,
        model_hits={"linear_reg": {"samples": total, "hits": hits, "accuracy_pct": accuracy}},
        samples=samples[-10:], source="legacy",
    )
    return {
        "symbol": symbol,
        "source": "legacy",
        "windows_tested": total,
        "direction_hits": hits,
        "direction_accuracy_pct": accuracy,
        "recent_samples": samples[-10:],
    }


@app.get("/forecast/history")
def history(symbol: str = "", limit: int = 50):
    """历史预测列表(供回查)。"""
    return {"items": list_forecasts(limit=min(limit, 200), symbol=symbol)}


@app.get("/forecast/models")
def forecast_models():
    """预测引擎模型清单(设置页展示)。"""
    cfg = _load_llm_config()
    kronos_root = os.path.expanduser("~/Kronos")
    lag_ckpt = os.path.expanduser(
        "~/.cache/huggingface/models--time-series-foundation-models--Lag-Llama/"
        "snapshots/72dcfc29da106acfe38250a60f4ae29d1e56a3d9/lag-llama.ckpt"
    )
    env_path = os.path.expanduser("~/.panwatch_forecast.env")

    return {
        "models": [
            {"name": "Kronos", "module": "预测主模型", "model_id": "NeoQuasar/Kronos-small",
             "location": kronos_root, "configurable": "本地源码路径(~/Kronos)"},
            {"name": "Lag-Llama", "module": "投票模型(短周期)", "model_id": "time-series-foundation-models/Lag-Llama",
             "location": lag_ckpt, "configurable": "checkpoint 路径(代码内)"},
            {"name": "XGBoost", "module": "投票模型", "model_id": "XGBRegressor(n_estimators=100, depth=3)",
             "location": "pip 包", "configurable": "参数在代码内"},
            {"name": "LLM情绪打分", "module": "公告/新闻语义判断", "model_id": cfg.get("model", "agnes-2.5-flash"),
             "location": cfg.get("base_url", ""), "configurable": "PanWatch 设置→AI 模型(默认模型),或 ~/.panwatch_forecast.env 的 LLM_MODEL",
             "api_key_set": bool(cfg.get("api_key"))},
            {"name": "PanWatch AI", "module": "AI对话/Agent 分析", "model_id": "AIModel 表默认",
             "location": "PanWatch 设置→AI 服务商", "configurable": "PanWatch 设置页(已有)"},
        ],
        "config_file": env_path,
        "config_file_exists": os.path.exists(env_path),
        "note": "修改 LLM 情绪打分模型: ① PanWatch 设置→AI 模型 改默认模型 ② 或编辑配置文件",
    }


@app.get("/stocks/search")
def stocks_search(q: str = "", limit: int = 10):
    """股票名称/代码搜索(baostock 全市场,主板过滤)。"""
    if not q.strip():
        return {"items": []}
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return {"items": []}
        rs = bs.query_all_stock(day=datetime.now().strftime("%Y-%m-%d"))
        results = []
        q_lower = q.strip().lower()
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if len(row) < 3:
                continue
            code_full, status, name = row[0], row[1], row[2]
            if status != "1":
                continue
            code6 = code_full.split(".")[-1]
            if not (code6.startswith(("60", "00", "002"))):
                continue
            if q_lower in name.lower() or q_lower in code6 or q_lower in code_full:
                results.append({"symbol": code6, "name": name, "market": "sh" if code_full.startswith("sh") else "sz"})
                if len(results) >= limit:
                    break
        bs.logout()
        return {"items": results}
    except Exception as e:
        return {"items": [], "error": str(e)}


@app.get("/report/generate")
def report_generate(symbol: str, task_id: str = ""):
    """生成预测报告 双格式(Dashboard + Detail)。

    要求 /predict 流程完成且任务存到 _tasks 里；如果传 task_id 取对应 result；
    如不传 task_id 则取该 symbol 最近完的一次预测(forecasts 表)。
    """
    from forecast_utils import _tasks

    result = None
    # 尝试从 _tasks 拿结果
    if task_id:
        with _tasks_lock_placeholder():
            t = _tasks.get(task_id)
            if t and t.get("result"):
                result = t["result"]

    # 如不传 task_id 或 _tasks 无，则取 forecasts 表最新
    if not result:
        rows = list_forecasts(limit=1, symbol=symbol)
        if not rows:
            raise HTTPException(404, f"无 {symbol} 的预测记录，请先调用 /predict")
        data = rows[0]
        # 重建类似 /predict 返回的 result dict
        # DB forecasts 表用独立列存 recommendation/sentiment 字段, 需映射回嵌套结构
        def _coerce_notes(obj):
            if isinstance(obj, list):
                return obj
            if isinstance(obj, str):
                try:
                    v = json.loads(obj)
                    return v if isinstance(v, list) else [str(v)]
                except Exception:
                    return [obj] if obj else []
            return []
        rec = {
            "action": data.get("action", "持有"),
            "confidence": data.get("confidence", "中"),
            "target_price": data.get("target_price"),
            "stop_loss": data.get("stop_loss"),
            "summary": data.get("summary", ""),
        }
        sentiment = {
            "adjustment_pct": data.get("sentiment_adj", 0),
            "market_sentiment": "中性",
            "notes": _coerce_notes(data.get("sentiment_notes", "[]")),
        }
        result = {
            "symbol": data["symbol"],
            "stock_name": data.get("stock_name", ""),
            "last_close": data["last_close"],
            "last_date": data.get("last_date", ""),
            "target_date": data.get("target_date", ""),
            "pred_days": data.get("pred_days", 5),
            "prediction": data.get("prediction", []),
            "direction": data.get("direction", "flat"),
            "expected_pct": data.get("expected_pct", 0),
            "recommendation": rec,
            "models": data.get("models", {}),
            "sentiment": sentiment,
            "capital_flow": _coerce_notes(data.get("capital_flow", "[]")),
            "elapsed_ms": data.get("elapsed_ms", 0),
        }

    # 查对应 run_id(仅取 prediction_runs 最新的 symbol)
    run_id = 0
    runs = list_runs_for_symbol(symbol, limit=1)
    if runs:
        run_id = runs[0].get("id", 0)

    # 四模型完整输出(从 run 的 model_outputs 拿, 补进 result 供企微版展示)
    if run_id and isinstance(result, dict):
        try:
            result["model_outputs"] = list_model_outputs(run_id)
        except Exception:
            result["model_outputs"] = []

    # 取回测数据
    backtest_data = None
    if run_id:
        try:
            backtest_data = _call_backtest_internal(symbol)
        except Exception:
            pass

    # 生成
    if run_id:
        dash, detail = generate_report(run_id, result, backtest_data=backtest_data)
    else:
        # 没有 run 也用 result 兜底
        dash, detail = generate_report(0, result, backtest_data=backtest_data)

    # 保存到 prediction_reports 表
    report_id = save_prediction_report(
        run_id=run_id, symbol=symbol,
        report_kind="prediction",
        dashboard_md=dash, detail_md=detail,
        model_used=result.get("stock_name", ""),
        tokens_used=0,
        status="ok",
    ) or 0

    return {
        "report_id": report_id,
        "symbol": symbol,
        "run_id": run_id,
        "dashboard_md": dash,
        "detail_md": detail,
        "result": result,
        "backtest_data": backtest_data,
    }


@app.get("/report/backtest")
def report_backtest(symbol: str):
    """生成回测报告 双格式(Dashboard + Detail)。

    先调用内部 /backtest 逻辑拿回测数据，再生成报告。
    """
    # 调 backtest 主流程
    try:
        bt_data = _call_backtest_internal(symbol)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"回测失败: {e}")

    # 取 backtest_id(该 symbol 最新一条)
    bts = list_backtests_for_symbol(symbol, limit=1)
    bt_id = bts[0].get("id", 0) if bts else 0

    dash, detail = generate_backtest_report(bt_id, bt_data, symbol)

    return {
        "report_id": 0,
        "backtest_id": bt_id,
        "symbol": symbol,
        "dashboard_md": dash,
        "detail_md": detail,
    }


def _call_backtest_internal(symbol: str) -> dict:
    """复用 /backtest 内部逻辑，返回 dict(不经过 HTTP)。"""
    # 该函数在 backtest() 内已经返回 dict，这里直接调用 backtest(symbol)
    # 防止无限递归，我们走 backtest() 函数本身
    return backtest(symbol)


@app.get("/report/list")
def report_list(symbol: str = "", limit: int = 20):
    """列出某只股票(或全局)预测报告列表。"""
    if symbol:
        rows = list_reports_for_symbol(symbol, limit=limit)
    else:
        # 不带 symbol 时暂未实现全列表(需要 forecast_traces 加函数)
        rows = list_reports_for_symbol(symbol, limit=limit)
    # 精简输出
    result = []
    for r in rows:
        result.append({
            "id": r.get("id"),
            "run_id": r.get("run_id"),
            "symbol": r.get("symbol"),
            "report_kind": r.get("report_kind"),
            "status": r.get("status"),
            "created_at": r.get("created_at"),
            "dashboard_preview": (r.get("dashboard_md") or "")[:100],
        })
    return {"items": result, "total": len(result)}


@app.get("/report/get")
def report_get(report_id: int):
    """获取单条预测报告。"""
    r = get_prediction_report(report_id)
    if not r:
        raise HTTPException(404, f"报告 {report_id} 不存在")
    return r


def _push_to_wecom_via_hermes(text: str, event_type: str = "forecast_report") -> dict:
    """通过 Hermes webhook 中转推送文本到企微。

    容器内访问 172.17.0.1:8644 (localhost 指容器自己)。
    secret 从 /hermes/webhook_subscriptions.json 的 panwatch-notify.secret 读取。
    wecom adapter 期望 payload 含 title + body 字段。
    返回 {"ok": bool, "message": str}。
    """
    import hmac
    import hashlib
    import json as _json

    # 读取 secret
    secret = ""
    for cand in [
        os.path.expanduser("~/.hermes/webhook_subscriptions.json"),
        "/hermes/webhook_subscriptions.json",
    ]:
        try:
            with open(cand) as f:
                subs = _json.load(f)
            secret = (subs.get("panwatch-notify") or {}).get("secret", "")
            if secret:
                break
        except Exception:
            continue
    if not secret:
        return {"ok": False, "message": "未找到 panwatch-notify webhook secret"}

    # 拆 text 为 title(首行) + body(剩余)
    _lines = (text or "").split("\n", 1)
    title = _lines[0].strip()
    body_text = _lines[1].strip() if len(_lines) > 1 else ""

    payload = _json.dumps({
        "event_type": event_type,
        "title": title,
        "body": body_text,
    }, ensure_ascii=False)
    sig = "sha256=" + hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    url = "http://172.17.0.1:8644/webhooks/panwatch-notify"
    try:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=payload.encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": event_type,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            ok = resp.status == 200
            return {"ok": ok, "message": body if not ok else "delivered"}
    except Exception as e:
        return {"ok": False, "message": f"推送失败: {e}"}


def _wecom_friendly_md(md: str, max_len: int = 3500) -> str:
    """把完整 markdown 报告转成企微友好的纯文本。

    企微 markdown 不支持表格语法(| col |)、<details> 折叠、HTML 标签,
    且单条消息上限 4096 字节。这里去掉这些不兼容元素, 保留标题/列表/加粗,
    并截断到安全长度。
    """
    import re
    lines = (md or "").split("\n")
    out = []
    for ln in lines:
        s = ln.strip()
        # 跳过表格分隔行 (|---|---|) 和表头/表格数据行 (含 | 列分隔)
        if re.match(r"^\|[\s:|-]+\|$", s):
            continue
        if s.startswith("|") and s.endswith("|") and s.count("|") >= 2:
            # 把表格行转成可读列表: 去掉首尾 | 和列间 |
            cells = [c.strip() for c in s.strip("|").split("|")]
            out.append("· " + " / ".join(cells))
            continue
        # 跳过 HTML / 折叠块标签
        if s.startswith("<") and s.endswith(">"):
            continue
        # 折叠摘要块 <details>...<summary> 标题 </summary>内容</details> 已在上行被去,
        # 这里把 <summary>xxx</summary> 转成小标题
        m = re.match(r"<summary>(.*?)</summary>", s)
        if m:
            out.append(f"**{m.group(1)}**")
            continue
        out.append(ln)
    text = "\n".join(out).strip()
    # 截断(按字符, 企微限制 4096 字节, 中文 3 字节/字, 留余量)
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "\n…(报告较长, 完整版见 PanWatch)"
    return text


@app.post("/report/push")
def report_push(body: dict):
    """推送报告到企微(通过 Hermes webhook 中转)。

    重新生成企微专用版(手机友好排版), 而非复用 detail_md。

    body: {kind: "prediction"|"backtest", symbol, dashboard_md, detail_md}
    """
    kind = body.get("kind", "prediction")
    symbol = body.get("symbol", "")

    try:
        if kind == "backtest":
            # 回测报告: 用 detail_md 兜底转企微友好版
            detail_md = body.get("detail_md", "")
            dashboard_md = body.get("dashboard_md", "")
            title_line = (dashboard_md or "").split("\n", 1)[0].replace("#", "").strip()
            body_text = _wecom_friendly_md(detail_md) if detail_md else _wecom_friendly_md(dashboard_md)
            push_text = f"{title_line}\n\n{body_text}" if body_text else (title_line or "")
        else:
            # 预测报告: 重新生成企微专用版(逐行四模型 + emoji 分段)
            gen = report_generate(symbol)
            wecom_md = generate_wecom_report(gen.get("result", {}), gen.get("backtest_data"))
            push_text = wecom_md
    except Exception as e:
        # 兜底: 用传入的 detail_md 转友好版
        detail_md = body.get("detail_md", "")
        dashboard_md = body.get("dashboard_md", "")
        title_line = (dashboard_md or "").split("\n", 1)[0].replace("#", "").strip()
        body_text = _wecom_friendly_md(detail_md) if detail_md else _wecom_friendly_md(dashboard_md)
        push_text = f"{title_line}\n\n{body_text}" if body_text else (title_line or "")
        _log("report_push fallback: " + str(e))

    result = _push_to_wecom_via_hermes(push_text, event_type=f"forecast_{kind}_report")
    return result


@app.get("/forecast/card")
def forecast_card(symbol: str, task_id: str = ""):
    """生成预测结果图片卡片(PNG,可下载)。"""
    rows = list_forecasts(limit=1, symbol=symbol)
    from forecast_utils import _tasks
    if task_id:
        with _tasks_lock_placeholder():
            t = _tasks.get(task_id)
            if t and t.get("result"):
                return Response(
                    content=_render_card(t["result"]).getvalue(),
                    media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="forecast_{symbol}.png"'},
                )
    if not rows:
        raise HTTPException(404, f"无 {symbol} 的预测记录,先执行预测")
    data = rows[0]
    render = {
        "symbol": data["symbol"],
        "stock_name": data.get("stock_name", ""),
        "last_close": data["last_close"],
        "last_date": data["last_date"],
        "prediction": data["prediction"],
        "direction": data["direction"],
        "expected_pct": data["expected_pct"],
        "recommendation": {
            "action": data.get("action", ""),
            "confidence": data.get("confidence", ""),
            "summary": data.get("summary", ""),
            "target_price": data.get("target_price"),
            "stop_loss": data.get("stop_loss"),
        },
    }
    return Response(
        content=_render_card(render).getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="forecast_{symbol}.png"'},
    )


def _render_card(data: dict) -> io.BytesIO:
    """用 matplotlib 渲染预测卡片。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    for fp in ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
               "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
               "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]:
        if os.path.exists(fp):
            fm.fontManager.addfont(fp)
            plt.rcParams["font.family"] = fm.FontProperties(fname=fp).get_name()
            break
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(7, 9), dpi=130)
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    symbol = data["symbol"]
    last = data["last_close"]
    preds = data["prediction"]
    direction = data.get("direction", "up")
    exp = data.get("expected_pct", 0)
    rec = data.get("recommendation", {})
    color = "#f85149" if direction == "up" else "#3fb950" if direction == "down" else "#8b949e"
    dir_cn = {"up": "看多", "down": "看空", "flat": "横盘"}.get(direction, direction)

    name = data.get("stock_name", "") or ""
    title = f"A股预测 · {symbol}" + (f" · {name}" if name else "")
    ax.text(0.5, 0.96, title, ha="center", color="#e6edf3",
            fontsize=18, fontweight="bold", transform=ax.transAxes)
    ax.text(0.5, 0.92, f"基准 {last:.2f} ({data.get('last_date', '')}) → {dir_cn} {exp:+.1f}%",
            ha="center", color=color, fontsize=13, transform=ax.transAxes)

    xs = list(range(len(preds)))
    ax.plot(xs, preds, color=color, linewidth=2.5, marker="o", markersize=6)
    ax.axhline(last, color="#8b949e", linestyle="--", linewidth=1, alpha=0.6)
    ax.text(len(preds) - 1, last, f" 基准 {last:.2f}", color="#8b949e", fontsize=10, va="center")
    ax.set_xlabel("T+N 日", color="#8b949e")
    ax.set_ylabel("预测价格", color="#8b949e")
    ax.tick_params(colors="#8b949e")
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.grid(True, alpha=0.2, color="#30363d")

    action = rec.get("action", "")
    conf = rec.get("confidence", "")
    target = rec.get("target_price")
    stop = rec.get("stop_loss")
    ax.text(0.02, 0.05, f"操作建议: {action}", color="#e6edf3", fontsize=14,
            fontweight="bold", transform=ax.transAxes)
    ax.text(0.02, 0.015, f"置信度: {conf}  目标: {target}  止损参考: {stop}",
            color="#8b949e", fontsize=11, transform=ax.transAxes)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8010)
