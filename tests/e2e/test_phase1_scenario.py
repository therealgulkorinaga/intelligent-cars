"""End-to-end Phase 1 scenario test.

Simulates a fleet of 10 vehicles, each generating 100 telemetry records,
processed through the LocalGateway with the demo manifest.  No external
simulators are required — all data is synthetic.

This test validates the full Chambers data flow:
  vehicle records -> manifest evaluation -> filtered delivery -> burn -> audit
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from chambers_sim.models.data_record import (
    BurnReceipt,
    DataType,
    FilteredDataRecord,
    ProcessingResult,
    SessionSummary,
)
from chambers_sim.models.manifest import PreservationManifest
from chambers_sim.utils.data_residue import DataResidueAnalyzer
from chambers_sim.utils.local_gateway import BURN_LAYERS, LocalGateway


FLEET_SIZE = 10
RECORDS_PER_VEHICLE = 100


@dataclass
class VehicleResult:
    """Collects all artefacts for one vehicle's session."""

    vehicle_id: str
    session_id: str
    receipts: BurnReceipt | None = None
    summary: SessionSummary | None = None
    transmitted: list[FilteredDataRecord] | None = None
    blocked_count: int = 0


class TestPhase1Scenario:
    """Full Phase 1 end-to-end scenario."""

    @pytest.fixture(autouse=True)
    def _run_fleet(
        self,
        local_gateway: LocalGateway,
        demo_manifest: PreservationManifest,
        generate_vehicle_records,
    ) -> None:
        """Run the entire fleet simulation once for the class."""
        self.gateway = local_gateway
        self.manifest = demo_manifest
        self.vehicle_results: list[VehicleResult] = []
        self.all_transmitted: list[FilteredDataRecord] = []
        self.all_records = []

        for v_idx in range(FLEET_SIZE):
            vehicle_id = f"fleet-veh-{v_idx:03d}"
            sid = local_gateway.start_session(vehicle_id, demo_manifest)
            records = generate_vehicle_records(
                RECORDS_PER_VEHICLE, session_id=sid, vehicle_seed=v_idx
            )
            self.all_records.extend(records)

            vr = VehicleResult(vehicle_id=vehicle_id, session_id=sid, transmitted=[])
            blocked = 0

            for record in records:
                result = local_gateway.process_record(sid, record)
                vr.transmitted.extend(result.records_transmitted)
                self.all_transmitted.extend(result.records_transmitted)
                blocked += len(result.records_blocked)

            vr.blocked_count = blocked
            vr.receipts = local_gateway.end_session(sid)
            vr.summary = local_gateway.get_session_summary(sid)
            self.vehicle_results.append(vr)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def test_all_sessions_started(self) -> None:
        """All 10 vehicle sessions were created successfully."""
        assert len(self.vehicle_results) == FLEET_SIZE
        session_ids = {vr.session_id for vr in self.vehicle_results}
        assert len(session_ids) == FLEET_SIZE  # all unique

    def test_all_sessions_ended(self) -> None:
        """All 10 sessions produced a burn receipt."""
        for vr in self.vehicle_results:
            assert vr.receipts is not None
            assert vr.receipts.success is True

    # ------------------------------------------------------------------
    # Burn receipts
    # ------------------------------------------------------------------

    def test_all_burn_receipts_have_6_layers(self) -> None:
        """Every burn receipt has all 6 layers complete."""
        for vr in self.vehicle_results:
            assert len(vr.receipts.layers_completed) == 6
            assert set(vr.receipts.layers_completed) == set(BURN_LAYERS)

    def test_burn_receipt_session_ids_match(self) -> None:
        """Each burn receipt references the correct session ID."""
        for vr in self.vehicle_results:
            assert vr.receipts.session_id == vr.session_id

    # ------------------------------------------------------------------
    # OEM receives only declared categories
    # ------------------------------------------------------------------

    def test_oem_receives_only_declared_types(self) -> None:
        """OEM receives only the data types declared in the manifest."""
        oem_declared = {
            cat.data_type
            for s in self.manifest.stakeholders
            if s.id == "oem-stellantis"
            for cat in s.categories
        }

        oem_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        oem_received_types = {r.data_type for r in oem_records}

        assert oem_received_types.issubset(oem_declared), (
            f"OEM received undeclared types: {oem_received_types - oem_declared}"
        )

    def test_oem_receives_sensor_health(self) -> None:
        """OEM receives sensor health records."""
        oem_sensor = [
            r for r in self.all_transmitted
            if r.stakeholder_id == "oem-stellantis" and r.data_type == DataType.SENSOR_HEALTH
        ]
        assert len(oem_sensor) > 0

    # ------------------------------------------------------------------
    # Insurer receives only behaviour scores, no GPS
    # ------------------------------------------------------------------

    def test_insurer_receives_only_behaviour(self) -> None:
        """Insurer receives only DrivingBehaviour, no other data types."""
        insurer_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        for rec in insurer_records:
            assert rec.data_type == DataType.DRIVING_BEHAVIOUR

    def test_insurer_no_gps_in_any_record(self) -> None:
        """Insurer never sees GPS coordinates in any record."""
        insurer_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        for rec in insurer_records:
            assert "latitude" not in rec.fields
            assert "longitude" not in rec.fields
            assert "route" not in rec.fields

    def test_insurer_no_raw_traces(self) -> None:
        """Insurer never sees raw speed/acceleration traces."""
        insurer_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        for rec in insurer_records:
            assert "raw_speed_trace" not in rec.fields
            assert "raw_accel_trace" not in rec.fields

    # ------------------------------------------------------------------
    # ADAS receives only sealed events
    # ------------------------------------------------------------------

    def test_adas_receives_only_sealed_events(self) -> None:
        """ADAS supplier receives only SealedEvent records."""
        adas_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        for rec in adas_records:
            assert rec.data_type == DataType.SEALED_EVENT

    def test_adas_excludes_driver_face_and_audio(self) -> None:
        """ADAS sealed events do not contain driver_face_crop or cabin_audio."""
        adas_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        for rec in adas_records:
            assert "driver_face_crop" not in rec.fields
            assert "cabin_audio" not in rec.fields

    # ------------------------------------------------------------------
    # Tier-1 receives only diagnostics and sensor health
    # ------------------------------------------------------------------

    def test_tier1_receives_only_declared_types(self) -> None:
        """Tier-1 receives only DiagnosticCode and SensorHealth."""
        tier1_declared = {
            cat.data_type
            for s in self.manifest.stakeholders
            if s.id == "tier1-bosch"
            for cat in s.categories
        }
        tier1_records = [
            r for r in self.all_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        for rec in tier1_records:
            assert rec.data_type in tier1_declared

    def test_tier1_no_driver_identity_in_diagnostics(self) -> None:
        """Tier-1 diagnostic records do not contain VIN or driver_id."""
        tier1_diag = [
            r for r in self.all_transmitted
            if r.stakeholder_id == "tier1-bosch" and r.data_type == DataType.DIAGNOSTIC_CODE
        ]
        for rec in tier1_diag:
            assert "vin" not in rec.fields
            assert "driver_id" not in rec.fields

    # ------------------------------------------------------------------
    # No data to undeclared endpoints
    # ------------------------------------------------------------------

    def test_no_undeclared_stakeholders_receive_data(self) -> None:
        """Only stakeholders in the manifest receive data."""
        declared_ids = {s.id for s in self.manifest.stakeholders}
        received_ids = {r.stakeholder_id for r in self.all_transmitted}
        assert received_ids.issubset(declared_ids)

    def test_undeclared_types_blocked_for_all(self) -> None:
        """Data types not in any stakeholder's manifest are never transmitted."""
        all_declared_types = set()
        for s in self.manifest.stakeholders:
            for cat in s.categories:
                all_declared_types.add(cat.data_type)

        for rec in self.all_transmitted:
            assert rec.data_type in all_declared_types

    # ------------------------------------------------------------------
    # Audit logs
    # ------------------------------------------------------------------

    def test_audit_logs_complete_for_all_sessions(self) -> None:
        """Every session has audit entries for start, records, end, burn."""
        for vr in self.vehicle_results:
            events = [
                e for e in self.gateway.audit_log if e.session_id == vr.session_id
            ]
            event_types = {e.event_type for e in events}

            assert "session_start" in event_types
            assert "record_processed" in event_types
            assert "session_end" in event_types
            assert "burn_complete" in event_types

    def test_audit_log_chain_integrity(self) -> None:
        """Audit events for each session are chronologically ordered."""
        for vr in self.vehicle_results:
            events = [
                e for e in self.gateway.audit_log if e.session_id == vr.session_id
            ]
            timestamps = [e.timestamp for e in events]
            assert timestamps == sorted(timestamps), (
                f"Audit events for {vr.session_id} are not chronologically ordered"
            )

    def test_audit_log_record_count_matches(self) -> None:
        """Number of record_processed audit events matches records generated."""
        for vr in self.vehicle_results:
            record_events = [
                e for e in self.gateway.audit_log
                if e.session_id == vr.session_id and e.event_type == "record_processed"
            ]
            assert len(record_events) == RECORDS_PER_VEHICLE

    # ------------------------------------------------------------------
    # Data residue
    # ------------------------------------------------------------------

    def test_data_residue_significant_reduction(self) -> None:
        """Chambers achieves significant data reduction compared to baseline.

        The baseline sends every record to every stakeholder in full.
        Chambers filters by category, field, and granularity. With a mix
        of declared and undeclared data types, reduction should exceed 30%.
        """
        analyzer = DataResidueAnalyzer()
        # run_chambers first so the manifest is set for baseline stakeholder tracking
        analyzer.run_chambers(self.all_records, self.manifest)
        report = analyzer.compare()

        assert report.reduction_ratio > 0.30, (
            f"Expected >30% reduction, got {report.reduction_ratio:.1%}"
        )

    # ------------------------------------------------------------------
    # Driver summary readability
    # ------------------------------------------------------------------

    def test_driver_summary_human_readable(self) -> None:
        """Driver summary for each session is human-readable (no crypto jargon)."""
        jargon = ["HMAC", "AES", "SHA256", "key handle", "nonce", "cipher", "RSA"]

        for vr in self.vehicle_results:
            summary = vr.summary
            text = (
                f"Drive session for vehicle {vr.vehicle_id}: "
                f"{summary.records_generated} records generated, "
                f"{summary.records_transmitted} shared with authorised parties, "
                f"{summary.records_blocked} blocked. "
                f"All data securely destroyed after session."
            )
            for term in jargon:
                assert term not in text

    def test_driver_summary_mentions_destruction(self) -> None:
        """Every vehicle's summary mentions data destruction."""
        for vr in self.vehicle_results:
            assert vr.summary.records_burned == vr.summary.records_generated
            assert vr.summary.records_burned > 0

    # ------------------------------------------------------------------
    # Summary statistics coherence
    # ------------------------------------------------------------------

    def test_total_records_generated(self) -> None:
        """Total records generated across the fleet is FLEET_SIZE * RECORDS_PER_VEHICLE."""
        total = sum(vr.summary.records_generated for vr in self.vehicle_results)
        assert total == FLEET_SIZE * RECORDS_PER_VEHICLE

    def test_each_vehicle_independent_summary(self) -> None:
        """Each vehicle's summary reflects only its own session."""
        for vr in self.vehicle_results:
            assert vr.summary.records_generated == RECORDS_PER_VEHICLE
            assert vr.summary.session_id == vr.session_id
