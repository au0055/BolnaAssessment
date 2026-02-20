"""
ConsoleRenderer — subscribes to the event bus and pretty-prints
status events using the Rich library.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from app.event_bus import EventBus
from app.models import IncidentImpact, IncidentStatus, StatusEvent

logger = logging.getLogger(__name__)

# Color mapping for incident statuses
STATUS_COLORS: dict[IncidentStatus, str] = {
    IncidentStatus.INVESTIGATING: "yellow",
    IncidentStatus.IDENTIFIED: "dark_orange",
    IncidentStatus.MONITORING: "cyan",
    IncidentStatus.RESOLVED: "green",
    IncidentStatus.POSTMORTEM: "blue",
}

IMPACT_COLORS: dict[IncidentImpact, str] = {
    IncidentImpact.NONE: "dim",
    IncidentImpact.MINOR: "yellow",
    IncidentImpact.MAJOR: "red",
    IncidentImpact.CRITICAL: "bold red",
}

EVENT_TYPE_ICONS: dict[str, str] = {
    "new_incident": "🔴",
    "incident_update": "🔄",
    "resolved": "✅",
}

console = Console()


class ConsoleRenderer:
    """Subscribes to the event bus and renders events to the terminal."""

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._running = False

    async def start(self) -> None:
        """Begin listening for events and rendering them."""
        self._running = True
        logger.info("Console renderer started")

        console.print(
            Panel(
                "[bold cyan]Status Page Tracker[/bold cyan]\n"
                "[dim]Listening for status updates...[/dim]",
                border_style="cyan",
            )
        )

        async for event in self.event_bus.stream():
            if not self._running:
                break
            self._render_event(event)

    async def stop(self) -> None:
        self._running = False

    def _render_event(self, event: StatusEvent) -> None:
        """Format and print a single status event."""
        icon = EVENT_TYPE_ICONS.get(event.event_type, "📋")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # Build the status line
        status_color = STATUS_COLORS.get(event.incident.status, "white")
        impact_color = IMPACT_COLORS.get(event.incident.impact, "dim")

        # Get the latest update body
        latest = event.latest_update
        body = latest.body if latest and latest.body else event.incident.name

        # Formatted output matching the requested pattern
        output = Text()
        output.append(f"[{now}] ", style="dim")
        output.append(f"{icon} ", style="bold")
        output.append(f"Product: ", style="bold white")
        output.append(f"{event.provider} — ", style="bold cyan")
        output.append(f"{event.incident.name}\n", style="bold white")
        output.append(f"         Status: ", style="bold white")
        output.append(f"{event.incident.status.value}", style=f"bold {status_color}")
        output.append(f" | Impact: ", style="dim")
        output.append(f"{event.incident.impact.value}", style=impact_color)

        if body and body != event.incident.name:
            output.append(f"\n         Detail: ", style="bold white")
            output.append(body, style="white")

        console.print(output)
        console.print("─" * 80, style="dim")

        # Also print to standard format for non-rich terminals
        print(
            f"[{now}] Product: {event.provider} - {event.incident.name}\n"
            f"Status: {event.incident.status.value} "
            f"({'resolved' if event.event_type == 'resolved' else body})"
        )
