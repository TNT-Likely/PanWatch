"""命令行系统自检:`python -m src.core.doctor` 或 `make doctor`。

终端跑一遍 系统基础项(DB/磁盘/调度)+ 数据源/AI/通知,打印结果与中文修复建议。
CLI 进程内无运行中的调度器 → 调度项会优雅跳过(显示说明,不误报)。
退出码:有异常项返回 1,全通返回 0(便于 CI/脚本判断)。
"""

from __future__ import annotations

import asyncio
import sys

from src.core.selfcheck import run_selfcheck

_ICON = {"ok": "✅", "slow": "⚠️", "fail": "❌"}
_CAT = {"system": "系统", "datasource": "数据源", "ai": "AI模型", "notify": "通知渠道"}
_ORDER = ["system", "datasource", "ai", "notify"]


def _print_report(res: dict) -> None:
    s = res["summary"]
    print("\n===== PanWatch 系统自检 =====")
    print(f"共 {s['total']} · ✅通 {s['ok']} · ⚠️慢 {s['slow']} · ❌断 {s['fail']}\n")
    items = res.get("items", [])
    for cat in _ORDER:
        cat_items = [i for i in items if i["category"] == cat]
        if not cat_items:
            continue
        print(f"【{_CAT.get(cat, cat)}】")
        for i in cat_items:
            icon = _ICON.get(i["status"], "?")
            grp = f"{i['group']} / " if i.get("group") else ""
            lat = f"  {i['latency_ms']}ms" if i.get("latency_ms") else ""
            print(f"  {icon} {grp}{i['name']}{lat}")
            if i["status"] == "fail":
                if i.get("error"):
                    print(f"       错误: {i['error']}")
                if i.get("hint"):
                    print(f"       建议: {i['hint']}")
            elif i.get("note"):
                print(f"       {i['note']}")
        print()
    if s["fail"]:
        print(f"⚠️  发现 {s['fail']} 项异常,见上方建议。")
    else:
        print("✅ 全部正常。")


def main() -> int:
    res = asyncio.run(run_selfcheck())
    _print_report(res)
    return 1 if res["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
