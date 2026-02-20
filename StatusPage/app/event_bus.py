"""
Async event bus — lock-free, in-process pub/sub.

Subscribers get their own `asyncio.Queue` for backpressure.
Supports both callback and async-iterator consumption patterns.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Coroutine, Optional
from uuid import uuid4

from app.models import StatusEvent

logger = logging.getLogger(__name__)


class Subscription:
    """Handle representing a single subscriber."""

    __slots__ = ("id", "queue", "_callback")

    def __init__(
        self,
        callback: Optional[Callable[[StatusEvent], Coroutine[Any, Any, None]]] = None,
        maxsize: int = 256,
    ) -> None:
        self.id: str = uuid4().hex
        self.queue: asyncio.Queue[StatusEvent] = asyncio.Queue(maxsize=maxsize)
        self._callback = callback

    async def deliver(self, event: StatusEvent) -> None:
        """Push event to the subscriber's queue and fire callback if set."""
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Drop oldest to prevent memory pressure — log the drop
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(event)
            logger.warning("Subscriber %s queue full — dropped oldest event", self.id)

        if self._callback is not None:
            try:
                await self._callback(event)
            except Exception:
                logger.exception("Subscriber %s callback failed", self.id)


class EventBus:
    """
    Asyncio-native event bus.

    Usage:
        bus = EventBus()

        # callback style
        sub = bus.subscribe(callback=my_async_handler)

        # async iterator style (for SSE)
        async for event in bus.stream():
            yield event.model_dump_json()

        # publish from a monitor
        await bus.publish(event)
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, Subscription] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        callback: Optional[Callable[[StatusEvent], Coroutine[Any, Any, None]]] = None,
        maxsize: int = 256,
    ) -> Subscription:
        """Register a new subscriber. Returns the Subscription handle."""
        sub = Subscription(callback=callback, maxsize=maxsize)
        async with self._lock:
            self._subscribers[sub.id] = sub
        logger.info("New subscriber registered: %s (total: %d)", sub.id, len(self._subscribers))
        return sub

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscriber."""
        async with self._lock:
            self._subscribers.pop(subscription_id, None)
        logger.info("Subscriber removed: %s", subscription_id)

    async def publish(self, event: StatusEvent) -> None:
        """Fan-out an event to all active subscribers."""
        async with self._lock:
            subs = list(self._subscribers.values())

        if not subs:
            return

        await asyncio.gather(
            *(sub.deliver(event) for sub in subs),
            return_exceptions=True,
        )
        logger.debug(
            "Published event for %s/%s to %d subscribers",
            event.provider,
            event.incident.name,
            len(subs),
        )

    async def stream(self) -> AsyncIterator[StatusEvent]:
        """
        Async generator that yields events as they arrive.
        Automatically cleans up the subscription on exit.
        """
        sub = await self.subscribe()
        try:
            while True:
                event = await sub.queue.get()
                yield event
        finally:
            await self.unsubscribe(sub.id)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
