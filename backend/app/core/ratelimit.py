"""Rate-limit buckets (spec §43) and login throttling (spec §48).

Two backends behind one interface:

* `RedisBuckets` — shared across API and worker processes when REDIS_URL is set
  (fixed-window counters with expiry, atomic INCR).
* `MemoryBuckets` — process-local fallback used in development/tests.

Durable *application* limits (per hour / per day) are additionally enforced from
the database (see executor.preflight), because a restart must not reset the
count of real submissions.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

from ..config import settings


class Buckets(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Consume one unit. Returns (allowed, remaining)."""

    def peek(self, key: str, limit: int, window_seconds: int) -> int: ...

    def reset(self, key: str) -> None: ...


class MemoryBuckets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, int]] = {}  # key -> (window_start, count)

    def _window(self, key: str, window_seconds: int, now: float) -> tuple[int, int]:
        start = int(now // window_seconds) * window_seconds
        current = self._windows.get(key)
        if current is None or current[0] != start:
            current = (start, 0)
        return current

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            start, count = self._window(key, window_seconds, now)
            if count >= limit:
                self._windows[key] = (start, count)
                return False, 0
            self._windows[key] = (start, count + 1)
            return True, limit - count - 1

    def peek(self, key: str, limit: int, window_seconds: int) -> int:
        with self._lock:
            _, count = self._window(key, window_seconds, time.time())
            return max(0, limit - count)

    def reset(self, key: str) -> None:
        with self._lock:
            self._windows.pop(key, None)


class RedisBuckets:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)

    def _key(self, key: str, window_seconds: int) -> str:
        start = int(time.time() // window_seconds) * window_seconds
        return f"oa:rl:{key}:{start}"

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        rkey = self._key(key, window_seconds)
        pipe = self._client.pipeline()
        pipe.incr(rkey)
        pipe.expire(rkey, window_seconds + 5)
        results: Any = pipe.execute()
        count = int(results[0])
        if count > limit:
            return False, 0
        return True, limit - count

    def peek(self, key: str, limit: int, window_seconds: int) -> int:
        value: Any = self._client.get(self._key(key, window_seconds))
        count = int(value) if value else 0
        return max(0, limit - count)

    def reset(self, key: str) -> None:
        for found in self._client.scan_iter(f"oa:rl:{key}:*"):
            self._client.delete(found)


_buckets: Buckets | None = None
_buckets_lock = threading.Lock()


def buckets() -> Buckets:
    global _buckets
    with _buckets_lock:
        if _buckets is None:
            url = settings().redis_url
            if url:
                try:
                    candidate = RedisBuckets(url)
                    candidate.peek("healthcheck", 1, 1)
                    _buckets = candidate
                except Exception:  # noqa: BLE001 - fall back, log elsewhere
                    _buckets = MemoryBuckets()
            else:
                _buckets = MemoryBuckets()
        return _buckets


def reset_for_tests() -> None:
    global _buckets
    with _buckets_lock:
        _buckets = None
