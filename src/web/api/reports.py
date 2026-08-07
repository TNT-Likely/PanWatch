"""报告中心 API: 列出/读取/同步 Hermes cron 输出报告。

数据源: ~/.hermes/cron/output/<job_id>/*.md
同步目标: ~/Obsidian/FinanceVault/03-CronReports/<job_name>/YYYY-MM-DD.md
"""
import os
import re
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

# Hermes cron 输出根目录
# 容器内通过 HERMES_HOME 环境变量指定(挂载主机 ~/.hermes 到 /hermes, HERMES_HOME=/hermes)
# 则 CRON_OUTPUT_DIR = /hermes/cron/output
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME")
    or os.environ.get("CRON_OUTPUT_DIR")
    or "/hermes"  # 推荐挂载点
)
CRON_OUTPUT_DIR = HERMES_HOME / "cron" / "output"

# Obsidian vault 目标目录
OBSIDIAN_VAULT = Path(os.environ.get("OBSIDIAN_VAULT", "/home/ubuntu/Obsidian/FinanceVault"))
OB_REPORTS_DIR = OBSIDIAN_VAULT / "03-CronReports"


def _strip_meta(content: str) -> str:
    """剥掉 cron 原始报告的元信息噪音, 只留正文。

    噪音构成(开头):
      # Cron Job: <任务名>
      **Job ID:** ...
      **Run Time:** ...
      **Schedule:** ...
      ## Prompt
      <整段 skill 定义 / Prompt 内容>
      ...
      ## Response            <- cron 系统注入的"正文开始"标记
      # 📈 <真正的报告标题>   <- 正文起点

    正文起点锚定: 找到 '## Response' 之后出现的第一个一级标题 '# x'(非 '## ')。
    找不到 Response 则退回到 '## Prompt' 之后第一个 '# '; 再找不到则原样返回(不误删)。
    """
    lines = content.splitlines()

    def _first_h1_after(start: int) -> int | None:
        for j in range(start + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("# ") and not s.startswith("## "):
                return j
        return None

    # 优先锚点: ## Response
    anchor = None
    for i, ln in enumerate(lines):
        if ln.strip() == "## Response":
            anchor = i
            break
    # 次选锚点: ## Prompt
    if anchor is None:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("## Prompt"):
                anchor = i
                break

    if anchor is None:
        return content

    body_start = _first_h1_after(anchor)
    if body_start is None:
        # 没找到一级标题, 退回到 anchor 之后第一行非空
        for j in range(anchor + 1, len(lines)):
            if lines[j].strip():
                body_start = j
                break
    if body_start is None:
        return content
    return "\n".join(lines[body_start:])



def _job_name_map() -> dict:
    """job_id → 人类可读任务名(从 jobs.json 读)。"""
    jobs_file = HERMES_HOME / "cron" / "jobs.json"
    if not jobs_file.exists():
        return {}
    try:
        data = json.loads(jobs_file.read_text())
        return {j["id"]: j.get("name", j["id"]) for j in data.get("jobs", [])}
    except Exception as e:
        logger.warning(f"读 jobs.json 失败: {e}")
        return {}


@router.get("/list")
async def list_reports(
    job_id: Optional[str] = Query(None, description="按 job_id 过滤"),
    limit: int = Query(200, ge=1, le=1000, description="最多返回多少个文件"),
):
    """列出所有 cron 报告。

    按 job_id 分组,每组按 mtime 倒序(最新在前)。
    返回: [{ job_id, job_name, file, size, mtime, title_preview }, ...]
    """
    if not CRON_OUTPUT_DIR.exists():
        return {"items": [], "total": 0, "jobs": []}

    name_map = _job_name_map()
    items = []
    jobs_seen = set()

    for job_dir in sorted(CRON_OUTPUT_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        jid = job_dir.name
        if job_id and jid != job_id:
            continue
        jobs_seen.add(jid)

        for f in job_dir.iterdir():
            if not f.is_file() or not f.name.endswith(".md"):
                continue
            try:
                stat = f.stat()
            except OSError:
                continue
            # 提取 md 标题(第一行非空 # 开头)
            title_preview = ""
            try:
                with f.open("r", encoding="utf-8", errors="ignore") as fp:
                    for line in fp:
                        line = line.strip()
                        if line.startswith("# "):
                            title_preview = line[2:].strip()[:80]
                            break
            except Exception:
                pass
            items.append({
                "job_id": jid,
                "job_name": name_map.get(jid, jid),
                "file": f.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "mtime_iso": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "title_preview": title_preview,
            })

    # 按 mtime 倒序
    items.sort(key=lambda x: x["mtime"], reverse=True)
    items = items[:limit]

    return {
        "items": items,
        "total": len(items),
        "jobs": [
            {"job_id": j, "job_name": name_map.get(j, j)}
            for j in sorted(jobs_seen)
        ],
    }


@router.get("/content")
async def get_report_content(
    job_id: str = Query(...),
    file: str = Query(...),
):
    """读取单个报告完整 markdown。

    优先读 Obsidian 精修版(03-CronReports/<job_name>/<date>.md, 已去噪);
    找不到则 fallback 到 cron 原始输出(同样过 _strip_meta 去噪), 保证 Dialog 不展示元信息噪音。
    """
    # 防止路径穿越
    if ".." in file or "/" in file or "\\" in file:
        raise HTTPException(400, "非法文件名")

    # 1) 优先 Obsidian 精修版: 用文件名日期 YYYY-MM-DD 匹配 <date>.md
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_", file)
    vault_content = None
    if m and OB_REPORTS_DIR.exists():
        date_str = m.group(1)
        job_name = _job_name_map().get(job_id, job_id)
        # 任务名清理规则需与 sync_to_vault 保持一致
        safe_name = re.sub(r"[^\w\u4e00-\u9fff\-_]", "_", job_name).strip("_")[:60]
        if not safe_name:
            safe_name = job_id
        vault_file = OB_REPORTS_DIR / safe_name / f"{date_str}.md"
        if vault_file.exists():
            vault_content = vault_file.read_text(encoding="utf-8", errors="ignore")

    # 2) fallback: cron 原始输出
    f = CRON_OUTPUT_DIR / job_id / file
    if vault_content is None:
        if not f.exists() or not f.is_file():
            raise HTTPException(404, f"报告不存在: {job_id}/{file}")
        try:
            vault_content = f.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            raise HTTPException(500, f"读取失败: {e}")

    # 统一去噪(cron 原始版必去; Obsidian 版已是去噪后的, 再跑一次无副作用)
    content = _strip_meta(vault_content)
    return {"job_id": job_id, "file": file, "content": content}


class SyncResult(BaseModel):
    synced: int
    skipped: int
    errors: list
    target_dir: str


@router.post("/sync-to-vault", response_model=SyncResult)
async def sync_to_vault(
    job_id: Optional[str] = Query(None, description="只同步某个 job; 缺省全部"),
):
    """同步 cron 报告到 Obsidian vault: ~/Obsidian/FinanceVault/03-CronReports/<job_name>/

    目标文件名: YYYY-MM-DD.md(从源文件名提取日期, 多次同日合并取最新)。
    """
    if not CRON_OUTPUT_DIR.exists():
        raise HTTPException(503, "cron 输出目录不存在")

    OB_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    name_map = _job_name_map()

    synced = 0
    skipped = 0
    errors = []

    # 按 (job_id, 日期) 分组: 选最新 mtime 的文件代表那天
    grouped: dict[tuple[str, str], Path] = {}
    for job_dir in CRON_OUTPUT_DIR.iterdir():
        if not job_dir.is_dir():
            continue
        jid = job_dir.name
        if job_id and jid != job_id:
            continue
        for f in job_dir.iterdir():
            if not f.is_file() or not f.name.endswith(".md"):
                continue
            # 文件名格式: YYYY-MM-DD_HH-MM-SS.md
            m = re.match(r"^(\d{4}-\d{2}-\d{2})_", f.name)
            if not m:
                continue
            date_str = m.group(1)
            key = (jid, date_str)
            # 同日多份 → 取 mtime 最新
            existing = grouped.get(key)
            if existing is None or f.stat().st_mtime > existing.stat().st_mtime:
                grouped[key] = f

    for (jid, date_str), src in grouped.items():
        job_name = name_map.get(jid, jid)
        # 任务名清理(去掉路径不安全字符)
        safe_name = re.sub(r"[^\w\u4e00-\u9fff\-_]", "_", job_name).strip("_")[:60]
        if not safe_name:
            safe_name = jid
        target_dir = OB_REPORTS_DIR / safe_name
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{date_str}.md"
            # 加 frontmatter 让 Obsidian 能识别任务名
            try:
                rel = target.relative_to(OBSIDIAN_VAULT)
            except ValueError:
                rel = target
            content = src.read_text(encoding="utf-8", errors="ignore")
            # 剥掉 cron 元信息噪音(Job ID / Run Time / Prompt 区块), 只留正文
            content = _strip_meta(content)
            if not content.startswith("---"):
                fm = (
                    f"---\n"
                    f"job_id: {jid}\n"
                    f"job_name: \"{job_name}\"\n"
                    f"source_file: {src.name}\n"
                    f"synced_at: {datetime.now().isoformat()}\n"
                    f"tags: [cron-report, panwatch, {safe_name}]\n"
                    f"---\n\n"
                )
                content = fm + content
            # 仅在内容变化时覆盖, 减少无谓写入
            if target.exists() and target.read_text(encoding="utf-8", errors="ignore") == content:
                skipped += 1
            else:
                target.write_text(content, encoding="utf-8")
                synced += 1
        except Exception as e:
            errors.append(f"{safe_name}/{date_str}: {e}")

    return SyncResult(
        synced=synced,
        skipped=skipped,
        errors=errors,
        target_dir=str(OB_REPORTS_DIR),
    )


@router.get("/vault-status")
async def vault_status():
    """检查 Obsidian vault 是否可写,返回现有报告统计。"""
    if not OBSIDIAN_VAULT.exists():
        return {
            "exists": False,
            "vault_path": str(OBSIDIAN_VAULT),
            "hint": "Obsidian vault 路径不存在, 同步会失败",
            "reports_count": 0,
        }
    if not OB_REPORTS_DIR.exists():
        return {
            "exists": True,
            "vault_path": str(OBSIDIAN_VAULT),
            "reports_dir": str(OB_REPORTS_DIR),
            "reports_count": 0,
            "tasks": [],
        }
    tasks = []
    total = 0
    for d in OB_REPORTS_DIR.iterdir():
        if d.is_dir():
            n = sum(1 for f in d.iterdir() if f.suffix == ".md")
            total += n
            tasks.append({"task_name": d.name, "count": n})
    tasks.sort(key=lambda x: x["count"], reverse=True)
    return {
        "exists": True,
        "vault_path": str(OBSIDIAN_VAULT),
        "reports_dir": str(OB_REPORTS_DIR),
        "reports_count": total,
        "tasks": tasks,
    }