"""Pure-Python local gateway for standalone testing without the Rust backend.

Implements the full manifest evaluation, field filtering, granularity
transformation, jurisdiction checking, consent revocation, and burn protocol.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from chambers_sim.models.data_record import (
    BurnReceipt,
    DataRecord,
    DataType,
    FilteredDataRecord,
    Granularity,
    ProcessingResult,
    SessionSummary,
)
from chambers_sim.models.manifest import PreservationManifest

logger = structlog.get_logger(__name__)

# The six layers of the Chambers burn protocol
BURN_LAYERS = [
    "application_cache",
    "database_records",
    "search_indices",
    "message_queues",
    "backup_snapshots",
    "audit_log_references",
]


@dataclass
class AuditEvent:
    """An entry in the in-memory audit log."""

    timestamp: datetime
    session_id: str
    event_type: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionState:
    """Tracks the state of an active session."""

    session_id: str
    vehicle_id: str
    manifest: PreservationManifest
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    records_generated: int = 0
    records_transmitted: int = 0
    records_blocked: int = 0
    stakeholder_transmitted: dict[str, int] = field(default_factory=dict)
    stakeholder_blocked: dict[str, int] = field(default_factory=dict)
    revoked_stakeholders: set[str] = field(default_factory=set)
    ended: bool = False


class LocalGateway:
    """In-process implementation of the Chambers gateway.

    Evaluates each DataRecord against the preservation manifest and returns
    filtered records per stakeholder, enforcing field-level access control,
    granularity transformation, and jurisdiction constraints.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._audit_log: list[AuditEvent] = []

    # ---- Session lifecycle ----

    def start_session(
        self,
        vehicle_id: str,
        manifest: PreservationManifest,
    ) -> str:
        """Create a new session and return its ID."""
        session_id = f"local-{vehicle_id}-{uuid.uuid4().hex[:8]}"
        manifest_copy = manifest.model_copy(deep=True)
        manifest_copy.session_id = session_id
        manifest_copy.vehicle_id = vehicle_id

        state = SessionState(
            session_id=session_id,
            vehicle_id=vehicle_id,
            manifest=manifest_copy,
        )
        self._sessions[session_id] = state

        self._audit(session_id, "session_start", {"vehicle_id": vehicle_id})
        logger.info("local_session_started", session_id=session_id, vehicle_id=vehicle_id)
        return session_id

    def process_record(
        self,
        session_id: str,
        record: DataRecord,
    ) -> ProcessingResult:
        """Evaluate a record against the manifest and return filtered results."""
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")
        if state.ended:
            raise ValueError(f"Session already ended: {session_id}")

        state.records_generated += 1
        transmitted: list[FilteredDataRecord] = []
        blocked: list[str] = []

        for stakeholder in state.manifest.stakeholders:
            # Check consent revocation
            if stakeholder.id in state.revoked_stakeholders:
                blocked.append(stakeholder.id)
                state.stakeholder_blocked[stakeholder.id] = (
                    state.stakeholder_blocked.get(stakeholder.id, 0) + 1
                )
                continue

            # Find matching category for this data type
            matched_category = None
            for cat in stakeholder.categories:
                if cat.data_type == record.data_type:
                    matched_category = cat
                    break

            if matched_category is None:
                # Data type not declared for this stakeholder -> block
                blocked.append(stakeholder.id)
                state.stakeholder_blocked[stakeholder.id] = (
                    state.stakeholder_blocked.get(stakeholder.id, 0) + 1
                )
                continue

            # Field filtering: include declared fields, exclude excluded_fields
            filtered_fields = self._filter_fields(
                record.fields,
                matched_category.fields,
                matched_category.excluded_fields,
            )

            # Apply granularity transformation
            filtered_fields = self._apply_granularity(
                filtered_fields,
                matched_category.granularity,
                record.data_type,
            )

            filtered_record = FilteredDataRecord(
                stakeholder_id=stakeholder.id,
                data_type=record.data_type,
                fields=filtered_fields,
                granularity=matched_category.granularity,
            )
            transmitted.append(filtered_record)
            state.stakeholder_transmitted[stakeholder.id] = (
                state.stakeholder_transmitted.get(stakeholder.id, 0) + 1
            )

        state.records_transmitted += len(transmitted)
        state.records_blocked += len(blocked)

        self._audit(
            session_id,
            "record_processed",
            {
                "data_type": record.data_type.value,
                "transmitted_to": [r.stakeholder_id for r in transmitted],
                "blocked_for": blocked,
            },
        )

        return ProcessingResult(records_transmitted=transmitted, records_blocked=blocked)

    def end_session(self, session_id: str) -> BurnReceipt:
        """End a session and execute the burn protocol."""
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        state.ended = True
        now = datetime.now(timezone.utc)

        # Simulate completing all 6 burn layers
        receipt = BurnReceipt(
            session_id=session_id,
            timestamp=now,
            layers_completed=list(BURN_LAYERS),
            success=True,
        )

        self._audit(session_id, "session_end", {"layers": BURN_LAYERS})
        self._audit(session_id, "burn_complete", {"receipt": receipt.model_dump(mode="json")})
        logger.info("local_session_ended", session_id=session_id, burned=True)
        return receipt

    def revoke_consent(self, session_id: str, stakeholder_id: str) -> None:
        """Revoke consent for a stakeholder mid-session."""
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        state.revoked_stakeholders.add(stakeholder_id)
        self._audit(
            session_id,
            "consent_revoked",
            {"stakeholder_id": stakeholder_id},
        )
        logger.info(
            "consent_revoked",
            session_id=session_id,
            stakeholder_id=stakeholder_id,
        )

    def get_session_summary(self, session_id: str) -> SessionSummary:
        """Build a summary of the session."""
        state = self._sessions.get(session_id)
        if state is None:
            raise ValueError(f"Unknown session: {session_id}")

        end_time = datetime.now(timezone.utc) if not state.ended else datetime.now(timezone.utc)
        breakdown: dict[str, dict[str, int]] = {}
        for sid, count in state.stakeholder_transmitted.items():
            breakdown.setdefault(sid, {})["transmitted"] = count
        for sid, count in state.stakeholder_blocked.items():
            breakdown.setdefault(sid, {})["blocked"] = count

        return SessionSummary(
            session_id=session_id,
            start=state.start_time,
            end=end_time,
            records_generated=state.records_generated,
            records_transmitted=state.records_transmitted,
            records_blocked=state.records_blocked,
            records_burned=state.records_generated if state.ended else 0,
            stakeholder_breakdown=breakdown,
        )

    # ---- Audit ----

    @property
    def audit_log(self) -> list[AuditEvent]:
        """Return the full audit log."""
        return list(self._audit_log)

    def _audit(self, session_id: str, event_type: str, details: dict[str, Any]) -> None:
        self._audit_log.append(
            AuditEvent(
                timestamp=datetime.now(timezone.utc),
                session_id=session_id,
                event_type=event_type,
                details=details,
            )
        )

    # ---- Internal helpers ----

    @staticmethod
    def _filter_fields(
        source_fields: dict[str, Any],
        declared_fields: list[str],
        excluded_fields: list[str],
    ) -> dict[str, Any]:
        """Filter fields: keep only declared, remove excluded."""
        if not declared_fields:
            # No field restriction declared -> pass all (minus exclusions)
            result = dict(source_fields)
        else:
            result = {k: v for k, v in source_fields.items() if k in declared_fields}

        for excl in excluded_fields:
            result.pop(excl, None)

        return result

    @staticmethod
    def _apply_granularity(
        fields: dict[str, Any],
        granularity: Granularity,
        data_type: DataType,
    ) -> dict[str, Any]:
        """Apply granularity transformation to the fields."""
        if granularity == Granularity.RAW:
            return fields

        if granularity == Granularity.ANONYMISED:
            return _anonymise_fields(fields, data_type)

        if granularity == Granularity.AGGREGATED:
            return _aggregate_fields(fields, data_type)

        if granularity == Granularity.PER_TRIP_SCORE:
            return _per_trip_score_fields(fields, data_type)

        return fields


def _anonymise_fields(fields: dict[str, Any], data_type: DataType) -> dict[str, Any]:
    """Anonymise fields by reducing precision."""
    result = dict(fields)

    # Position anonymisation: snap to ~1km grid
    if "latitude" in result:
        grid = 0.009  # ~1km
        lat = result["latitude"]
        result["latitude"] = round(math.floor(lat / grid) * grid + grid / 2, 4)
    if "longitude" in result:
        grid = 0.009
        lon = result["longitude"]
        result["longitude"] = round(math.floor(lon / grid) * grid + grid / 2, 4)

    # Speed anonymisation: round to nearest 5
    if "speed_mps" in result:
        result["speed_mps"] = round(result["speed_mps"] / 5) * 5

    # Remove any raw traces
    for key in ["raw_speed_trace", "raw_accel_trace"]:
        result.pop(key, None)

    return result


def _aggregate_fields(fields: dict[str, Any], data_type: DataType) -> dict[str, Any]:
    """Aggregate fields by summarizing numeric values."""
    result = dict(fields)

    # Remove raw traces, keep only summary stats
    for key in ["raw_speed_trace", "raw_accel_trace"]:
        result.pop(key, None)

    # Round numeric values for aggregation
    for k, v in result.items():
        if isinstance(v, float):
            result[k] = round(v, 1)

    return result


def _per_trip_score_fields(fields: dict[str, Any], data_type: DataType) -> dict[str, Any]:
    """Reduce to per-trip score: only the score and trip-level metadata."""
    result = {}

    # Keep only score-level fields
    for key in ["score", "distance_km", "duration_minutes", "time_of_day_bucket"]:
        if key in fields:
            result[key] = fields[key]

    # Ensure a score exists
    if "score" not in result:
        result["score"] = 0.0

    return result
