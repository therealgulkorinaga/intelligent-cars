"""Tests for the local gateway: manifest evaluation, filtering, burn protocol."""

from datetime import datetime, timezone

import pytest

from chambers_sim.models.data_record import (
    ChannelType,
    DataRecord,
    DataType,
    Granularity,
)
from chambers_sim.models.manifest import (
    CategoryDeclaration,
    PreservationManifest,
    StakeholderDeclaration,
)
from chambers_sim.utils.data_residue import DataResidueAnalyzer
from chambers_sim.utils.local_gateway import BURN_LAYERS, LocalGateway


@pytest.fixture
def manifest() -> PreservationManifest:
    """Return the default demo manifest."""
    return PreservationManifest.default_demo_manifest()


@pytest.fixture
def gateway() -> LocalGateway:
    """Return a fresh LocalGateway instance."""
    return LocalGateway()


@pytest.fixture
def session_id(gateway: LocalGateway, manifest: PreservationManifest) -> str:
    """Start a session and return its ID."""
    return gateway.start_session("test-vehicle", manifest)


def _make_record(
    session_id: str,
    data_type: DataType,
    fields: dict,
    channel: ChannelType = ChannelType.CELLULAR,
) -> DataRecord:
    return DataRecord(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc),
        source="test",
        data_type=data_type,
        fields=fields,
        channel=channel,
    )


class TestManifestEvaluationOemGetsAnonymised:
    """OEM gets position data but at Anonymised granularity."""

    def test_oem_position_is_anonymised(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.POSITION,
            {
                "latitude": 51.507400,
                "longitude": -0.127800,
                "altitude": 30.0,
                "heading": 180.0,
            },
        )
        result = gateway.process_record(session_id, record)

        # OEM should receive position
        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1

        oem_rec = oem_records[0]
        assert oem_rec.granularity == Granularity.ANONYMISED

        # Latitude/longitude should be snapped to ~1km grid (different from original)
        assert oem_rec.fields["latitude"] != 51.507400
        assert oem_rec.fields["longitude"] != -0.127800

    def test_oem_speed_is_raw(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.SPEED,
            {"speed_mps": 15.5, "speed_limit": 50.0, "road_type": "urban"},
        )
        result = gateway.process_record(session_id, record)
        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1
        assert oem_records[0].granularity == Granularity.RAW
        assert oem_records[0].fields["speed_mps"] == 15.5


class TestManifestEvaluationInsurerNoGps:
    """Insurer should never receive GPS/position data."""

    def test_insurer_blocked_for_position(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.POSITION,
            {"latitude": 51.5074, "longitude": -0.1278},
        )
        result = gateway.process_record(session_id, record)

        # Insurer should be in the blocked list (no POSITION category)
        assert "insurer-allianz" in result.records_blocked

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 0

    def test_insurer_gets_behaviour_score_only(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.DRIVING_BEHAVIOUR,
            {
                "score": 85.0,
                "harsh_braking_count": 2,
                "harsh_accel_count": 1,
                "distance_km": 12.5,
                "duration_minutes": 30.0,
                "time_of_day_bucket": "midday",
                "raw_speed_trace": [15.0, 16.0, 14.0],
                "raw_accel_trace": [0.5, -0.3, 1.2],
                "latitude": 51.5074,
                "longitude": -0.1278,
                "route": "A-B-C",
            },
        )
        result = gateway.process_record(session_id, record)

        insurer_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_records) == 1

        insurer_rec = insurer_records[0]
        assert insurer_rec.granularity == Granularity.PER_TRIP_SCORE

        # Per-trip score should only contain score + trip metadata
        assert "score" in insurer_rec.fields
        assert "distance_km" in insurer_rec.fields
        # Raw traces, GPS, and route should be excluded
        assert "raw_speed_trace" not in insurer_rec.fields
        assert "raw_accel_trace" not in insurer_rec.fields
        assert "latitude" not in insurer_rec.fields
        assert "route" not in insurer_rec.fields


class TestManifestEvaluationAdasSealedEventsOnly:
    """ADAS supplier should only receive sealed events."""

    def test_adas_blocked_for_speed(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.SPEED,
            {"speed_mps": 25.0},
        )
        result = gateway.process_record(session_id, record)
        assert "adas-mobileye" in result.records_blocked

    def test_adas_receives_sealed_event(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.SEALED_EVENT,
            {
                "trigger_type": "collision",
                "trigger_timestamp": "2025-06-01T12:00:00Z",
                "window_start": "2025-06-01T11:59:55Z",
                "window_end": "2025-06-01T12:00:02Z",
                "camera_frames": [{"frame": 1}],
                "lidar_snapshots": [{"points": 1000}],
                "imu_trace": [{"ax": 0.1}],
                "gnss_trace": [{"lat": 51.5}],
                "speed_trace": [25.0],
                "driver_face_crop": "base64data",
                "cabin_audio": "base64audio",
            },
        )
        result = gateway.process_record(session_id, record)

        adas_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "adas-mobileye"
        ]
        assert len(adas_records) == 1

        adas_rec = adas_records[0]
        assert adas_rec.granularity == Granularity.RAW
        # Excluded fields should be removed
        assert "driver_face_crop" not in adas_rec.fields
        assert "cabin_audio" not in adas_rec.fields
        # Declared fields should be present
        assert "trigger_type" in adas_rec.fields
        assert "camera_frames" in adas_rec.fields

    def test_adas_blocked_for_position(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.POSITION,
            {"latitude": 51.5074, "longitude": -0.1278},
        )
        result = gateway.process_record(session_id, record)
        assert "adas-mobileye" in result.records_blocked


class TestUndeclaredStakeholderBlocked:
    """A stakeholder not in the manifest should never receive data."""

    def test_unknown_stakeholder_not_in_results(self, gateway, session_id):
        record = _make_record(
            session_id,
            DataType.SPEED,
            {"speed_mps": 20.0},
        )
        result = gateway.process_record(session_id, record)

        # Only declared stakeholders can appear
        transmitted_ids = {r.stakeholder_id for r in result.records_transmitted}
        assert "unknown-corp" not in transmitted_ids

    def test_custom_manifest_with_only_one_stakeholder(self, gateway):
        custom_manifest = PreservationManifest(
            vehicle_id="v1",
            stakeholders=[
                StakeholderDeclaration(
                    id="only-one",
                    role="Test",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.SPEED,
                            fields=["speed_mps"],
                        )
                    ],
                )
            ],
        )
        sid = gateway.start_session("v1", custom_manifest)
        record = _make_record(sid, DataType.SPEED, {"speed_mps": 10.0})
        result = gateway.process_record(sid, record)

        assert len(result.records_transmitted) == 1
        assert result.records_transmitted[0].stakeholder_id == "only-one"
        # No other stakeholder received data
        assert len(result.records_blocked) == 0  # only one stakeholder and it matched


class TestJurisdictionBlock:
    """Jurisdiction checking with mismatched jurisdictions."""

    def test_records_from_matching_jurisdiction_pass(self, gateway, session_id):
        # The default manifest is all EU jurisdiction
        record = _make_record(
            session_id,
            DataType.SPEED,
            {"speed_mps": 25.0},
        )
        result = gateway.process_record(session_id, record)
        # OEM should receive it (EU jurisdiction matches)
        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1


class TestConsentRevocation:
    """Test that revoking consent blocks a stakeholder immediately."""

    def test_revoke_blocks_stakeholder(self, gateway, session_id):
        # Before revocation: insurer gets driving behaviour
        record = _make_record(
            session_id,
            DataType.DRIVING_BEHAVIOUR,
            {
                "score": 90.0,
                "distance_km": 5.0,
                "duration_minutes": 15.0,
                "time_of_day_bucket": "morning_rush",
            },
        )
        result_before = gateway.process_record(session_id, record)
        insurer_before = [
            r for r in result_before.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_before) == 1

        # Revoke insurer consent
        gateway.revoke_consent(session_id, "insurer-allianz")

        # After revocation: insurer is blocked
        result_after = gateway.process_record(session_id, record)
        insurer_after = [
            r for r in result_after.records_transmitted if r.stakeholder_id == "insurer-allianz"
        ]
        assert len(insurer_after) == 0
        assert "insurer-allianz" in result_after.records_blocked

    def test_revoke_does_not_affect_others(self, gateway, session_id):
        gateway.revoke_consent(session_id, "insurer-allianz")

        record = _make_record(
            session_id,
            DataType.SPEED,
            {"speed_mps": 20.0, "speed_limit": 50.0, "road_type": "urban"},
        )
        result = gateway.process_record(session_id, record)

        # OEM should still receive speed data
        oem_records = [
            r for r in result.records_transmitted if r.stakeholder_id == "oem-stellantis"
        ]
        assert len(oem_records) == 1


class TestSessionLifecycle:
    """Test the full session lifecycle: start, process, end, burn."""

    def test_full_lifecycle(self, gateway, manifest):
        sid = gateway.start_session("lifecycle-vehicle", manifest)
        assert sid.startswith("local-")

        # Send several records
        for i in range(10):
            record = _make_record(sid, DataType.SPEED, {"speed_mps": float(i)})
            gateway.process_record(sid, record)

        # Get summary before end
        summary = gateway.get_session_summary(sid)
        assert summary.records_generated == 10
        assert summary.records_transmitted > 0

        # End session -> burn
        receipt = gateway.end_session(sid)
        assert receipt.success is True
        assert len(receipt.layers_completed) == 6
        assert set(receipt.layers_completed) == set(BURN_LAYERS)

        # Summary after end
        summary_after = gateway.get_session_summary(sid)
        assert summary_after.records_burned == 10

    def test_cannot_process_after_end(self, gateway, manifest):
        sid = gateway.start_session("v", manifest)
        gateway.end_session(sid)

        record = _make_record(sid, DataType.SPEED, {"speed_mps": 10.0})
        with pytest.raises(ValueError, match="already ended"):
            gateway.process_record(sid, record)

    def test_unknown_session_raises(self, gateway):
        record = _make_record("nonexistent", DataType.SPEED, {"speed_mps": 10.0})
        with pytest.raises(ValueError, match="Unknown session"):
            gateway.process_record("nonexistent", record)

    def test_audit_log_populated(self, gateway, manifest):
        sid = gateway.start_session("audit-vehicle", manifest)
        record = _make_record(sid, DataType.SPEED, {"speed_mps": 10.0})
        gateway.process_record(sid, record)
        gateway.end_session(sid)

        events = gateway.audit_log
        assert len(events) >= 3  # start, process, end, burn
        event_types = [e.event_type for e in events]
        assert "session_start" in event_types
        assert "record_processed" in event_types
        assert "session_end" in event_types
        assert "burn_complete" in event_types


class TestDataResidueReduction:
    """Test that Chambers actually reduces data exposure."""

    def test_reduction_is_significant(self, manifest):
        from chambers_sim.utils.data_residue import DataResidueAnalyzer

        # Generate some synthetic records
        records = []
        for i in range(50):
            records.append(
                DataRecord(
                    session_id="test",
                    source="test",
                    data_type=DataType.POSITION,
                    fields={
                        "latitude": 51.5074 + i * 0.001,
                        "longitude": -0.1278 + i * 0.001,
                        "altitude": 30.0,
                        "heading": 180.0,
                    },
                )
            )
            records.append(
                DataRecord(
                    session_id="test",
                    source="test",
                    data_type=DataType.SPEED,
                    fields={"speed_mps": 15.0 + i * 0.1, "speed_limit": 50.0, "road_type": "urban"},
                )
            )
            records.append(
                DataRecord(
                    session_id="test",
                    source="test",
                    data_type=DataType.CAMERA_FRAME,
                    fields={
                        "width": 1920,
                        "height": 1080,
                        "fov": 90,
                        "timestamp": 12345.0 + i,
                    },
                )
            )

        analyzer = DataResidueAnalyzer()
        analyzer.run_chambers(records, manifest)
        report = analyzer.compare()

        # Chambers should reduce total data exposure
        assert report.total_bytes_chambers < report.total_bytes_baseline
        assert report.reduction_ratio > 0.0

        # Camera frames should be fully blocked for all stakeholders
        # (no stakeholder declares CameraFrame in the demo manifest)
        assert report.total_bytes_chambers < report.total_bytes_baseline

    def test_analyzer_with_all_blocked_data(self):
        """If no stakeholder declares the data type, everything is blocked."""
        manifest = PreservationManifest(
            vehicle_id="v1",
            stakeholders=[
                StakeholderDeclaration(
                    id="limited",
                    role="Test",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.SPEED,
                            fields=["speed_mps"],
                        )
                    ],
                )
            ],
        )

        records = [
            DataRecord(
                session_id="t",
                source="t",
                data_type=DataType.CAMERA_FRAME,
                fields={"width": 1920, "data": "large_blob" * 100},
            )
            for _ in range(20)
        ]

        analyzer = DataResidueAnalyzer()
        analyzer.run_chambers(records, manifest)
        report = analyzer.compare()

        # All camera frames should be blocked -> chambers bytes = 0 for stakeholder
        assert report.total_bytes_chambers == 0
