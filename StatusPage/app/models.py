"""
Domain models for status page events.

All models use Pydantic v2 for validation, serialization,
and automatic OpenAPI schema generation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ComponentStatusValue(str, Enum):
    """Possible statuses for a service component."""
    OPERATIONAL = "operational"
    DEGRADED_PERFORMANCE = "degraded_performance"
    PARTIAL_OUTAGE = "partial_outage"
    MAJOR_OUTAGE = "major_outage"
    UNDER_MAINTENANCE = "under_maintenance"


class IncidentImpact(str, Enum):
    """Severity level of an incident."""
    NONE = "none"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    """Lifecycle status of an incident."""
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    POSTMORTEM = "postmortem"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ComponentStatus(BaseModel):
    """A single service component (e.g. 'Chat Completions')."""
    id: str
    name: str
    status: ComponentStatusValue
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class IncidentUpdate(BaseModel):
    """One timeline entry within an incident."""
    id: str
    status: IncidentStatus
    body: str = ""
    created_at: datetime
    updated_at: Optional[datetime] = None


class Incident(BaseModel):
    """A full incident with all its timeline updates."""
    id: str
    name: str
    status: IncidentStatus
    impact: IncidentImpact = IncidentImpact.NONE
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    incident_updates: list[IncidentUpdate] = Field(default_factory=list)


class StatusEvent(BaseModel):
    """
    The canonical event emitted on the internal event bus
    whenever a status change is detected.
    """
    provider: str
    incident: Incident
    event_type: str = "incident_update"  # incident_update | new_incident | resolved
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def latest_update(self) -> Optional[IncidentUpdate]:
        """Return the most recent timeline entry."""
        if not self.incident.incident_updates:
            return None
        return max(self.incident.incident_updates, key=lambda u: u.created_at)


class StatusSummary(BaseModel):
    """Overall status snapshot for a single provider."""
    provider: str
    status_description: str
    components: list[ComponentStatus] = Field(default_factory=list)
    active_incidents: list[Incident] = Field(default_factory=list)
    last_checked: Optional[datetime] = None
