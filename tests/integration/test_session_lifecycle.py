"""Tests for the full session lifecycle.

Covers session creation, record processing, burn receipt generation,
session independence, audit logging, and summary statistics.
"""

from __future__ import annotations

import pytest

from chambers_sim.models.data_record import DataRecord, DataType
from chambers_sim.utils.local_gateway import BURN_LAYERS, LocalGateway


class TestSessionStart:
    """Session creation tests."""

    def test_session_start_returns_id(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """start_session returns a non-empty session ID string."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        assert isinstance(sid, str)
        assert len(sid) > 0
        assert "veh-001" in sid

    def test_session_start_unique_ids(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """Each session gets a unique ID."""
        sid1 = local_gateway.start_session("veh-001", demo_manifest)
        sid2 = local_gateway.start_session("veh-001", demo_manifest)
        assert sid1 != sid2


class TestRecordProcessing:
    """Record processing within a session."""

    def test_session_processes_records(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """Records are accepted and return a ProcessingResult."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid
        result = local_gateway.process_record(sid, sample_speed_record)

        assert result is not None
        # Speed is declared for OEM, so at least one transmitted
        assert len(result.records_transmitted) >= 1

    def test_process_record_unknown_session_raises(
        self,
        local_gateway: LocalGateway,
        sample_speed_record: DataRecord,
    ) -> None:
        """Processing a record for an unknown session raises ValueError."""
        with pytest.raises(ValueError, match="Unknown session"):
            local_gateway.process_record("nonexistent-session", sample_speed_record)

    def test_process_record_ended_session_raises(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """Processing a record after session end raises ValueError."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        local_gateway.end_session(sid)
        sample_speed_record.session_id = sid
        with pytest.raises(ValueError, match="Session already ended"):
            local_gateway.process_record(sid, sample_speed_record)


class TestSessionEnd:
    """Session termination and burn protocol."""

    def test_session_end_returns_burn_receipt(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """end_session returns a BurnReceipt with all 6 layers."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        receipt = local_gateway.end_session(sid)

        assert receipt.session_id == sid
        assert receipt.success is True
        assert len(receipt.layers_completed) == 6
        assert set(receipt.layers_completed) == set(BURN_LAYERS)

    def test_session_end_burns_all_keys(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """After burn, the session cannot process new records (data irrecoverable)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid
        local_gateway.process_record(sid, sample_speed_record)
        receipt = local_gateway.end_session(sid)

        assert receipt.success is True

        # Attempting to process after burn should fail
        with pytest.raises(ValueError, match="Session already ended"):
            local_gateway.process_record(sid, sample_speed_record)

    def test_burn_receipt_has_timestamp(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """Burn receipt contains a timestamp."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        receipt = local_gateway.end_session(sid)
        assert receipt.timestamp is not None


class TestSessionIndependence:
    """Multiple sessions must be independent."""

    def test_multiple_sessions_independent(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """Two vehicles' sessions do not share data or counters."""
        sid1 = local_gateway.start_session("veh-001", demo_manifest)
        sid2 = local_gateway.start_session("veh-002", demo_manifest)

        sample_speed_record.session_id = sid1
        local_gateway.process_record(sid1, sample_speed_record)

        sample_sensor_health_record.session_id = sid2
        local_gateway.process_record(sid2, sample_sensor_health_record)

        summary1 = local_gateway.get_session_summary(sid1)
        summary2 = local_gateway.get_session_summary(sid2)

        assert summary1.session_id != summary2.session_id
        assert summary1.records_generated == 1
        assert summary2.records_generated == 1


class TestAuditLog:
    """Audit log completeness."""

    def test_session_audit_log(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """Audit log contains session_start, record_processed, session_end, burn_complete."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid
        local_gateway.process_record(sid, sample_speed_record)
        local_gateway.end_session(sid)

        events = [e for e in local_gateway.audit_log if e.session_id == sid]
        event_types = [e.event_type for e in events]

        assert "session_start" in event_types
        assert "record_processed" in event_types
        assert "session_end" in event_types
        assert "burn_complete" in event_types

    def test_audit_log_events_have_timestamps(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """Every audit event has a timestamp."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        local_gateway.end_session(sid)

        for event in local_gateway.audit_log:
            assert event.timestamp is not None


class TestSessionSummary:
    """Session summary statistics."""

    def test_session_summary_counts(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Generated, transmitted, and blocked counts are consistent."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(50, session_id=sid)

        for record in records:
            local_gateway.process_record(sid, record)

        summary = local_gateway.get_session_summary(sid)
        assert summary.records_generated == 50
        # transmitted + blocked should reflect stakeholder-level decisions
        assert summary.records_transmitted > 0
        assert summary.records_blocked > 0
        # Total per-stakeholder counts should add up
        total_transmitted = sum(
            v.get("transmitted", 0) for v in summary.stakeholder_breakdown.values()
        )
        total_blocked = sum(
            v.get("blocked", 0) for v in summary.stakeholder_breakdown.values()
        )
        assert total_transmitted == summary.records_transmitted
        assert total_blocked == summary.records_blocked

    def test_session_summary_burned_after_end(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """After session end, records_burned equals records_generated."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(20, session_id=sid)
        for record in records:
            local_gateway.process_record(sid, record)
        local_gateway.end_session(sid)

        summary = local_gateway.get_session_summary(sid)
        assert summary.records_burned == summary.records_generated


class TestFullDriveSession:
    """Full drive session end-to-end within integration scope."""

    def test_full_drive_session(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        generate_drive_session,
    ) -> None:
        """Generate 1000 records, process all, end session, verify audit completeness."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        records = generate_drive_session(1000, session_id=sid)

        for record in records:
            local_gateway.process_record(sid, record)

        receipt = local_gateway.end_session(sid)
        summary = local_gateway.get_session_summary(sid)

        # All records processed
        assert summary.records_generated == 1000

        # Burn receipt complete
        assert receipt.success is True
        assert len(receipt.layers_completed) == 6

        # Audit log has all events
        session_events = [e for e in local_gateway.audit_log if e.session_id == sid]
        assert len(session_events) >= 1002  # 1 start + 1000 records + 1 end + 1 burn

        # Verify stakeholder breakdown exists
        assert len(summary.stakeholder_breakdown) > 0

        # Some records were transmitted, some blocked (due to mixed data types)
        assert summary.records_transmitted > 0
        assert summary.records_blocked > 0
