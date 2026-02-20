"""
StatusPageMonitor — async polling engine with conditional HTTP.

Uses ETag / If-Modified-Since headers to minimize bandwidth.
Falls back to content hashing for deduplication.
Detects new and updated incidents by diffing against previous state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.event_bus import EventBus
from app.models import (
    ComponentStatus,
    ComponentStatusValue,
    Incident,
    IncidentImpact,
    IncidentStatus,
    IncidentUpdate,
    StatusEvent,
    StatusSummary,
)

logger = logging.getLogger(__name__)


class ProviderConfig:
    """Configuration for a single status-page provider."""

    def __init__(
        self,
        name: str,
        base_url: str,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.poll_interval_seconds = poll_interval_seconds

    @property
    def incidents_url(self) -> str:
        return f"{self.base_url}/incidents.json"

    @property
    def summary_url(self) -> str:
        return f"{self.base_url}/summary.json"


class StatusPageMonitor:
    """
    Monitors a single provider's status page.

    Runs as a long-lived asyncio coroutine.  On each tick it:
      1. Fetches /incidents.json with conditional headers
      2. Skips processing on 304 Not Modified
      3. Hashes the body as secondary dedup
      4. Diffs current vs previous incidents
      5. Publishes StatusEvent(s) for any changes
    """

    def __init__(
        self,
        config: ProviderConfig,
        event_bus: EventBus,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self._client = client
        self._owns_client = client is None

        # Conditional-request state
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._last_hash: Optional[str] = None

        # Previous incident state for diffing
        self._known_incidents: dict[str, Incident] = {}
        self._known_update_ids: set[str] = set()

        # Latest summary cache
        self._summary: Optional[StatusSummary] = None

        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Run the monitor loop indefinitely."""
        if self._owns_client:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                http2=True,
            )
        self._running = True
        logger.info(
            "[%s] Monitor started — polling every %.0fs",
            self.config.name,
            self.config.poll_interval_seconds,
        )
        try:
            while self._running:
                try:
                    await self._tick()
                except httpx.HTTPError as exc:
                    logger.error("[%s] HTTP error: %s", self.config.name, exc)
                except Exception:
                    logger.exception("[%s] Unexpected error in monitor tick", self.config.name)
                await asyncio.sleep(self.config.poll_interval_seconds)
        finally:
            if self._owns_client and self._client:
                await self._client.aclose()

    async def stop(self) -> None:
        """Signal the monitor to stop after the current tick."""
        self._running = False
        logger.info("[%s] Monitor stopping", self.config.name)

    @property
    def summary(self) -> Optional[StatusSummary]:
        return self._summary

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """One poll cycle."""
        assert self._client is not None

        # --- Fetch incidents with conditional headers ---
        headers: dict[str, str] = {}
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        resp = await self._client.get(self.config.incidents_url, headers=headers)

        if resp.status_code == 304:
            logger.debug("[%s] 304 Not Modified — skipping", self.config.name)
            return

        resp.raise_for_status()

        # Update conditional-request tokens
        self._etag = resp.headers.get("etag")
        self._last_modified = resp.headers.get("last-modified")

        # Content-hash dedup
        body_bytes = resp.content
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        if body_hash == self._last_hash:
            logger.debug("[%s] Content hash unchanged — skipping", self.config.name)
            return
        self._last_hash = body_hash

        # --- Parse & diff ---
        data = resp.json()
        raw_incidents: list[dict] = data.get("incidents", [])
        current_incidents = self._parse_incidents(raw_incidents)

        await self._diff_and_publish(current_incidents)

        # Update summary cache
        await self._fetch_summary()

    def _parse_incidents(self, raw: list[dict]) -> dict[str, Incident]:
        """Parse raw JSON into Incident models, keyed by ID."""
        result: dict[str, Incident] = {}
        for item in raw:
            try:
                updates = [
                    IncidentUpdate(
                        id=u["id"],
                        status=IncidentStatus(u.get("status", "investigating")),
                        body=u.get("body", "") or "",
                        created_at=u["created_at"],
                        updated_at=u.get("updated_at"),
                    )
                    for u in item.get("incident_updates", [])
                ]
                incident = Incident(
                    id=item["id"],
                    name=item["name"],
                    status=IncidentStatus(item.get("status", "investigating")),
                    impact=IncidentImpact(item.get("impact", "none")),
                    created_at=item["created_at"],
                    updated_at=item.get("updated_at"),
                    resolved_at=item.get("resolved_at"),
                    incident_updates=updates,
                )
                result[incident.id] = incident
            except Exception:
                logger.exception("Failed to parse incident: %s", item.get("id", "?"))
        return result

    async def _diff_and_publish(self, current: dict[str, Incident]) -> None:
        """Detect new/updated incidents and emit events."""
        now = datetime.now(timezone.utc)

        for inc_id, incident in current.items():
            # Collect all update IDs for this incident
            current_update_ids = {u.id for u in incident.incident_updates}

            if inc_id not in self._known_incidents:
                # Brand-new incident
                event = StatusEvent(
                    provider=self.config.name,
                    incident=incident,
                    event_type="new_incident",
                    detected_at=now,
                )
                await self.event_bus.publish(event)
                self._known_update_ids |= current_update_ids
                logger.info(
                    "[%s] NEW incident: %s (%s)",
                    self.config.name,
                    incident.name,
                    incident.status.value,
                )
            else:
                # Check for new updates within an existing incident
                new_update_ids = current_update_ids - self._known_update_ids
                if new_update_ids:
                    event = StatusEvent(
                        provider=self.config.name,
                        incident=incident,
                        event_type="incident_update",
                        detected_at=now,
                    )
                    await self.event_bus.publish(event)
                    self._known_update_ids |= new_update_ids
                    logger.info(
                        "[%s] UPDATED incident: %s → %s",
                        self.config.name,
                        incident.name,
                        incident.status.value,
                    )

        # Detect newly resolved incidents
        for inc_id, old_incident in self._known_incidents.items():
            if inc_id in current:
                new_incident = current[inc_id]
                if (
                    old_incident.status != IncidentStatus.RESOLVED
                    and new_incident.status == IncidentStatus.RESOLVED
                ):
                    event = StatusEvent(
                        provider=self.config.name,
                        incident=new_incident,
                        event_type="resolved",
                        detected_at=now,
                    )
                    await self.event_bus.publish(event)
                    logger.info(
                        "[%s] RESOLVED incident: %s",
                        self.config.name,
                        new_incident.name,
                    )

        self._known_incidents = current

    async def _fetch_summary(self) -> None:
        """Fetch and cache the provider summary."""
        assert self._client is not None
        try:
            resp = await self._client.get(self.config.summary_url)
            resp.raise_for_status()
            data = resp.json()

            components = []
            for c in data.get("components", []):
                try:
                    components.append(
                        ComponentStatus(
                            id=c["id"],
                            name=c["name"],
                            status=ComponentStatusValue(c.get("status", "operational")),
                            created_at=c.get("created_at"),
                            updated_at=c.get("updated_at"),
                        )
                    )
                except Exception:
                    pass

            status_info = data.get("status", {})
            active = [
                inc
                for inc in self._known_incidents.values()
                if inc.status != IncidentStatus.RESOLVED
            ]

            self._summary = StatusSummary(
                provider=self.config.name,
                status_description=status_info.get("description", "Unknown"),
                components=components,
                active_incidents=active,
                last_checked=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("[%s] Failed to fetch summary", self.config.name)
