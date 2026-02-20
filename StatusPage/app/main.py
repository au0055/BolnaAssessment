"""
FastAPI application — entrypoint for the Status Page Tracker.

Endpoints:
    GET /          → health check
    GET /status    → current status summary (all providers)
    GET /incidents → recent incidents across all providers
    GET /events    → SSE stream of real-time status events
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.config import DEFAULT_PROVIDERS, settings
from app.console import ConsoleRenderer
from app.event_bus import EventBus
from app.models import StatusEvent
from app.registry import MonitorRegistry

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

event_bus = EventBus()
registry = MonitorRegistry(event_bus)
console_renderer = ConsoleRenderer(event_bus)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start monitors and console renderer on boot; stop on shutdown."""
    logger.info("🚀 Starting %s", settings.app_name)

    # Register all providers
    for provider in DEFAULT_PROVIDERS:
        if settings.poll_interval_override:
            provider.poll_interval_seconds = settings.poll_interval_override
        registry.register(provider)

    # Start monitors
    await registry.start_all()

    # Start console renderer as a background task
    console_task = asyncio.create_task(
        console_renderer.start(), name="console-renderer"
    )

    logger.info(
        "✅ %d monitors active — SSE at http://%s:%d/events",
        registry.monitor_count,
        settings.host,
        settings.port,
    )

    yield  # ← Application is running

    # Shutdown
    logger.info("🛑 Shutting down...")
    await console_renderer.stop()
    console_task.cancel()
    try:
        await console_task
    except asyncio.CancelledError:
        pass
    await registry.stop_all()
    logger.info("👋 Goodbye")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    description="Event-driven service status tracker with SSE streaming",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def health_check() -> str:
    """Health check with a minimal dashboard."""
    summaries = registry.get_summaries()
    rows = ""
    for s in summaries:
        active = len(s.active_incidents)
        badge = "🟢" if active == 0 else "🔴"
        rows += (
            f"<tr>"
            f"<td>{badge} {s.provider}</td>"
            f"<td>{s.status_description}</td>"
            f"<td>{active}</td>"
            f"<td>{s.last_checked.strftime('%H:%M:%S') if s.last_checked else '-'}</td>"
            f"</tr>"
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{settings.app_name}</title>
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; }}
            h1 {{ color: #58a6ff; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
            th, td {{ padding: 0.75rem 1rem; text-align: left; border-bottom: 1px solid #21262d; }}
            th {{ color: #8b949e; font-weight: 600; }}
            a {{ color: #58a6ff; }}
            .links {{ margin-top: 1.5rem; }}
            .links a {{ margin-right: 1.5rem; }}
        </style>
    </head>
    <body>
        <h1>⚡ {settings.app_name}</h1>
        <p>Tracking <strong>{registry.monitor_count}</strong> providers |
           <strong>{event_bus.subscriber_count}</strong> active subscribers</p>
        <table>
            <tr><th>Provider</th><th>Status</th><th>Active Incidents</th><th>Last Checked</th></tr>
            {rows}
        </table>
        <div class="links">
            <a href="/status">📊 /status</a>
            <a href="/incidents">📋 /incidents</a>
            <a href="/events">📡 /events (SSE)</a>
            <a href="/docs">📖 /docs</a>
        </div>
    </body>
    </html>
    """


@app.get("/status")
async def get_status() -> list[dict]:
    """Current status summary for all tracked providers."""
    summaries = registry.get_summaries()
    return [s.model_dump(mode="json") for s in summaries]


@app.get("/incidents")
async def get_incidents() -> list[dict]:
    """Recent incidents across all providers."""
    result = []
    for summary in registry.get_summaries():
        for inc in summary.active_incidents:
            result.append(
                {
                    "provider": summary.provider,
                    **inc.model_dump(mode="json"),
                }
            )
    return result


@app.get("/events")
async def sse_events(request: Request) -> EventSourceResponse:
    """
    Server-Sent Events stream of real-time status updates.

    Connect with:
        curl -N http://localhost:8000/events
        new EventSource("http://localhost:8000/events")
    """

    async def event_generator() -> AsyncIterator[dict]:
        async for event in event_bus.stream():
            if await request.is_disconnected():
                break
            yield {
                "event": event.event_type,
                "data": event.model_dump_json(),
                "id": event.incident.id,
            }

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
