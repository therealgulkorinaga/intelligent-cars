"""Tests for all Pydantic models: serialization, validation, defaults."""

from datetime import datetime, timezone

import pytest

from chambers_sim.models.data_record import (
    BurnReceipt,
    ChannelType,
    DataRecord,
    DataType,
    FilteredDataRecord,
    Granularity,
    Jurisdiction,
    LegalBasis,
    ProcessingResult,
    SessionSummary,
)
from chambers_sim.models.manifest import (
    CategoryDeclaration,
    MandatoryRetention,
    PreservationManifest,
    StakeholderDeclaration,
)


# ---- DataType enum ----


class TestDataType:
    def test_all_variants(self):
        expected = {
            "Position", "Speed", "Acceleration", "SensorHealth",
            "DrivingBehaviour", "CameraFrame", "LidarCloud",
            "GnssPosition", "ImuReading", "DiagnosticCode",
            "ContactSync", "MediaMetadata", "V2xCam", "SealedEvent",
        }
        assert {dt.value for dt in DataType} == expected

    def test_string_value(self):
        assert DataType.POSITION.value == "Position"
        assert DataType.SEALED_EVENT.value == "SealedEvent"


class TestGranularity:
    def test_all_variants(self):
        assert {g.value for g in Granularity} == {
            "Raw", "Aggregated", "Anonymised", "PerTripScore"
        }


class TestJurisdiction:
    def test_all_variants(self):
        assert {j.value for j in Jurisdiction} == {"EU", "US", "CN", "UK"}


class TestLegalBasis:
    def test_all_variants(self):
        assert len(LegalBasis) == 6
        assert LegalBasis.CONSENT.value == "Consent"
        assert LegalBasis.LEGITIMATE_INTEREST.value == "LegitimateInterest"


class TestChannelType:
    def test_all_variants(self):
        assert {c.value for c in ChannelType} == {
            "Cellular", "Bluetooth", "WiFi", "ObdII", "V2x"
        }


# ---- DataRecord ----


class TestDataRecord:
    def test_creation_with_defaults(self):
        record = DataRecord(
            session_id="sess-001",
            source="test",
            data_type=DataType.SPEED,
            fields={"speed_mps": 15.0},
        )
        assert record.session_id == "sess-001"
        assert record.data_type == DataType.SPEED
        assert record.channel == ChannelType.CELLULAR
        assert record.fields["speed_mps"] == 15.0
        assert isinstance(record.timestamp, datetime)

    def test_serialization_roundtrip(self):
        record = DataRecord(
            session_id="sess-002",
            timestamp=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
            source="sumo:vehicle_0",
            data_type=DataType.POSITION,
            fields={"latitude": 51.5074, "longitude": -0.1278},
            channel=ChannelType.CELLULAR,
        )
        data = record.model_dump(mode="json")
        restored = DataRecord.model_validate(data)
        assert restored.session_id == "sess-002"
        assert restored.data_type == DataType.POSITION
        assert restored.fields["latitude"] == 51.5074

    def test_empty_fields(self):
        record = DataRecord(
            session_id="s", source="t", data_type=DataType.SENSOR_HEALTH
        )
        assert record.fields == {}

    def test_all_channel_types(self):
        for channel in ChannelType:
            record = DataRecord(
                session_id="s", source="t", data_type=DataType.SPEED, channel=channel
            )
            assert record.channel == channel


# ---- FilteredDataRecord ----


class TestFilteredDataRecord:
    def test_creation(self):
        fr = FilteredDataRecord(
            stakeholder_id="oem-stellantis",
            data_type=DataType.SPEED,
            fields={"speed_mps": 15.0},
            granularity=Granularity.RAW,
        )
        assert fr.stakeholder_id == "oem-stellantis"
        assert fr.granularity == Granularity.RAW

    def test_default_granularity(self):
        fr = FilteredDataRecord(
            stakeholder_id="test", data_type=DataType.SPEED
        )
        assert fr.granularity == Granularity.RAW

    def test_serialization(self):
        fr = FilteredDataRecord(
            stakeholder_id="insurer",
            data_type=DataType.DRIVING_BEHAVIOUR,
            fields={"score": 85.0},
            granularity=Granularity.PER_TRIP_SCORE,
        )
        data = fr.model_dump(mode="json")
        restored = FilteredDataRecord.model_validate(data)
        assert restored.granularity == Granularity.PER_TRIP_SCORE
        assert restored.fields["score"] == 85.0


# ---- ProcessingResult ----


class TestProcessingResult:
    def test_empty_result(self):
        result = ProcessingResult()
        assert result.records_transmitted == []
        assert result.records_blocked == []

    def test_with_data(self):
        fr = FilteredDataRecord(
            stakeholder_id="oem", data_type=DataType.SPEED, fields={"speed_mps": 10}
        )
        result = ProcessingResult(
            records_transmitted=[fr],
            records_blocked=["insurer"],
        )
        assert len(result.records_transmitted) == 1
        assert result.records_blocked == ["insurer"]


# ---- BurnReceipt ----


class TestBurnReceipt:
    def test_creation(self):
        receipt = BurnReceipt(
            session_id="sess-001",
            layers_completed=["application_cache", "database_records"],
            success=True,
        )
        assert receipt.session_id == "sess-001"
        assert len(receipt.layers_completed) == 2
        assert receipt.success is True

    def test_failed_burn(self):
        receipt = BurnReceipt(session_id="s", success=False)
        assert receipt.success is False
        assert receipt.layers_completed == []

    def test_serialization(self):
        receipt = BurnReceipt(session_id="s", layers_completed=["l1", "l2"])
        data = receipt.model_dump(mode="json")
        restored = BurnReceipt.model_validate(data)
        assert restored.layers_completed == ["l1", "l2"]


# ---- SessionSummary ----


class TestSessionSummary:
    def test_creation(self):
        now = datetime.now(timezone.utc)
        summary = SessionSummary(
            session_id="sess-001",
            start=now,
            end=now,
            records_generated=100,
            records_transmitted=80,
            records_blocked=20,
            records_burned=100,
            stakeholder_breakdown={
                "oem": {"transmitted": 60, "blocked": 0},
                "insurer": {"transmitted": 20, "blocked": 20},
            },
        )
        assert summary.records_generated == 100
        assert summary.stakeholder_breakdown["oem"]["transmitted"] == 60

    def test_defaults(self):
        now = datetime.now(timezone.utc)
        summary = SessionSummary(session_id="s", start=now, end=now)
        assert summary.records_generated == 0
        assert summary.stakeholder_breakdown == {}


# ---- CategoryDeclaration ----


class TestCategoryDeclaration:
    def test_creation(self):
        cat = CategoryDeclaration(
            data_type=DataType.SPEED,
            fields=["speed_mps", "road_type"],
            excluded_fields=["raw_trace"],
            granularity=Granularity.RAW,
            retention="P90D",
            purpose="Fleet telemetry",
            jurisdiction=Jurisdiction.EU,
        )
        assert cat.data_type == DataType.SPEED
        assert "speed_mps" in cat.fields
        assert cat.retention == "P90D"

    def test_defaults(self):
        cat = CategoryDeclaration(data_type=DataType.POSITION)
        assert cat.granularity == Granularity.RAW
        assert cat.retention == "P30D"
        assert cat.jurisdiction == Jurisdiction.EU

    def test_serialization(self):
        cat = CategoryDeclaration(
            data_type=DataType.ACCELERATION,
            fields=["longitudinal"],
        )
        data = cat.model_dump(mode="json")
        restored = CategoryDeclaration.model_validate(data)
        assert restored.data_type == DataType.ACCELERATION


# ---- StakeholderDeclaration ----


class TestStakeholderDeclaration:
    def test_creation(self):
        stakeholder = StakeholderDeclaration(
            id="oem-stellantis",
            role="OEM",
            legal_basis=LegalBasis.CONTRACT,
            consent_ref="contract-2024",
            categories=[
                CategoryDeclaration(data_type=DataType.SPEED, fields=["speed_mps"]),
            ],
        )
        assert stakeholder.id == "oem-stellantis"
        assert len(stakeholder.categories) == 1

    def test_defaults(self):
        s = StakeholderDeclaration(id="test", role="tester")
        assert s.legal_basis == LegalBasis.CONSENT
        assert s.categories == []


# ---- MandatoryRetention ----


class TestMandatoryRetention:
    def test_creation(self):
        mr = MandatoryRetention(
            type=DataType.SEALED_EVENT,
            regulation="EU-EDR-2024",
            treatment="encrypted_archive",
        )
        assert mr.type == DataType.SEALED_EVENT
        assert mr.regulation == "EU-EDR-2024"


# ---- PreservationManifest ----


class TestPreservationManifest:
    def test_default_demo_manifest(self):
        manifest = PreservationManifest.default_demo_manifest()
        assert manifest.manifest_version == "1.0"
        assert manifest.vehicle_id == "demo-vehicle-001"
        assert len(manifest.stakeholders) == 4
        assert len(manifest.mandatory_retention) == 2

        # Verify stakeholder IDs
        ids = {s.id for s in manifest.stakeholders}
        assert ids == {"oem-stellantis", "insurer-allianz", "adas-mobileye", "tier1-bosch"}

    def test_oem_has_correct_categories(self):
        manifest = PreservationManifest.default_demo_manifest()
        oem = next(s for s in manifest.stakeholders if s.id == "oem-stellantis")
        cat_types = {c.data_type for c in oem.categories}
        assert DataType.POSITION in cat_types
        assert DataType.SPEED in cat_types
        assert DataType.DRIVING_BEHAVIOUR in cat_types
        assert DataType.SEALED_EVENT not in cat_types  # OEM doesn't get sealed events

    def test_insurer_only_gets_behaviour(self):
        manifest = PreservationManifest.default_demo_manifest()
        insurer = next(s for s in manifest.stakeholders if s.id == "insurer-allianz")
        assert len(insurer.categories) == 1
        assert insurer.categories[0].data_type == DataType.DRIVING_BEHAVIOUR
        assert insurer.categories[0].granularity == Granularity.PER_TRIP_SCORE

    def test_adas_only_gets_sealed_events(self):
        manifest = PreservationManifest.default_demo_manifest()
        adas = next(s for s in manifest.stakeholders if s.id == "adas-mobileye")
        assert len(adas.categories) == 1
        assert adas.categories[0].data_type == DataType.SEALED_EVENT

    def test_serialization_roundtrip(self):
        manifest = PreservationManifest.default_demo_manifest()
        data = manifest.model_dump(mode="json")
        restored = PreservationManifest.model_validate(data)
        assert len(restored.stakeholders) == 4
        assert restored.manifest_version == "1.0"

    def test_from_file(self, tmp_path):
        manifest = PreservationManifest.default_demo_manifest()
        path = tmp_path / "manifest.json"
        import json
        path.write_text(manifest.model_dump_json(indent=2))

        loaded = PreservationManifest.from_file(path)
        assert loaded.vehicle_id == manifest.vehicle_id
        assert len(loaded.stakeholders) == len(manifest.stakeholders)

    def test_empty_manifest(self):
        manifest = PreservationManifest()
        assert manifest.stakeholders == []
        assert manifest.mandatory_retention == []
        assert manifest.vehicle_id == ""
