"""Pydantic models for mock stakeholder request/response payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request payloads
# ---------------------------------------------------------------------------

class TelemetryPayload(BaseModel):
    """Generic telemetry payload sent to OEM, Insurer, Tier-1 endpoints."""

    session_id: str = Field(..., description="UUID of the sealed drive session")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="ISO-8601 timestamp of the telemetry sample",
    )
    data_type: str = Field(
        ...,
        description="Category type, e.g. sensor_health, driving_behaviour, component_telemetry",
    )
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Key-value pairs of the telemetry data",
    )
    granularity: str = Field(
        default="raw",
        description="Granularity level: raw, anonymised, aggregated, per_trip_score",
    )
    source: str = Field(
        default="unknown",
        description="Source identifier (e.g. vehicle_id pseudonym)",
    )


class SealedEventPayload(BaseModel):
    """Sealed safety event sent to the ADAS supplier endpoint."""

    session_id: str = Field(..., description="UUID of the sealed drive session")
    trigger_type: str = Field(
        ...,
        description="Trigger classification, e.g. safety_critical, normal",
    )
    trigger_timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="Exact time the event was triggered",
    )
    window_start: datetime = Field(
        ...,
        description="Start of the captured temporal window (e.g. 5 s before trigger)",
    )
    window_end: datetime = Field(
        ...,
        description="End of the captured temporal window (e.g. 2 s after trigger)",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Sealed event payload (sensor snapshot within window)",
    )


# ---------------------------------------------------------------------------
# Response payloads
# ---------------------------------------------------------------------------

class AcceptResponse(BaseModel):
    """Standard acceptance response returned by stakeholder endpoints."""

    status: str = Field(default="accepted")
    stakeholder: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional extra information for the caller",
    )


class StatsResponse(BaseModel):
    """Counts of received payloads per stakeholder (admin endpoint)."""

    oem_count: int = 0
    insurer_count: int = 0
    adas_count: int = 0
    tier1_count: int = 0
    broker_count: int = 0
    foreign_count: int = 0
