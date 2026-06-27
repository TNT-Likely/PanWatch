"""reports/ 目录路径约定 — 按股票代码分子目录存放成稿。"""

from __future__ import annotations

from pathlib import Path


def normalize_report_symbol(symbol: str) -> str:
    """规范化股票代码目录名(去空格、大写 ticker)。"""
    return (symbol or "").strip().upper()


def symbol_report_dir(reports_dir: Path | str, symbol: str) -> Path:
    """某只股票报告子目录: reports/{代码}/"""
    root = Path(reports_dir)
    sym = normalize_report_symbol(symbol)
    return root / sym if sym else root


def iter_report_md_files(reports_dir: Path | str) -> list[Path]:
    """递归列出 reports/ 下所有 .md 文件(含根目录与子目录)。"""
    root = Path(reports_dir)
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.md"))
