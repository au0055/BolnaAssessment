"""
MonitorRegistry — manages the lifecycle of all provider monitors.

Each monitor runs as an independent asyncio Task inside a TaskGroup.
Designed to handle 100+ concurrent providers with zero thread overhead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.event_bus import EventBus
from app.models import StatusSummary
from app.monitor import ProviderConfig, StatusPageMonitor

logger = logging.getLogger(__name__)


class MonitorRegistry:
    """
    Central registry for status-page monitors.

    Usage:
        registry = MonitorRegistry(event_bus)
        registry.register(ProviderConfig("OpenAI", "https://status.openai.com/api/v2"))
        await registry.start_all()   # launches all monitors
        await registry.stop_all()    # graceful shutdown
    """

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._monitors: dict[str, StatusPageMonitor] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._client: Optional[httpx.AsyncClient] = None

    def register(self, config: ProviderConfig) -> StatusPageMonitor:
        """Create and register a monitor for the given provider."""
        monitor = StatusPageMonitor(
            config=config,
            event_bus=self.event_bus,
            client=self._client,
        )
        self._monitors[config.name] = monitor
        logger.info("Registered monitor: %s (%s)", config.name, config.base_url)
        return monitor

    async def start_all(self) -> None:
        """Launch all registered monitors as asyncio tasks."""
        # Shared HTTP client for connection pooling across monitors
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=200,
                max_keepalive_connections=50,
            ),
        )

        for name, monitor in self._monitors.items():
            monitor._client = self._client
            monitor._owns_client = False
            task = asyncio.create_task(monitor.start(), name=f"monitor-{name}")
            self._tasks[name] = task
            logger.info("Launched monitor task: %s", name)

        logger.info(
            "All %d monitors started — shared connection pool active",
            len(self._monitors),
        )

    async def stop_all(self) -> None:
        """Gracefully stop all monitors."""
        logger.info("Stopping all monitors...")
        for monitor in self._monitors.values():
            await monitor.stop()

        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("Monitor task stopped: %s", name)

        if self._client:
            await self._client.aclose()
            self._client = None

        self._tasks.clear()
        logger.info("All monitors stopped")

    def get_summaries(self) -> list[StatusSummary]:
        """Collect latest summaries from all monitors."""
        return [
            monitor.summary
            for monitor in self._monitors.values()
            if monitor.summary is not None
        ]

    def get_monitor(self, provider_name: str) -> Optional[StatusPageMonitor]:
        return self._monitors.get(provider_name)

    @property
    def monitor_count(self) -> int:
        return len(self._monitors)
