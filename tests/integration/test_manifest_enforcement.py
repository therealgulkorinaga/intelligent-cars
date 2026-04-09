"""Tests for core manifest evaluation logic.

Verifies that each stakeholder receives only the data categories and
fields declared in the preservation manifest, and that undeclared types
and excluded fields are blocked.
"""

from __future__ import annotations

import pytest

from chambers_sim.models.data_record import DataRecord, DataType, FilteredDataRecord, Granularity
from chambers_sim.utils.local_gateway import LocalGateway


class TestOemAccess:
    """OEM stakeholder access constraints."""

    def test_oem_receives_sensor_health(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """OEM stakeholder receives sensor_health data at the declared granularity."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sensor_health_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sensor_health_record)

        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1
        assert oem_records[0].data_type == DataType.SENSOR_HEALTH
        # OEM sensor_health is declared as RAW granularity in demo manifest
        assert oem_records[0].granularity == Granularity.RAW

    def test_oem_receives_position_anonymised(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_position_record: DataRecord,
    ) -> None:
        """OEM receives position data but at anonymised granularity (grid-snapped)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_position_record.session_id = sid
        result = local_gateway.process_record(sid, sample_position_record)

        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1
        assert oem_records[0].granularity == Granularity.ANONYMISED
        # Anonymised GPS should be grid-snapped, not raw
        assert oem_records[0].fields["latitude"] != 48.8566

    def test_oem_blocked_from_contact_sync(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_contact_sync_record: DataRecord,
    ) -> None:
        """OEM does not receive contact sync data (undeclared category)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_contact_sync_record.session_id = sid
        result = local_gateway.process_record(sid, sample_contact_sync_record)

        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 0
        assert "oem-stellantis" in result.records_blocked


class TestInsurerAccess:
    """Insurer stakeholder access constraints."""

    def test_insurer_receives_behaviour_scores(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """Insurer receives driving behaviour data with score fields."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid
        result = local_gateway.process_record(sid, sample_driving_behaviour_record)

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 1
        assert insurer_records[0].data_type == DataType.DRIVING_BEHAVIOUR
        assert "score" in insurer_records[0].fields

    def test_insurer_excluded_from_gps(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """Insurer never sees GPS position fields even when present in the record."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid
        result = local_gateway.process_record(sid, sample_driving_behaviour_record)

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 1
        fields = insurer_records[0].fields
        # GPS and route explicitly excluded in manifest
        assert "latitude" not in fields
        assert "longitude" not in fields
        assert "route" not in fields

    def test_insurer_excluded_from_raw_traces(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """Insurer never sees raw speed/accel traces (excluded_fields)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid
        result = local_gateway.process_record(sid, sample_driving_behaviour_record)

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 1
        fields = insurer_records[0].fields
        assert "raw_speed_trace" not in fields
        assert "raw_accel_trace" not in fields

    def test_insurer_per_trip_score_granularity(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_driving_behaviour_record: DataRecord,
    ) -> None:
        """Insurer receives per-trip score granularity (only score-level fields)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_driving_behaviour_record.session_id = sid
        result = local_gateway.process_record(sid, sample_driving_behaviour_record)

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 1
        assert insurer_records[0].granularity == Granularity.PER_TRIP_SCORE
        # per_trip_score transformation reduces to score-level fields only
        fields = insurer_records[0].fields
        assert "score" in fields


class TestAdasAccess:
    """ADAS supplier access constraints."""

    def test_adas_receives_only_sealed_events(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sealed_event_record: DataRecord,
    ) -> None:
        """ADAS supplier receives sealed event records."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sealed_event_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sealed_event_record)

        adas_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        assert len(adas_records) == 1
        assert adas_records[0].data_type == DataType.SEALED_EVENT

    def test_adas_excludes_driver_face_and_audio(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sealed_event_record: DataRecord,
    ) -> None:
        """ADAS supplier does not receive driver_face_crop or cabin_audio."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sealed_event_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sealed_event_record)

        adas_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        assert len(adas_records) == 1
        assert "driver_face_crop" not in adas_records[0].fields
        assert "cabin_audio" not in adas_records[0].fields

    def test_adas_blocked_from_normal_telemetry(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_speed_record: DataRecord,
    ) -> None:
        """ADAS does not receive speed, position, or other normal telemetry."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_speed_record.session_id = sid
        result = local_gateway.process_record(sid, sample_speed_record)

        adas_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        assert len(adas_records) == 0
        assert "adas-mobileye" in result.records_blocked

    def test_adas_blocked_from_position(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_position_record: DataRecord,
    ) -> None:
        """ADAS does not receive standalone position records."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_position_record.session_id = sid
        result = local_gateway.process_record(sid, sample_position_record)

        adas_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        assert len(adas_records) == 0


class TestTier1Access:
    """Tier-1 supplier access constraints."""

    def test_tier1_receives_diagnostics(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_diagnostic_record: DataRecord,
    ) -> None:
        """Tier-1 receives diagnostic code records."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_diagnostic_record.session_id = sid
        result = local_gateway.process_record(sid, sample_diagnostic_record)

        tier1_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        assert len(tier1_records) == 1
        assert tier1_records[0].data_type == DataType.DIAGNOSTIC_CODE
        assert "dtc_code" in tier1_records[0].fields

    def test_tier1_no_driver_identity(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_diagnostic_record: DataRecord,
    ) -> None:
        """Tier-1 never sees driver-identifying fields (vin, driver_id)."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_diagnostic_record.session_id = sid
        result = local_gateway.process_record(sid, sample_diagnostic_record)

        tier1_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        assert len(tier1_records) == 1
        fields = tier1_records[0].fields
        assert "vin" not in fields
        assert "driver_id" not in fields

    def test_tier1_receives_sensor_health(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """Tier-1 receives sensor health records."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sensor_health_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sensor_health_record)

        tier1_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        assert len(tier1_records) == 1
        assert tier1_records[0].data_type == DataType.SENSOR_HEALTH

    def test_tier1_sensor_health_excludes_firmware_serial(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """Tier-1 sensor health excludes firmware_version and serial_number."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sensor_health_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sensor_health_record)

        tier1_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "tier1-bosch"
        ]
        assert len(tier1_records) == 1
        fields = tier1_records[0].fields
        assert "firmware_version" not in fields
        assert "serial_number" not in fields


class TestCrossCuttingEnforcement:
    """Cross-cutting manifest enforcement checks."""

    def test_undeclared_data_type_blocked(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_contact_sync_record: DataRecord,
    ) -> None:
        """ContactSync type is not in any stakeholder's manifest - blocked for all."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_contact_sync_record.session_id = sid
        result = local_gateway.process_record(sid, sample_contact_sync_record)

        assert len(result.records_transmitted) == 0
        # All 4 stakeholders should be blocked
        assert len(result.records_blocked) == 4

    def test_all_stakeholders_see_different_views(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
        sample_sensor_health_record: DataRecord,
    ) -> None:
        """Same sensor_health record produces different filtered views per stakeholder.

        OEM and Tier-1 both receive sensor_health but with different field sets.
        Insurer and ADAS do not receive sensor_health at all.
        """
        sid = local_gateway.start_session("veh-001", demo_manifest)
        sample_sensor_health_record.session_id = sid
        result = local_gateway.process_record(sid, sample_sensor_health_record)

        stakeholder_views = {r.stakeholder_id: r for r in result.records_transmitted}

        # OEM and Tier-1 both get sensor_health
        assert "oem-stellantis" in stakeholder_views
        assert "tier1-bosch" in stakeholder_views

        # Insurer and ADAS do not get sensor_health
        assert "insurer-allianz" not in stakeholder_views
        assert "adas-mobileye" not in stakeholder_views

        oem_fields = set(stakeholder_views["oem-stellantis"].fields.keys())
        tier1_fields = set(stakeholder_views["tier1-bosch"].fields.keys())

        # They have overlapping but distinct field sets
        # OEM gets uptime_hours; Tier-1 gets error_count
        assert "uptime_hours" in oem_fields
        assert "error_count" in tier1_fields
        # Tier-1 excludes firmware_version and serial_number
        assert "firmware_version" not in tier1_fields
        assert "serial_number" not in tier1_fields

    def test_media_metadata_blocked_for_all(
        self,
        local_gateway: LocalGateway,
        demo_manifest,
    ) -> None:
        """MediaMetadata is not declared for any stakeholder."""
        sid = local_gateway.start_session("veh-001", demo_manifest)
        record = DataRecord(
            session_id=sid,
            source="a2dp",
            data_type=DataType.MEDIA_METADATA,
            fields={"track_title": "Bohemian Rhapsody", "artist": "Queen"},
        )
        result = local_gateway.process_record(sid, record)
        assert len(result.records_transmitted) == 0
        assert len(result.records_blocked) == 4
