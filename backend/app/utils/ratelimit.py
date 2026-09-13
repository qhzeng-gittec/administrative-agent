"""内存版固定窗口限流器。

单进程有效；多副本部署时应替换为 Redis 计数（当前项目登录接口单后端进程，够用）。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class MemoryRateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._attempts[key] if now - t < self.window_seconds]

    def check(self, key: str) -> bool:
        """当前是否允许（未超限）。"""
        now = time.monotonic()
        with self._lock:
            return len(self._recent(key, now)) < self.max_attempts

    def hit(self, key: str) -> None:
        """记录一次尝试（失败时调用）。"""
        now = time.monotonic()
        with self._lock:
            recent = self._recent(key, now)
            recent.append(now)
            self._attempts[key] = recent

    def clear(self, key: str) -> None:
        """成功后清除记录。"""
        with self._lock:
            self._attempts.pop(key, None)

    def remaining(self, key: str) -> int:
        """窗口内剩余可用次数（便于响应头提示）。"""
        now = time.monotonic()
        with self._lock:
            return max(0, self.max_attempts - len(self._recent(key, now)))
