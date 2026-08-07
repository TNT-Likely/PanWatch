"""多 key 池: 同源多个凭证的轮换 + 限流冷却调度。

设计要点:
- 进程级内存状态(Engine 本身不依赖 DB/web, 见 engine.py 顶部注释)。
- KeyPool 对一组 key 维护: 当前健康度、限流冷却截止时间、累计用量。
- pick(): 返回当前最优 key —— 优先未冷却且用量最低者; 全冷却则取最早恢复的那个。
- mark_failure(key, *, rate_limited=False): 标记该 key 失效。rate_limited=True 进入冷却
  (默认 60s, 可由 cooldown_sec 调整), 否则标记为坏 key(本轮不再用, 但不过冷却)。
- mark_success(key): 清零坏标记, 累加用量。

与 Engine 配合: Engine.fetch 在调 vendor 前, 若 SourceConfig.key_pool 非空, 用 KeyPool
选一个 key 注入 call_config["api_key"]; vendor 约定从 config["api_key"] 读凭证。
vendor 抛限流/失效异常时, Engine 调 mark_failure 冷却该 key 并重试用下一个。
"""
from __future__ import annotations

import threading
import time


class KeyPool:
    def __init__(self, keys: list[str], *, cooldown_sec: float = 60.0, daily_quota: int | None = None):
        self._lock = threading.Lock()
        self._cooldown = cooldown_sec
        self._daily_quota = daily_quota  # 可选: 单 key 日配额, 超出则冷却
        # key -> state
        self._state: dict[str, dict] = {
            k: {"cooldown_until": 0.0, "bad": False, "used": 0} for k in (keys or [])
        }

    def add(self, key: str) -> None:
        with self._lock:
            if key not in self._state:
                self._state[key] = {"cooldown_until": 0.0, "bad": False, "used": 0}

    def remove(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._state)

    def pick(self) -> str | None:
        """返回当前最优 key; 空池返回 None。"""
        with self._lock:
            if not self._state:
                return None
            now = time.monotonic()
            usable = [
                (k, s) for k, s in self._state.items()
                if not s["bad"] and s["cooldown_until"] <= now
            ]
            if not usable:
                # 全冷却/坏: 取最早冷却结束的(避免死锁)
                k = min(self._state, key=lambda kk: self._state[kk]["cooldown_until"])
                return k
            # 优先用量最低(配额均匀); 同用量取冷却结束最早
            k = min(usable, key=lambda ks: (self._state[ks[0]]["used"], self._state[ks[0]]["cooldown_until"]))
            return k[0]

    def mark_success(self, key: str) -> None:
        with self._lock:
            s = self._state.get(key)
            if s:
                s["bad"] = False
                s["used"] += 1

    def mark_failure(self, key: str, *, rate_limited: bool = False) -> None:
        with self._lock:
            s = self._state.get(key)
            if not s:
                return
            if rate_limited:
                s["cooldown_until"] = time.monotonic() + self._cooldown
            else:
                s["bad"] = True

    def snapshot(self) -> list[dict]:
        with self._lock:
            now = time.monotonic()
            return [
                {
                    "key": k[:6] + "…" + k[-4:] if len(k) > 12 else k,
                    "cooling": s["cooldown_until"] > now,
                    "bad": s["bad"],
                    "used": s["used"],
                }
                for k, s in self._state.items()
            ]
