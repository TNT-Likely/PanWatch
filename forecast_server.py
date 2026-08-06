#!/usr/bin/env python3
"""A股预测回测引擎服务 (:8010)

独立于 PanWatch 容器的 FastAPI 服务,复用 ~/Kronos + baostock 数据,
提供:
- GET /health                    健康检查
- GET /predict?symbol=002361     多模型预测(Kronos MC + XGBoost + 回归)
- GET /backtest?symbol=002361    历史预测命中率

启动: python3 forecast_server.py  (或 nohup, 见 deploy 脚本)
"""
import os
import sys
import json
import time
import uuid
import io
import threading
from datetime import datetime, timedelta

# Kronos 路径
KRONOS_ROOT = os.path.expanduser('~/Kronos')
KRONOS_MODEL_PATH = os.path.join(KRONOS_ROOT, 'model')
if os.path.isdir(KRONOS_MODEL_PATH) and os.path.isdir(KRONOS_ROOT):
    sys.path.insert(0, KRONOS_ROOT)
    sys.path.insert(0, KRONOS_MODEL_PATH)

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="A股预测引擎", version="0.2.0")

# ════ 历史预测存储(SQLite,引擎本地) ════
import sqlite3 as _sqlite3

_HISTORY_DB = os.path.expanduser("~/.panwatch_forecast.db")


def _init_history_db():
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            stock_name TEXT DEFAULT '',
            last_close REAL,
            last_date TEXT,
            target_date TEXT DEFAULT '',
            pred_days INTEGER,
            direction TEXT,
            expected_pct REAL,
            prediction TEXT,
            action TEXT,
            tone TEXT,
            confidence TEXT,
            target_price REAL,
            stop_loss REAL,
            summary TEXT,
            sentiment_adj REAL,
            models TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 迁移: 旧表无 stock_name/target_date 列则补(ALTER TABLE ADD COLUMN)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(forecasts)").fetchall()]
        if "stock_name" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN stock_name TEXT DEFAULT ''")
        if "target_date" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN target_date TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass
    conn.close()


_init_history_db()


def get_stock_name(symbol: str) -> str:
    """查股票名称(baostock query_stock_basic,需带市场前缀,失败返回空)。"""
    try:
        import baostock as bs
        code = f"sh.{symbol}" if symbol.startswith(("6", "9")) else f"sz.{symbol}"
        lg = bs.login()
        if lg.error_code != "0":
            return ""
        rs = bs.query_stock_basic(code=code)
        name = ""
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            # query_stock_basic(code) 返回 [code, code_name, ipoDate, outDate, type, status]
            if len(row) >= 2:
                name = row[1]
        bs.logout()
        return name or ""
    except Exception:
        return ""


def save_forecast(rec: dict):
    """保存一次预测到历史库。"""
    try:
        conn = _sqlite3.connect(_HISTORY_DB)
        conn.execute(
            """INSERT INTO forecasts
               (symbol, stock_name, last_close, last_date, target_date, pred_days, direction, expected_pct,
                prediction, action, tone, confidence, target_price, stop_loss,
                summary, sentiment_adj, models)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.get("symbol", ""), rec.get("stock_name", ""),
                rec.get("last_close"), rec.get("last_date"),
                rec.get("target_date", ""),
                rec.get("pred_days"), rec.get("direction"), rec.get("expected_pct"),
                json.dumps(rec.get("prediction", []), ensure_ascii=False),
                rec.get("action", ""), rec.get("tone", ""), rec.get("confidence", ""),
                rec.get("target_price"), rec.get("stop_loss"),
                rec.get("summary", ""), rec.get("sentiment_adj"),
                json.dumps(rec.get("models", {}), ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"保存历史失败: {e}")


def list_forecasts(limit: int = 50, symbol: str = ""):
    """查询历史预测列表。"""
    conn = _sqlite3.connect(_HISTORY_DB)
    conn.row_factory = _sqlite3.Row
    q = "SELECT * FROM forecasts"
    params: list = []
    if symbol:
        q += " WHERE symbol = ?"
        params.append(symbol)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    for r in rows:
        try:
            r["prediction"] = json.loads(r["prediction"])
        except Exception:
            pass
    return rows

# 全局缓存模型(只加载一次)
_predictor = None
_model_lock = False

# 任务进度存储: task_id -> {"status", "logs": [], "result": ...}
_tasks: dict[str, dict] = {}
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


def get_predictor():
    """懒加载 Kronos(首次 ~100MB 下载/加载,后续复用)。"""
    global _predictor, _model_lock
    if _predictor is not None:
        return _predictor
    if _model_lock:
        raise HTTPException(503, "模型加载中,请稍候")
    _model_lock = True
    try:
        from model import KronosPredictor, KronosTokenizer, Kronos
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
        _predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    finally:
        _model_lock = False
    return _predictor


def load_kline(symbol: str, days: int = 250) -> pd.DataFrame:
    """从 baostock 拉历史日K(不复权),转 Kronos 格式。

    为什么不复权: baostock 前复权对送转股有口径 bug(实测神剑
    116.53 vs 实际 11.70,差复权因子 9.96 倍)。不复权数据与
    真实价格一致,预测基于相对变化,不受影响。

    返回列: timestamp, open, high, low, close, volume, amount
    """
    import baostock as bs

    code = f"sh.{symbol}" if symbol.startswith(("6", "9")) else f"sz.{symbol}"
    lg = bs.login()
    if lg.error_code != "0":
        raise HTTPException(502, f"baostock 登录失败: {lg.error_msg}")

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days * 1.6)).strftime("%Y-%m-%d")
    rs = bs.query_history_k_data_plus(
        code,
        "date,open,high,low,close,volume,amount",
        start_date=start, end_date=end,
        frequency="d", adjustflag="3",  # 3=不复权(前复权有送转口径bug)
    )
    rows = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()

    if not rows:
        raise HTTPException(502, f"baostock 无数据: {symbol}")

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    df["timestamp"] = pd.to_datetime(df["date"])
    for c in ["open", "high", "low", "close", "volume", "amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().sort_values("timestamp").reset_index(drop=True)
    return df[["timestamp", "open", "high", "low", "close", "volume", "amount"]]


def kronos_predict(df: pd.DataFrame, pred_len: int = 5, n_samples: int = 30):
    """Kronos 蒙特卡洛预测:返回中位数 + P5/P95 区间。"""
    predictor = get_predictor()

    x_df = df[["open", "high", "low", "close", "volume", "amount"]].copy()
    x_ts = pd.Series(df["timestamp"])

    # 未来交易日
    dates = []
    cur = df["timestamp"].iloc[-1]
    while len(dates) < pred_len:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            dates.append(cur)
    y_ts = pd.Series(pd.to_datetime(dates))

    # MC 采样
    preds = []
    for _ in range(n_samples):
        p = predictor.predict(
            df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
            pred_len=pred_len, T=1.0, top_k=0, top_p=0.9,
            sample_count=1, verbose=False,
        )
        preds.append(p["close"].values)
    arr = np.array(preds)  # (n_samples, pred_len)

    return {
        "median": [round(float(x), 2) for x in np.median(arr, axis=0)],
        "p5": [round(float(x), 2) for x in np.percentile(arr, 5, axis=0)],
        "p95": [round(float(x), 2) for x in np.percentile(arr, 95, axis=0)],
        "n_samples": n_samples,
    }


def xgboost_predict(df: pd.DataFrame, pred_len: int = 5):
    """XGBoost 滚动预测(轻量,作为第二模型)。"""
    import xgboost as xgb
    from sklearn.metrics import mean_absolute_error

    # 特征: 过去 N 日 close 序列
    closes = df["close"].values
    window = 20
    X, y = [], []
    for i in range(window, len(closes)):
        X.append(closes[i - window:i])
        y.append(closes[i])
    X, y = np.array(X), np.array(y)
    if len(X) < 50:
        return None

    # 简单滚动: 训练 80% 预测未来
    split = int(len(X) * 0.8)
    model = xgb.XGBRegressor(n_estimators=100, max_depth=3, learning_rate=0.05)
    model.fit(X[:split], y[:split])

    # 滚动预测未来 pred_len 天
    last = closes[-window:]
    preds = []
    for _ in range(pred_len):
        p = model.predict(last.reshape(1, -1))[0]
        preds.append(round(float(p), 2))
        last = np.append(last[1:], p)
    return preds


def linreg_predict(df: pd.DataFrame, pred_len: int = 5):
    """多元线性回归(第三模型,趋势外推)。"""
    from sklearn.linear_model import LinearRegression

    closes = df["close"].values
    n = len(closes)
    X = np.arange(n).reshape(-1, 1)
    model = LinearRegression()
    model.fit(X, closes)
    preds = []
    for i in range(1, pred_len + 1):
        preds.append(round(float(model.predict([[n + i]])[0]), 2))
    return preds


# ════ Lag-Llama 全局缓存 ════
_lag_predictor = None
_lag_lock = False

# Lag-Llama 需要的补丁(在 import 时一次性应用)
import os as _os_ll
_os_ll.environ["HF_HOME"] = "/home/ubuntu/.cache/huggingface"
try:
    import torch as _torch
    import gluonts.torch.distributions.studentT as _studentT
    import gluonts.torch.modules.loss as _loss_mod

    _torch.serialization.add_safe_globals([
        _studentT.StudentTOutput,
        _loss_mod.NegativeLogLikelihood,
        _loss_mod.DistributionLoss,
    ])
except Exception:
    pass
try:
    import lightning.fabric.utilities.cloud_io as _cloud_io

    _orig_pl_load = _cloud_io._load

    def _patched_pl_load(*args, **kwargs):
        kwargs["weights_only"] = False
        return _orig_pl_load(*args, **kwargs)

    _cloud_io._load = _patched_pl_load
except Exception:
    pass


def get_lag_predictor():
    """懒加载 Lag-Llama predictor(首次加载慢,后续复用)。"""
    global _lag_predictor, _lag_lock
    if _lag_predictor is not None:
        return _lag_predictor
    if _lag_lock:
        raise HTTPException(503, "Lag-Llama 加载中,请稍候")
    _lag_lock = True
    try:
        import sys as _sys
        if "/tmp/lag-llama" not in _sys.path:
            _sys.path.insert(0, "/tmp/lag-llama")
        from lag_llama.gluon.estimator import LagLlamaEstimator

        ckpt = "/home/ubuntu/.cache/huggingface/models--time-series-foundation-models--Lag-Llama/snapshots/72dcfc29da106acfe38250a60f4ae29d1e56a3d9/lag-llama.ckpt"
        estimator = LagLlamaEstimator(
            ckpt_path=ckpt,
            prediction_length=5,
            context_length=32,
            input_size=1,
            n_layer=2,
            n_embd_per_head=16,
            n_head=9,
            lags_seq=["Q", "M", "W", "D", "H", "T", "S"],
            time_feat=True,  # 必须=True,feature_size 才 =15 匹配 checkpoint(92=144×? 维度)
            scaling="mean",
            batch_size=32,
            num_parallel_samples=100,
        )
        _lag_predictor = estimator.create_predictor(
            transformation=estimator.create_transformation(),
            module=estimator.create_lightning_module(use_kv_cache=True),
        )
    finally:
        _lag_lock = False
    return _lag_predictor


def lag_llama_predict(df: pd.DataFrame, pred_len: int = 5):
    """Lag-Llama 预测(第4模型,多变量时序基础模型)。

    关键: 模型预训练在标准化数据上,输入必须 mean-std 标准化,
    输出再反缩放回真实价格。否则(原始价格直接喂)外推崩坏(负价格)。
    """
    try:
        from gluonts.dataset.pandas import PandasDataset

        predictor = get_lag_predictor()

        df_long = df[["timestamp", "close"]].copy()
        df_long.columns = ["timestamp", "target"]
        df_long = df_long.set_index("timestamp")
        # 处理停牌缺口:重采样为连续工作日索引并前向填充(PandasDataset 要求均匀间隔)
        df_long = df_long.asfreq("B").ffill()
        # 模型权重是 float32,输入必须转 float32 否则 matmul dtype 不匹配
        df_long["target"] = df_long["target"].astype("float32")

        # ⚠️ 标准化: 减均值/除标准差(模型预训练分布),预测后反缩放
        mean = float(df_long["target"].mean())
        std = float(df_long["target"].std())
        if std < 1e-9:
            std = 1.0
        df_long["target"] = (df_long["target"] - mean) / std

        ds = PandasDataset(dataframes=[df_long], target="target", freq="B")

        forecasts = list(predictor.predict(ds, num_samples=100))
        samples = forecasts[0].samples  # (100, pred_len)
        # 反缩放回真实价格
        samples = samples * std + mean
        median = np.median(samples, axis=0)
        p10 = np.percentile(samples, 10, axis=0)
        p90 = np.percentile(samples, 90, axis=0)
        return {
            "median": [round(float(x), 2) for x in median[:pred_len]],
            "p10": [round(float(x), 2) for x in p10[:pred_len]],
            "p90": [round(float(x), 2) for x in p90[:pred_len]],
            "n_samples": 100,
        }
    except Exception as e:
        print(f"Lag-Llama 预测失败: {e}")
        return None


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


def _detect_panwatch_url() -> str:
    """自动探测 PanWatch 地址(容器网关 IP)。

    引擎跑在主机,PanWatch 在 Docker 容器内,需通过主机→容器网关访问。
    网关 IP 可能随 Docker 网络变化,不能写死。自动从 /proc/net/route 探测,
    多候选尝试连通性。
    """
    import socket as _socket

    candidates = []

    # 1. 环境变量优先
    import os as _os
    env = _os.getenv("PANWATCH_URL", "")
    if env:
        candidates.append(env.rstrip("/"))

    # 2. 默认网关(主机→Docker 网桥)
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    ip_int = int(parts[2], 16)
                    gw = f"{(ip_int & 0xFF)}.{(ip_int >> 8 & 0xFF)}.{(ip_int >> 16 & 0xFF)}.{(ip_int >> 24 & 0xFF)}"
                    candidates.append(f"http://{gw}:8000")
                    break
    except Exception:
        pass

    # 3. 常见 Docker 网桥(兜底)
    for ip in ("172.17.0.1", "172.18.0.1", "10.8.0.1"):
        candidates.append(f"http://{ip}:8000")

    # 4. 去重 + 探测连通性
    seen, checked = set(), []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        checked.append(c)
        try:
            r = _req_get(f"{c}/api/health", timeout=3)
            if r and r.status_code < 500:
                return c
        except Exception:
            continue

    # 全部失败: 用第一个候选(可能是环境变量)
    return candidates[0] if candidates else "http://172.17.0.1:8000"


def _req_get(url: str, timeout: float = 8):
    """轻量 GET(避免顶层 import requests 的副作用)。"""
    import requests as _r
    return _r.get(url, timeout=timeout)


PANWATCH_URL = _detect_panwatch_url()
print(f"[forecast] PanWatch 地址: {PANWATCH_URL}")


def _load_llm_config() -> dict:
    """加载 LLM 情绪打分配置。

    优先级: 本地配置(~/.panwatch_forecast.env) > PanWatch 默认 AI 模型(动态) > 硬编码兜底。
    这样在 PanWatch 设置页改 AI 模型后,预测引擎自动跟随。
    """
    import os as _os
    import json as _json

    cfg = {"base_url": "https://api.agnes-ai.cn/v1", "model": "agnes-2.5-flash", "api_key": ""}

    # 1. 本地配置覆盖
    env_path = _os.path.expanduser("~/.panwatch_forecast.env")
    if _os.path.exists(env_path):
        try:
            for line in open(env_path):
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "LLM_BASE_URL":
                    cfg["base_url"] = v
                elif k == "LLM_MODEL":
                    cfg["model"] = v
                elif k == "LLM_API_KEY":
                    cfg["api_key"] = v
        except Exception:
            pass

    # 2. 从 PanWatch 拉默认 AI 模型(设置页配置,动态跟随)
    if not cfg["api_key"]:
        try:
            import requests as _req
            r = _req.get(f"{PANWATCH_URL}/api/providers/services", timeout=8)
            if r.status_code == 200:
                services = r.json()
                # providers API 无鉴权返回? 若 401 需带 token,则跳过
                if isinstance(services, list):
                    for svc in services:
                        for m in svc.get("models", []):
                            if m.get("is_default"):
                                cfg["base_url"] = svc.get("base_url", cfg["base_url"])
                                cfg["model"] = m.get("model", cfg["model"])
                                cfg["api_key"] = svc.get("api_key", cfg["api_key"])
                                return cfg
        except Exception:
            pass

    # 3. 兜底: agnes key 文件
    if not cfg["api_key"]:
        key_path = _os.path.expanduser("~/.agnes_key")
        if _os.path.exists(key_path):
            cfg["api_key"] = open(key_path).read().strip()
        if not cfg["api_key"]:
            cfg["api_key"] = _os.getenv("AGNES_API_KEY", "")
    return cfg


def llm_sentiment_score(events_text: str) -> dict:
    """LLM 语义情绪打分(替代关键词规则)。

    调 agnes-ai chat completions,让 LLM 判断公告/新闻情绪:
    - score: -2(重大利空) ~ +2(重大利好), 0=中性
    - reason: 一句话理由
    失败时返回 None(调用方降级到关键词规则)。
    """
    if not events_text.strip():
        return None
    try:
        import requests as _req
        import os as _os

        # 动态加载配置(设置页改模型 → 引擎自动跟随)
        cfg = _load_llm_config()
        api_key = cfg.get("api_key", "")
        base_url = cfg.get("base_url", "https://api.agnes-ai.cn/v1").rstrip("/")
        model = cfg.get("model", "agnes-2.5-flash")

        prompt = f"""你是A股短线情绪分析专家。以下是一只股票最近7天的公告/新闻标题:
{events_text[:800]}

请判断这些消息对股价的短期(1-5天)影响,只输出JSON:
{{"score": -2到+2的整数, "reason": "一句话理由"}}
规则: -2=重大利空(立案/退市/清仓减持/业绩暴雷), -1=利空(小幅减持/问询),
0=中性/无关, +1=利好(中标/回购/预增), +2=重大利好(重组/大额订单/政策利好)"""

        r = _req.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "你只输出JSON,不输出其他文字。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            # 推理模型可能把思考放 reasoning_content,content 为空
            if not content.strip():
                content = msg.get("reasoning_content") or ""
            # 提取 JSON(容错:可能包在 ```json 里)
            import re as _re
            m = _re.search(r"\{[^}]*\"score\"[^}]*\}", content)
            if m:
                data2 = json.loads(m.group(0))
                score = int(data2.get("score", 0))
                score = max(-2, min(2, score))
                return {"score": score, "reason": data2.get("reason", ""), "source": "llm"}
            return {"score": 0, "reason": f"LLM返回无法解析: {content[:80]}", "source": "llm-fallback"}
        return None
    except Exception as e:
        print(f"LLM情绪打分失败: {e}")
        return None


def fetch_sentiment(symbol: str) -> dict:
    """消息情绪面: 个股公告/新闻 + 板块共振 + 市场情绪。

    复用 PanWatch 数据体系(wudao MCP + 东财涨停池),输出事件修正系数。
    方法论: a-share-multi-model-prediction skill Pitfall #9(隔夜事件)。
    - 重大利好事件 + 板块宽度≥4 → +0.5%~+1.5%
    - 重大利空事件 → 对称下修
    - 事件日 P10-P90 区间放宽 30%
    """
    result: dict = {
        "events": [], "board_peers": 0, "market_sentiment": None,
        "adjustment_pct": 0.0, "notes": [],
    }
    try:
        # 1. wudao 个股公告/事件(近7天) — 直接 HTTP 调 MCP(与 PanWatch 容器内同机制)
        import requests as _req
        import os as _os

        wu_token = _os.getenv("WUDAO_MCP_TOKEN", "")
        if wu_token:
            wu_url = _os.getenv("WUDAO_MCP_URL", "https://stock.quicktiny.cn/api/mcp")
            wu_headers = {
                "Authorization": f"Bearer {wu_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            _req.post(wu_url, headers=wu_headers, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "forecast", "version": "1.0"}},
            }, timeout=15)
            _req.post(wu_url, headers=wu_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=10)
            ev_r = _req.post(wu_url, headers=wu_headers, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "official_announcements", "arguments": {"stockCode": symbol, "days": 7}},
            }, timeout=30)
            ev = ev_r.json()
            content = ((ev.get("result") or {}).get("content") or [])
            if content:
                txt = content[0].get("text", "") if isinstance(content[0], dict) else str(content[0])
                result["events"].append({"source": "wudao", "text": txt[:400]})
    except Exception as e:
        result["notes"].append(f"wudao公告失败: {e}")

    try:
        # 2. 东财公告(备用)
        import requests as _req
        url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
        params = {"sr": "-1", "page_size": "5", "page_index": "1", "ann_type": "A", "client_source": "web", "stock_list": symbol}
        r = _req.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code == 200:
            data = r.json()
            anns = (data.get("data") or {}).get("list") or []
            for a in anns[:5]:
                result["events"].append({
                    "source": "eastmoney",
                    "title": a.get("title", ""),
                    "date": str(a.get("notice_date", ""))[:10],
                })
    except Exception:
        pass

    # 3. 板块共振(涨停池) + 市场情绪 — 直接 HTTP 调东财
    try:
        import requests as _req2
        url = "https://push2ex.eastmoney.com/getTopicZTPool"
        params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
            "Pageindex": "0", "pagesize": "60", "sort": "fbt:asc",
            "date": datetime.now().strftime("%Y%m%d"),
        }
        r = _req2.get(url, params=params, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=10)
        if r.status_code == 200:
            d = r.json()
            pool = (d.get("data") or {}).get("pool") or []
            sector_dist = {}
            for item in pool:
                sec = item.get("hybk", "") or "其他"
                sector_dist[sec] = sector_dist.get(sec, 0) + 1
            top_sectors = sorted(sector_dist.items(), key=lambda x: x[1], reverse=True)[:5]
            result["market_sentiment"] = {
                "limit_up_count": len(pool),
                "top_sectors": [{"name": k, "count": v} for k, v in top_sectors],
            }
    except Exception:
        pass

    # 4. 情绪打分: 优先 LLM 语义判断,失败降级关键词规则
    events_text = " ".join(e.get("title", "") or str(e.get("text", ""))[:100] for e in result["events"])
    adjust = 0.0

    # 4a. LLM 语义打分
    llm_res = llm_sentiment_score(events_text)
    if llm_res:
        llm_score = llm_res.get("score", 0)
        # score -2~+2 → 修正 -1.5%~+1.5% (每档 0.75%)
        adjust += llm_score * 0.75
        result["notes"].append(
            f"LLM情绪判断: {llm_score:+d} ({llm_res.get('reason', '')}) → {adjust:+.2f}%"
        )
    else:
        # 4b. 关键词规则(降级)
        bearish_kw = ["减持", "亏损", "立案", "处罚", "警示", "问询", "终止", "退市", "风险提示", "诉讼", "冻结"]
        bullish_kw = ["中标", "签约", "增持", "回购", "业绩预增", "扭亏", "获批", "订单", "涨停", "合同", "战略合作", "产能", "涨价"]

        hit_bearish = [k for k in bearish_kw if k in events_text]
        hit_bullish = [k for k in bullish_kw if k in events_text]

        if hit_bullish:
            adjust += min(1.5, 0.5 + 0.5 * len(hit_bullish))
            result["notes"].append(f"利好事件: {', '.join(hit_bullish)} → +{adjust:.1f}%")
        if hit_bearish:
            adjust -= min(1.5, 0.5 + 0.5 * len(hit_bearish))
            result["notes"].append(f"利空事件: {', '.join(hit_bearish)} → {adjust:+.1f}%")
        result["notes"].append("(关键词规则,LLM不可用)")

    # 板块宽度(涨停池 top_sectors 中是否含该股所属板块)
    ms = result.get("market_sentiment") or {}
    top_sectors = ms.get("top_sectors", [])
    if top_sectors and len(top_sectors) >= 4:
        adjust += 0.5
        result["notes"].append("市场涨停板块≥4个(情绪偏热) → +0.5%")

    result["adjustment_pct"] = round(adjust, 2)
    return result


@app.get("/health")
def health():
    return {"status": "ok", "kronos_ready": _predictor is not None, "time": datetime.now().isoformat()}


@app.get("/predict")
def predict(symbol: str, days: int = 5, task_id: str = "", target_date: str = ""):
    """多模型预测: Kronos + XGBoost + 线性回归 投票。

    target_date 可选: 预测到该日期为止(自动换算交易日数)。task_id 可选(进度日志)。
    """
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(400, "symbol 需为 6 位 A 股代码")

    tid = task_id or new_task()
    if tid not in _tasks:
        # 外部传入的 task_id 不存在则创建(允许前端先查状态再启动)
        with _tasks_lock:
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
        # 从 last_date 到 target_date 的交易日数(跳过周末)
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
            # 3 天以上用 Kronos 中位数补位(避免 Lag-Llama 长周期异常值污染)
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
    with _tasks_lock:
        _tasks[tid]["result"] = result
    return result


@app.get("/predict/status")
def predict_status(task_id: str):
    """查询任务进度与日志。"""
    with _tasks_lock:
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

    # 滚动回测: 每 20 天预测 5 天,比对方向
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
            # 用线性回归快速预测(回测不求精度,求方向)
            X = np.arange(len(hist)).reshape(-1, 1)
            from sklearn.linear_model import LinearRegression
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
    """预测引擎模型清单(设置页展示,避免"不知道什么模块用什么模型")。

    返回每个模型: 名称/用途/当前配置/可配置方式。
    """
    import os as _os

    cfg = _load_llm_config()

    # Kronos/Lag-Llama checkpoint 路径
    kronos_root = _os.path.expanduser("~/Kronos")
    lag_ckpt = _os.path.expanduser(
        "~/.cache/huggingface/models--time-series-foundation-models--Lag-Llama/"
        "snapshots/72dcfc29da106acfe38250a60f4ae29d1e56a3d9/lag-llama.ckpt"
    )
    env_path = _os.path.expanduser("~/.panwatch_forecast.env")

    return {
        "models": [
            {
                "name": "Kronos",
                "module": "预测主模型",
                "model_id": "NeoQuasar/Kronos-small",
                "location": kronos_root,
                "configurable": "本地源码路径(~/Kronos)",
            },
            {
                "name": "Lag-Llama",
                "module": "参考模型(暂不参与投票)",
                "model_id": "time-series-foundation-models/Lag-Llama",
                "location": lag_ckpt,
                "configurable": "checkpoint 路径(代码内)",
            },
            {
                "name": "XGBoost",
                "module": "投票模型",
                "model_id": "XGBRegressor(n_estimators=100, depth=3)",
                "location": "pip 包",
                "configurable": "参数在代码内",
            },
            {
                "name": "LLM情绪打分",
                "module": "公告/新闻语义判断",
                "model_id": cfg.get("model", "agnes-2.5-flash"),
                "location": cfg.get("base_url", ""),
                "configurable": "PanWatch 设置→AI 模型(默认模型),或 ~/.panwatch_forecast.env 的 LLM_MODEL",
                "api_key_set": bool(cfg.get("api_key")),
            },
            {
                "name": "PanWatch AI",
                "module": "AI对话/Agent 分析",
                "model_id": "AIModel 表默认",
                "location": "PanWatch 设置→AI 服务商",
                "configurable": "PanWatch 设置页(已有)",
            },
        ],
        "config_file": env_path,
        "config_file_exists": _os.path.exists(env_path),
        "note": "修改 LLM 情绪打分模型: ① PanWatch 设置→AI 模型 改默认模型 ② 或编辑配置文件",
    }


@app.get("/stocks/search")
def stocks_search(q: str = "", limit: int = 10):
    """股票名称/代码搜索(baostock 全市场,支持中文名/代码前缀)。"""
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
            # [code(sh.600000), tradeStatus, code_name]
            if len(row) < 3:
                continue
            code_full, status, name = row[0], row[1], row[2]
            if status != "1":
                continue  # 只取正常交易
            code6 = code_full.split(".")[-1]
            # 主板过滤: 60/00/002(与用户铁律一致,排除 300/688/8xx)
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
    """生成预测结果图片卡片(PNG,可下载)。

    用最近一次预测结果渲染卡片。返回 PNG 二进制。
    """
    import io

    # 取该股最近一次预测(优先 task_id,否则最新)
    rows = list_forecasts(limit=1, symbol=symbol)
    if task_id:
        with _tasks_lock:
            t = _tasks.get(task_id)
            if t and t.get("result"):
                data = t["result"]
                return _render_card(data)
    if not rows:
        raise HTTPException(404, f"无 {symbol} 的预测记录,先执行预测")
    data = rows[0]
    # 从历史行构造渲染数据
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
    buf = _render_card(render)
    from fastapi.responses import Response
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'inline; filename="forecast_{symbol}.png"'},
    )


def _render_card(data: dict) -> io.BytesIO:
    """用 matplotlib 渲染预测卡片。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    import matplotlib.font_manager as fm

    # 中文字体
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

    # 标题(带股票名称)
    name = data.get("stock_name", "") or ""
    title = f"A股预测 · {symbol}" + (f" · {name}" if name else "")
    ax.text(0.5, 0.96, title, ha="center", color="#e6edf3",
            fontsize=18, fontweight="bold", transform=ax.transAxes)
    ax.text(0.5, 0.92, f"基准 {last:.2f} ({data.get('last_date', '')}) → {dir_cn} {exp:+.1f}%",
            ha="center", color=color, fontsize=13, transform=ax.transAxes)

    # 预测序列
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

    # 操作建议
    action = rec.get("action", "")
    conf = rec.get("confidence", "")
    summary = rec.get("summary", "")
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
