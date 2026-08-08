# 历史预测存储(SQLite)
import os, json
import sqlite3 as _sqlite3

_HISTORY_DB = os.path.expanduser('~/.panwatch_forecast.db')



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
        if "sentiment_notes" not in cols:
            conn.execute("ALTER TABLE forecasts ADD COLUMN sentiment_notes TEXT DEFAULT ''")
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
                summary, sentiment_adj, sentiment_notes, models)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.get("symbol", ""), rec.get("stock_name", ""),
                rec.get("last_close"), rec.get("last_date"),
                rec.get("target_date", ""),
                rec.get("pred_days"), rec.get("direction"), rec.get("expected_pct"),
                json.dumps(rec.get("prediction", []), ensure_ascii=False),
                rec.get("action", ""), rec.get("tone", ""), rec.get("confidence", ""),
                rec.get("target_price"), rec.get("stop_loss"),
                rec.get("summary", ""), rec.get("sentiment_adj"),
                rec.get("sentiment_notes", "[]"),
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
