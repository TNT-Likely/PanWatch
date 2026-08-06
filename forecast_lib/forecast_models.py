# 模型层: Kronos / Lag-Llama / XGBoost / 线性回归
import os, sys, json, time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from fastapi import HTTPException

# Kronos 路径
KRONOS_ROOT = os.path.expanduser('~/Kronos')
KRONOS_MODEL_PATH = os.path.join(KRONOS_ROOT, 'model')
if os.path.isdir(KRONOS_MODEL_PATH) and os.path.isdir(KRONOS_ROOT):
    sys.path.insert(0, KRONOS_ROOT)
    sys.path.insert(0, KRONOS_MODEL_PATH)


# 全局缓存模型(只加载一次)
_predictor = None
_model_lock = False


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

# ════ Lag-Llama 全局缓存 ════
_lag_predictor = None
_lag_lock = False
import os as _os_ll
_os_ll.environ['HF_HOME'] = '/home/ubuntu/.cache/huggingface'
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
        kwargs['weights_only'] = False
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