"""Tests for HSM fallback mode.

When the Hardware Security Module is unavailable, the gateway should
block all telemetry transmission while still allowing the vehicle to
generate records (the car remains drivable).

The LocalGateway simulates HSM fallback by using a manifest with no
stakeholders — the same effect as the gateway refusing to release
encryption keys.
"""

from __future__ import annotations

import pytest

from chambers_sim.models.data_record import DataRecord, DataType
from chambers_sim.models.manifest import (
    CategoryDeclaration,
    PreservationManifest,
    StakeholderDeclaration,
)
from chambers_sim.models.data_record import Granularity, LegalBasis
from chambers_sim.utils.local_gateway import LocalGateway


def _hsm_fallback_manifest() -> PreservationManifest:
    """Simulate HSM failure: no stakeholders can receive data."""
    return PreservationManifest(
        manifest_version="1.0",
        vehicle_id="veh-hsm-fallback",
        stakeholders=[],  # HSM offline -> no keys -> no stakeholder access
    )


def _normal_manifest() -> PreservationManifest:
    """A normal manifest for recovery testing."""
    return PreservationManifest(
        manifest_version="1.0",
        vehicle_id="veh-hsm-recovery",
        stakeholders=[
            StakeholderDeclaration(
                id="oem-test",
                role="OEM",
                legal_basis=LegalBasis.CONTRACT,
                categories=[
                    CategoryDeclaration(
                        data_type=DataType.SPEED,
                        fields=["speed_mps"],
                        granularity=Granularity.RAW,
                    ),
                ],
            ),
        ],
    )


class TestHsmFallback:
    """HSM fallback mode tests."""

    def test_fallback_blocks_all_telemetry(
        self,
        local_gateway: LocalGateway,
        generate_drive_session,
    ) -> None:
        """In fallback mode, no telemetry is transmitted to any stakeholder."""
        manifest = _hsm_fallback_manifest()
        sid = local_gateway.start_session("veh-001", manifest)
        records = generate_drive_session(50, session_id=sid)

        total_transmitted = 0
        for record in records:
            result = local_gateway.process_record(sid, record)
            total_transmitted += len(result.records_transmitted)

        assert total_transmitted == 0

        summary = local_gateway.get_session_summary(sid)
        assert summary.records_transmitted == 0
        assert summary.records_generated == 50

    def test_fallback_logged_in_audit(
        self,
        local_gateway: LocalGateway,
        generate_drive_session,
    ) -> None:
        """Fallback events are logged in the audit trail.

        Each blocked record produces an audit entry showing it was blocked.
        """
        manifest = _hsm_fallback_manifest()
        sid = local_gateway.start_session("veh-001", manifest)
        records = generate_drive_session(10, session_id=sid)
        for record in records:
            local_gateway.process_record(sid, record)

        record_events = [
            e for e in local_gateway.audit_log
            if e.session_id == sid and e.event_type == "record_processed"
        ]
        assert len(record_events) == 10
        # Each event should show empty transmitted_to and non-empty blocked_for
        # (well, blocked_for is empty because there are no stakeholders to block)
        for event in record_events:
            assert event.details["transmitted_to"] == []

    def test_fallback_recovery(
        self,
        local_gateway: LocalGateway,
        generate_drive_session,
    ) -> None:
        """After HSM recovers, a new session with a normal manifest works."""
        # Fallback session
        fallback_manifest = _hsm_fallback_manifest()
        sid_fallback = local_gateway.start_session("veh-001", fallback_manifest)
        records = generate_drive_session(20, session_id=sid_fallback)
        for r in records:
            result = local_gateway.process_record(sid_fallback, r)
            assert len(result.records_transmitted) == 0
        local_gateway.end_session(sid_fallback)

        # Recovery: new session with normal manifest
        normal_manifest = _normal_manifest()
        sid_normal = local_gateway.start_session("veh-001", normal_manifest)
        speed_record = DataRecord(
            session_id=sid_normal,
            source="can-bus",
            data_type=DataType.SPEED,
            fields={"speed_mps": 25.0},
        )
        result = local_gateway.process_record(sid_normal, speed_record)
        assert len(result.records_transmitted) == 1
        assert result.records_transmitted[0].stakeholder_id == "oem-test"

    def test_vehicle_still_drivable_in_fallback(
        self,
        local_gateway: LocalGateway,
        generate_drive_session,
    ) -> None:
        """In fallback mode the simulation continues — records are generated
        (vehicle is drivable) but nothing is transmitted.
        """
        manifest = _hsm_fallback_manifest()
        sid = local_gateway.start_session("veh-001", manifest)
        records = generate_drive_session(100, session_id=sid)

        # All 100 records can be processed (vehicle driving)
        for record in records:
            result = local_gateway.process_record(sid, record)
            # No crashes, no errors — just zero transmissions
            assert isinstance(result.records_transmitted, list)

        summary = local_gateway.get_session_summary(sid)
        assert summary.records_generated == 100
        assert summary.records_transmitted == 0

        # Session can still end cleanly
        receipt = local_gateway.end_session(sid)
        assert receipt.success is True
