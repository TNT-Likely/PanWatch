"""运行中调度器的轻量注册表,供系统自检读取健康状态。

各调度器 start() 时把自身的 APScheduler(有 .running / .get_jobs())register 进来;
自检的 probe_scheduler 据此判断"调度器是否在跑"。CLI 等无调度的进程注册表为空 → 优雅跳过。
"""

from __future__ import annotations

_REGISTRY: dict[str, object] = {}


def register(name: str, scheduler: object) -> None:
    _REGISTRY[name] = scheduler


def get_all() -> dict[str, object]:
    return dict(_REGISTRY)


def clear() -> None:
    _REGISTRY.clear()
