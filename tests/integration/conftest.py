"""Shared fixtures for Chambers integration tests.

All fixtures produce synthetic data and use the LocalGateway so that
tests run standalone without SUMO, CARLA, or ROS2.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chambers_sim.models.data_record import (
    ChannelType,
    DataRecord,
    DataType,
)
from chambers_sim.models.manifest import (
    CategoryDeclaration,
    MandatoryRetention,
    PreservationManifest,
    StakeholderDeclaration,
)
from chambers_sim.models.data_record import Granularity, Jurisdiction, LegalBasis
from chambers_sim.utils.local_gateway import LocalGateway

MANIFESTS_DIR = Path(__file__).resolve().parents[2] / "manifests"


# ---------------------------------------------------------------------------
# Manifest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def demo_manifest() -> PreservationManifest:
    """The full four-stakeholder demo manifest (programmatic, matches PRD Section 7)."""
    return PreservationManifest.default_demo_manifest()


@pytest.fixture()
def minimal_manifest() -> PreservationManifest:
    """A minimal manifest with only the OEM stakeholder."""
    return PreservationManifest(
        manifest_version="1.0",
        vehicle_id="veh-minimal-001",
        stakeholders=[
            StakeholderDeclaration(
                id="oem-stellantis",
                role="OEM",
                legal_basis=LegalBasis.LEGITIMATE_INTEREST,
                categories=[
                    CategoryDeclaration(
                        data_type=DataType.SENSOR_HEALTH,
                        fields=["sensor_id", "status", "temperature", "uptime_hours"],
                        granularity=Granularity.ANONYMISED,
                        retention="P90D",
                        purpose="Predictive maintenance",
                        jurisdiction=Jurisdiction.EU,
                    ),
                ],
            ),
        ],
        mandatory_retention=[
            MandatoryRetention(type=DataType.SEALED_EVENT, regulation="EU-EDR-2024"),
        ],
    )


@pytest.fixture()
def insurer_only_manifest() -> PreservationManifest:
    """Manifest with only an insurer stakeholder."""
    return PreservationManifest(
        manifest_version="1.0",
        vehicle_id="veh-insurer-only-001",
        stakeholders=[
            StakeholderDeclaration(
                id="insurer-allianz",
                role="Insurer",
                legal_basis=LegalBasis.CONSENT,
                consent_ref="consent-telematics-policy-2024-001",
                categories=[
                    CategoryDeclaration(
                        data_type=DataType.DRIVING_BEHAVIOUR,
                        fields=["score", "distance_km", "duration_minutes", "time_of_day_bucket"],
                        excluded_fields=[
                            "raw_speed_trace",
                            "raw_accel_trace",
                            "latitude",
                            "longitude",
                            "route",
                        ],
                        granularity=Granularity.PER_TRIP_SCORE,
                        retention="P365D",
                        purpose="Usage-based insurance premium calculation",
                        jurisdiction=Jurisdiction.EU,
                    ),
                ],
            ),
        ],
    )


@pytest.fixture()
def no_stakeholders_manifest() -> PreservationManifest:
    """Manifest with zero stakeholders — every record should be blocked."""
    return PreservationManifest(
        manifest_version="1.0",
        vehicle_id="veh-burn-all-001",
        stakeholders=[],
        mandatory_retention=[
            MandatoryRetention(type=DataType.SEALED_EVENT, regulation="EU-EDR-2024"),
        ],
    )


# ---------------------------------------------------------------------------
# Gateway fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def local_gateway() -> LocalGateway:
    """A fresh LocalGateway instance."""
    return LocalGateway()


# ---------------------------------------------------------------------------
# Sample data record fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_position_record() -> DataRecord:
    """A GPS position record with raw coordinates."""
    return DataRecord(
        session_id="test-session",
        source="gps-module",
        data_type=DataType.POSITION,
        fields={
            "latitude": 48.8566,
            "longitude": 2.3522,
            "altitude": 35.0,
            "heading": 180.0,
            "hdop": 1.2,
        },
        channel=ChannelType.CELLULAR,
    )


@pytest.fixture()
def sample_speed_record() -> DataRecord:
    """A vehicle speed record."""
    return DataRecord(
        session_id="test-session",
        source="can-bus",
        data_type=DataType.SPEED,
        fields={
            "speed_mps": 22.5,
            "speed_limit": 30.0,
            "road_type": "urban",
        },
        channel=ChannelType.CELLULAR,
    )


@pytest.fixture()
def sample_driving_behaviour_record() -> DataRecord:
    """A driving behaviour record with scores and GPS (GPS should be excluded for insurer)."""
    return DataRecord(
        session_id="test-session",
        source="behaviour-engine",
        data_type=DataType.DRIVING_BEHAVIOUR,
        fields={
            "score": 85.0,
            "distance_km": 12.3,
            "duration_minutes": 22,
            "time_of_day_bucket": "morning",
            "harsh_braking_count": 1,
            "harsh_accel_count": 0,
            "latitude": 48.8566,
            "longitude": 2.3522,
            "route": "A1-Paris-Nord",
            "raw_speed_trace": [20, 25, 30, 28, 22],
            "raw_accel_trace": [0.1, 0.3, -0.5, 0.0, 0.2],
        },
        channel=ChannelType.CELLULAR,
    )


@pytest.fixture()
def sample_sensor_health_record() -> DataRecord:
    """An anonymised sensor health record."""
    return DataRecord(
        session_id="test-session",
        source="sensor-hub",
        data_type=DataType.SENSOR_HEALTH,
        fields={
            "sensor_id": "lidar-front-001",
            "status": "nominal",
            "temperature": 42.5,
            "uptime_hours": 1200,
            "error_count": 0,
            "firmware_version": "3.2.1",
            "serial_number": "SN-ABC-12345",
        },
        channel=ChannelType.CELLULAR,
    )


@pytest.fixture()
def sample_sealed_event_record() -> DataRecord:
    """A sealed safety-critical event (e.g. near-miss)."""
    return DataRecord(
        session_id="test-session",
        source="adas-ecu",
        data_type=DataType.SEALED_EVENT,
        fields={
            "trigger_type": "near_miss",
            "trigger_timestamp": "2026-04-08T09:15:00Z",
            "window_start": "2026-04-08T09:14:55Z",
            "window_end": "2026-04-08T09:15:02Z",
            "camera_frames": ["frame_001.bin", "frame_002.bin"],
            "lidar_snapshots": ["snap_001.pcd"],
            "imu_trace": [0.1, 0.2, -0.3],
            "gnss_trace": [48.856, 2.352],
            "speed_trace": [22.0, 18.0, 5.0],
            "driver_face_crop": "face_crop_001.jpg",
            "cabin_audio": "audio_clip.wav",
        },
        channel=ChannelType.CELLULAR,
    )


@pytest.fixture()
def sample_diagnostic_record() -> DataRecord:
    """An OBD-II diagnostic trouble code record."""
    return DataRecord(
        session_id="test-session",
        source="obd-reader",
        data_type=DataType.DIAGNOSTIC_CODE,
        fields={
            "dtc_code": "P0301",
            "severity": "warning",
            "module": "engine",
            "mileage_km": 45000,
            "vin": "WBA12345678901234",
            "driver_id": "driver-001",
        },
        channel=ChannelType.OBD_II,
    )


@pytest.fixture()
def sample_contact_sync_record() -> DataRecord:
    """A Bluetooth contact sync record — typically not in any manifest."""
    return DataRecord(
        session_id="test-session",
        source="bluetooth-hci",
        data_type=DataType.CONTACT_SYNC,
        fields={
            "device_name": "iPhone-John",
            "mac_address": "AA:BB:CC:DD:EE:FF",
            "contacts_count": 342,
            "phone_book_hash": "sha256:abcdef1234567890",
        },
        channel=ChannelType.BLUETOOTH,
    )


@pytest.fixture()
def sample_v2x_cam_record() -> DataRecord:
    """A V2X Cooperative Awareness Message."""
    return DataRecord(
        session_id="test-session",
        source="v2x-obu",
        data_type=DataType.V2X_CAM,
        fields={
            "station_id": "pseudo-12345",
            "latitude": 48.8566,
            "longitude": 2.3522,
            "speed_mps": 15.0,
            "heading": 90.0,
            "generation_delta_time": 100,
        },
        channel=ChannelType.V2X,
    )


# ---------------------------------------------------------------------------
# Drive session generator
# ---------------------------------------------------------------------------

@pytest.fixture()
def generate_drive_session():
    """Return a factory that generates a realistic sequence of n DataRecords."""

    def _generate(n_records: int, session_id: str = "gen-session") -> list[DataRecord]:
        rng = random.Random(42)  # deterministic seed
        records: list[DataRecord] = []

        data_type_weights = [
            (DataType.POSITION, 0.20),
            (DataType.SPEED, 0.20),
            (DataType.DRIVING_BEHAVIOUR, 0.15),
            (DataType.SENSOR_HEALTH, 0.10),
            (DataType.SEALED_EVENT, 0.05),
            (DataType.DIAGNOSTIC_CODE, 0.08),
            (DataType.CONTACT_SYNC, 0.05),
            (DataType.V2X_CAM, 0.10),
            (DataType.MEDIA_METADATA, 0.04),
            (DataType.ACCELERATION, 0.03),
        ]
        types = [t for t, _ in data_type_weights]
        weights = [w for _, w in data_type_weights]

        base_lat, base_lon = 48.8566, 2.3522

        for i in range(n_records):
            dt = rng.choices(types, weights=weights, k=1)[0]
            fields: dict = {}

            if dt == DataType.POSITION:
                fields = {
                    "latitude": base_lat + rng.gauss(0, 0.01),
                    "longitude": base_lon + rng.gauss(0, 0.01),
                    "altitude": 35.0 + rng.gauss(0, 2),
                    "heading": rng.uniform(0, 360),
                    "hdop": round(rng.uniform(0.5, 3.0), 1),
                }
            elif dt == DataType.SPEED:
                fields = {
                    "speed_mps": round(rng.uniform(0, 40), 1),
                    "speed_limit": rng.choice([30, 50, 70, 90, 130]),
                    "road_type": rng.choice(["urban", "rural", "motorway"]),
                }
            elif dt == DataType.DRIVING_BEHAVIOUR:
                fields = {
                    "score": round(rng.uniform(50, 100), 1),
                    "distance_km": round(rng.uniform(0.1, 50), 1),
                    "duration_minutes": rng.randint(1, 120),
                    "time_of_day_bucket": rng.choice(["morning", "afternoon", "evening", "night"]),
                    "harsh_braking_count": rng.randint(0, 5),
                    "harsh_accel_count": rng.randint(0, 3),
                    "latitude": base_lat + rng.gauss(0, 0.01),
                    "longitude": base_lon + rng.gauss(0, 0.01),
                    "route": f"route-{rng.randint(1, 10)}",
                    "raw_speed_trace": [round(rng.uniform(0, 40), 1) for _ in range(5)],
                    "raw_accel_trace": [round(rng.gauss(0, 0.5), 2) for _ in range(5)],
                }
            elif dt == DataType.SENSOR_HEALTH:
                fields = {
                    "sensor_id": f"sensor-{rng.randint(1, 20):03d}",
                    "status": rng.choice(["nominal", "degraded", "faulty"]),
                    "temperature": round(rng.uniform(20, 80), 1),
                    "uptime_hours": rng.randint(0, 5000),
                    "error_count": rng.randint(0, 10),
                    "firmware_version": f"{rng.randint(1,5)}.{rng.randint(0,9)}.{rng.randint(0,9)}",
                    "serial_number": f"SN-{rng.randint(10000, 99999)}",
                }
            elif dt == DataType.SEALED_EVENT:
                fields = {
                    "trigger_type": rng.choice(["near_miss", "hard_braking", "collision"]),
                    "trigger_timestamp": "2026-04-08T09:15:00Z",
                    "window_start": "2026-04-08T09:14:55Z",
                    "window_end": "2026-04-08T09:15:02Z",
                    "camera_frames": [f"frame_{j}.bin" for j in range(rng.randint(1, 5))],
                    "lidar_snapshots": [f"snap_{j}.pcd" for j in range(rng.randint(1, 3))],
                    "imu_trace": [round(rng.gauss(0, 1), 2) for _ in range(3)],
                    "gnss_trace": [base_lat + rng.gauss(0, 0.001), base_lon + rng.gauss(0, 0.001)],
                    "speed_trace": [round(rng.uniform(0, 30), 1) for _ in range(3)],
                    "driver_face_crop": "face.jpg",
                    "cabin_audio": "audio.wav",
                }
            elif dt == DataType.DIAGNOSTIC_CODE:
                fields = {
                    "dtc_code": f"P{rng.randint(0, 9)}{rng.randint(100, 999)}",
                    "severity": rng.choice(["info", "warning", "critical"]),
                    "module": rng.choice(["engine", "transmission", "abs", "airbag"]),
                    "mileage_km": rng.randint(1000, 200000),
                    "vin": "WBA12345678901234",
                    "driver_id": "driver-001",
                }
            elif dt == DataType.CONTACT_SYNC:
                fields = {
                    "device_name": f"Phone-{rng.randint(1, 5)}",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "contacts_count": rng.randint(10, 500),
                    "phone_book_hash": f"sha256:{rng.randbytes(8).hex()}",
                }
            elif dt == DataType.V2X_CAM:
                fields = {
                    "station_id": f"pseudo-{rng.randint(10000, 99999)}",
                    "latitude": base_lat + rng.gauss(0, 0.005),
                    "longitude": base_lon + rng.gauss(0, 0.005),
                    "speed_mps": round(rng.uniform(0, 30), 1),
                    "heading": round(rng.uniform(0, 360), 1),
                    "generation_delta_time": rng.randint(50, 200),
                }
            elif dt == DataType.MEDIA_METADATA:
                fields = {
                    "track_title": f"Song {rng.randint(1, 100)}",
                    "artist": f"Artist {rng.randint(1, 20)}",
                    "album": f"Album {rng.randint(1, 10)}",
                    "duration_seconds": rng.randint(120, 360),
                    "source": "bluetooth_a2dp",
                }
            elif dt == DataType.ACCELERATION:
                fields = {
                    "longitudinal": round(rng.gauss(0, 2), 2),
                    "lateral": round(rng.gauss(0, 1), 2),
                    "vertical": round(rng.gauss(0, 0.5), 2),
                }

            channel = {
                DataType.CONTACT_SYNC: ChannelType.BLUETOOTH,
                DataType.MEDIA_METADATA: ChannelType.BLUETOOTH,
                DataType.V2X_CAM: ChannelType.V2X,
                DataType.DIAGNOSTIC_CODE: ChannelType.OBD_II,
            }.get(dt, ChannelType.CELLULAR)

            records.append(
                DataRecord(
                    session_id=session_id,
                    source=f"synthetic-{dt.value.lower()}",
                    data_type=dt,
                    fields=fields,
                    channel=channel,
                )
            )

        return records

    return _generate
