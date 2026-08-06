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

    # Kronos MC
    _log(tid, "Kronos 模型推理中(MC 30 采样,约 20-30s)...")
    kronos = kronos_predict(df, pred_len=days)
    _log(tid, "Kronos 完成")

    # XGBoost
    _log(tid, "XGBoost 训练预测中...")
    xgb_preds = xgboost_predict(df, pred_len=days)
    _log(tid, "XGBoost 完成")

    # 线性回归
    _log(tid, "线性回归趋势外推中...")
    reg_preds = linreg_predict(df, pred_len=days)
    _log(tid, "线性回归完成")

    # Lag-Llama(第4模型,时序基础模型)
    _log(tid, "Lag-Llama 推理中(首次加载约30-60s)...")
    lag = lag_llama_predict(df, pred_len=days)
    if lag:
        _log(tid, "Lag-Llama 完成")
    else:
        _log(tid, "Lag-Llama 不可用(跳过,用3模型投票)")

    # 消息情绪面(黑天鹅/公告/板块共振修正)
    _log(tid, "拉取消息情绪面(公告/新闻/板块共振)...")
    sentiment = fetch_sentiment(symbol)
    _log(tid, f"情绪面: 事件{len(sentiment['events'])}条, 修正系数 {sentiment['adjustment_pct']:+.2f}%")

    # 投票(取中位数,含权重)
    votes = []
    if kronos:
        votes.append(np.array(kronos["median"]))
    # Lag-Llama 参与投票但只取前 2 天(实测 3 天以上外推区间爆炸,仅短周期可靠)
    if lag:
        lag_med = np.array(lag["median"])
        lag_vote = lag_med.copy()
        if len(lag_vote) > 2 and kronos:
            kronos_med = np.array(kronos["median"])
            for i in range(2, len(lag_vote)):
                lag_vote[i] = kronos_med[i] if i < len(kronos_med) else lag_med[i]
        votes.append(lag_vote)
    if xgb_preds:
        votes.append(np.array(xgb_preds))
    if reg_preds:
        votes.append(np.array(reg_preds))
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
    rec = build_recommendation(
        symbol, last_close, final, direction,
        round((float(final[-1]) / last_close - 1) * 100, 2),
        kronos, lag, sentiment,
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
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }
    # 保存历史(供回查列表)
    rec["sentiment_adj"] = adjust_pct
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
def backtest(symbol: str):
    """回测: 用过去数据模拟预测 vs 实际,给出方向命中率。"""
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(400, "symbol 需为 6 位 A 股代码")

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
    return {
        "symbol": symbol,
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
