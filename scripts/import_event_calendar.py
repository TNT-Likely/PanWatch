#!/usr/bin/env python3
"""事件日历导入脚本:读自定义 JSON(数组)UPSERT 入 event_calendar_items。

用法:
    python scripts/import_event_calendar.py --file path/to/calendar.json

JSON 格式(数组,元素字段见 docs/event-calendar.schema.md):
    [
      {
        "event_date": "2026-10-01",
        "level": "high",
        "name": "示例事件(占位,不含真实数据)",
        "scope": "全球",
        "expected": "",
        "actual": "",
        "direction": "neutral",
        "impact_boards": []
      }
    ]

行为:
- 逐条校验(event_date/name 必填,level/direction 受控枚举),非法条目报错并终止,
  不做静默截断;
- 按 (event_date, name) UPSERT:同日同名更新,否则新增;可重复执行(幂等);
- 不访问任何外部网络接口,只写本地库。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 脚本直跑时把仓库根加入 sys.path,复用应用自身的持久化层
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.market import event_calendar_service  # noqa: E402
from src.platform.persistence.database import SessionLocal, init_db  # noqa: E402

logger = logging.getLogger("import_event_calendar")


def load_items(path: str | Path) -> list[dict]:
    """读 JSON 文件并校验顶层是数组;返回原始 dict 列表。"""
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise SystemExit("JSON 顶层必须是非空数组")
    items = [x for x in data if isinstance(x, dict)]
    if len(items) != len(data):
        raise SystemExit("数组中存在非对象元素")
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入事件日历 JSON(UPSERT)")
    parser.add_argument("--file", required=True, help="JSON 文件路径(数组)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    items = load_items(args.file)

    init_db()
    db = SessionLocal()
    try:
        result = event_calendar_service.upsert_items(db, items)
    except ValueError as exc:
        db.rollback()
        logger.error("条目校验失败,已整体回滚(未写入任何行): %s", exc)
        return 2
    finally:
        db.close()

    logger.info(
        "导入完成: 共 %s 条,新增 %s,更新 %s",
        len(items),
        result["created"],
        result["updated"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
