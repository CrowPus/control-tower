"""Realtime event bus (spec §47).

Domain events (opportunity discovered, application state changed, agent status,
recruiter reply, interview detected, automation paused, worker error) are
published here and streamed to the browser over Server-Sent Events.

Two transports share one API:

* in-process fan-out (always on) — API and embedded worker in one process
* Redis pub/sub (when REDIS_URL is set) — separate worker processes publish and
  the API process relays to its SSE subscribers

Every event belongs to one member (`user_id`, by default the ambient tenant of
the code that emits it) and is delivered only to that member's open tabs and
phones. An event with no member is server-wide and reaches no member at all.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import settings
from .logging import get_logger

log = get_logger(__name__)

CHANNEL = "oa:events"
ORIGIN = f"{os.getpid()}-{secrets.token_hex(4)}"


@dataclass
class Event:
    type: str
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    origin: str = ORIGIN
    user_id: int | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue[Event]]] = {}
        self._next = 1
        self._recent: list[Event] = []
        self._redis: Any | None = None
        self._relay_started = False
        self._closing = False

    # --- publishing ---------------------------------------------------------------
    def publish(self, event: Event, *, forward: bool = True) -> None:
        with self._lock:
            self._recent.append(event)
            del self._recent[:-200]
            targets = list(self._subscribers.values())
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                pass  # loop closed; subscriber will be dropped on next cleanup
        if forward:
            self._forward(event)

    def emit(self, type: str, **kwargs: Any) -> None:
        payload = kwargs.pop("payload", {})
        entity_type = kwargs.pop("entity_type", None)
        entity_id = kwargs.pop("entity_id", None)
        owner = kwargs.pop("user_id", None)
        payload.update(kwargs)
        self.publish(
            Event(
                type=type,
                entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                payload=payload,
                user_id=owner,
            )
        )

    def recent(self, limit: int = 50, *, user_id: int | None = None) -> list[Event]:
        """The latest events; with `user_id`, only that member's."""
        with self._lock:
            events = list(self._recent)
        if user_id is not None:
            events = [e for e in events if e.user_id == user_id]
        return events[-limit:]

    # --- subscribing (SSE) ----------------------------------------------------------
    def close(self) -> None:
        """Ends every open subscription (server shutdown): a streaming response
        that never finishes would otherwise block a graceful restart forever."""
        self._closing = True
        with self._lock:
            targets = list(self._subscribers.values())
        for loop, queue in targets:
            loop.call_soon_threadsafe(queue.put_nowait, Event(type="shutdown"))

    async def subscribe(self) -> AsyncIterator[Event]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        with self._lock:
            token = self._next
            self._next += 1
            self._subscribers[token] = (loop, queue)
        self._ensure_relay()
        try:
            while not self._closing:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield Event(type="heartbeat")
                    continue
                if event.type == "shutdown":
                    return
                yield event
        finally:
            with self._lock:
                self._subscribers.pop(token, None)

    # --- redis relay ------------------------------------------------------------------
    def _redis_client(self) -> Any | None:
        url = settings().redis_url
        if not url:
            return None
        if self._redis is None:
            try:
                import redis

                self._redis = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
                self._redis.ping()
            except Exception as exc:  # noqa: BLE001
                log.warning("Redis unavailable for event relay: %s", exc.__class__.__name__)
                self._redis = None
        return self._redis

    def _forward(self, event: Event) -> None:
        client = self._redis_client()
        if client is None:
            return
        try:
            client.publish(CHANNEL, event.to_json())
        except Exception as exc:  # noqa: BLE001
            log.warning("Redis publish failed: %s", exc.__class__.__name__)
            self._redis = None

    def _ensure_relay(self) -> None:
        """Start a background thread that relays Redis events into this process."""
        if self._relay_started or not settings().redis_url:
            return
        self._relay_started = True

        def run() -> None:
            import redis

            while True:
                # A connection of its own that may wait: the shared one gives up after 2 seconds without data, which
                # dropped the subscription every few seconds when nothing happened (events in the gaps were lost).
                try:
                    client: Any = redis.Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=None, health_check_interval=30)
                    client.ping()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Redis unavailable for event relay: %s", exc.__class__.__name__)
                    time.sleep(5)
                    continue
                try:
                    pubsub = client.pubsub(ignore_subscribe_messages=True)
                    pubsub.subscribe(CHANNEL)
                    for message in pubsub.listen():
                        data = message.get("data")
                        if isinstance(data, bytes):
                            raw = json.loads(data.decode("utf-8"))
                            if raw.get("origin") == ORIGIN:
                                continue  # our own publish already reached local subscribers
                            self.publish(Event(**raw), forward=False)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Redis relay interrupted: %s", exc.__class__.__name__)
                    client.close()
                    time.sleep(3)

        threading.Thread(target=run, name="event-relay", daemon=True).start()


bus = EventBus()
