"""Shared fixtures for end-to-end tests.

Reuses the integration conftest fixtures via the tests-level conftest,
plus adds fleet-level fixtures.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from chambers_sim.models.data_record import (
    ChannelType,
    DataRecord,
    DataType,
)
from chambers_sim.models.manifest import PreservationManifest
from chambers_sim.utils.local_gateway import LocalGateway


@pytest.fixture()
def local_gateway() -> LocalGateway:
    """A fresh LocalGateway instance for e2e tests."""
    return LocalGateway()


@pytest.fixture()
def demo_manifest() -> PreservationManifest:
    """The full demo manifest."""
    return PreservationManifest.default_demo_manifest()


@pytest.fixture()
def generate_vehicle_records():
    """Factory: generate records for a single vehicle drive session."""

    def _generate(
        n_records: int,
        session_id: str,
        vehicle_seed: int = 0,
    ) -> list[DataRecord]:
        rng = random.Random(42 + vehicle_seed)
        records: list[DataRecord] = []

        data_types_and_weights = [
            (DataType.POSITION, 0.18),
            (DataType.SPEED, 0.18),
            (DataType.DRIVING_BEHAVIOUR, 0.14),
            (DataType.SENSOR_HEALTH, 0.10),
            (DataType.SEALED_EVENT, 0.05),
            (DataType.DIAGNOSTIC_CODE, 0.08),
            (DataType.CONTACT_SYNC, 0.05),
            (DataType.V2X_CAM, 0.10),
            (DataType.MEDIA_METADATA, 0.05),
            (DataType.ACCELERATION, 0.04),
            (DataType.CAMERA_FRAME, 0.02),
            (DataType.LIDAR_CLOUD, 0.01),
        ]
        types = [t for t, _ in data_types_and_weights]
        weights = [w for _, w in data_types_and_weights]
        base_lat = 48.8566 + vehicle_seed * 0.01
        base_lon = 2.3522 + vehicle_seed * 0.01

        for i in range(n_records):
            dt = rng.choices(types, weights=weights, k=1)[0]
            fields: dict = {}

            if dt == DataType.POSITION:
                fields = {
                    "latitude": base_lat + rng.gauss(0, 0.01),
                    "longitude": base_lon + rng.gauss(0, 0.01),
                    "altitude": 35.0,
                    "heading": rng.uniform(0, 360),
                }
            elif dt == DataType.SPEED:
                fields = {
                    "speed_mps": round(rng.uniform(0, 40), 1),
                    "speed_limit": rng.choice([30, 50, 70, 130]),
                    "road_type": rng.choice(["urban", "rural", "motorway"]),
                }
            elif dt == DataType.DRIVING_BEHAVIOUR:
                fields = {
                    "score": round(rng.uniform(50, 100), 1),
                    "distance_km": round(rng.uniform(0.5, 30), 1),
                    "duration_minutes": rng.randint(5, 90),
                    "time_of_day_bucket": rng.choice(["morning", "afternoon", "evening", "night"]),
                    "harsh_braking_count": rng.randint(0, 5),
                    "harsh_accel_count": rng.randint(0, 3),
                    "latitude": base_lat + rng.gauss(0, 0.005),
                    "longitude": base_lon + rng.gauss(0, 0.005),
                    "route": f"route-{rng.randint(1, 5)}",
                    "raw_speed_trace": [round(rng.uniform(0, 40), 1) for _ in range(5)],
                    "raw_accel_trace": [round(rng.gauss(0, 0.5), 2) for _ in range(5)],
                }
            elif dt == DataType.SENSOR_HEALTH:
                fields = {
                    "sensor_id": f"sensor-{rng.randint(1, 10):03d}",
                    "status": rng.choice(["nominal", "degraded"]),
                    "temperature": round(rng.uniform(20, 70), 1),
                    "uptime_hours": rng.randint(100, 5000),
                    "error_count": rng.randint(0, 5),
                    "firmware_version": "3.0.0",
                    "serial_number": f"SN-{rng.randint(10000, 99999)}",
                }
            elif dt == DataType.SEALED_EVENT:
                fields = {
                    "trigger_type": rng.choice(["near_miss", "hard_braking"]),
                    "trigger_timestamp": "2026-04-08T09:15:00Z",
                    "window_start": "2026-04-08T09:14:55Z",
                    "window_end": "2026-04-08T09:15:02Z",
                    "camera_frames": ["frame_001.bin"],
                    "lidar_snapshots": ["snap_001.pcd"],
                    "imu_trace": [0.1, -0.2, 0.3],
                    "gnss_trace": [base_lat, base_lon],
                    "speed_trace": [20.0, 10.0, 0.0],
                    "driver_face_crop": "face.jpg",
                    "cabin_audio": "audio.wav",
                }
            elif dt == DataType.DIAGNOSTIC_CODE:
                fields = {
                    "dtc_code": f"P{rng.randint(0, 9)}{rng.randint(100, 999)}",
                    "severity": rng.choice(["info", "warning", "critical"]),
                    "module": rng.choice(["engine", "transmission", "abs"]),
                    "mileage_km": rng.randint(1000, 150000),
                    "vin": f"WBA{rng.randint(10000000000, 99999999999)}",
                    "driver_id": f"driver-{vehicle_seed:03d}",
                }
            elif dt == DataType.CONTACT_SYNC:
                fields = {
                    "device_name": f"Phone-{vehicle_seed}",
                    "mac_address": "AA:BB:CC:DD:EE:FF",
                    "contacts_count": rng.randint(50, 500),
                }
            elif dt == DataType.V2X_CAM:
                fields = {
                    "station_id": f"pseudo-{rng.randint(10000, 99999)}",
                    "latitude": base_lat + rng.gauss(0, 0.005),
                    "longitude": base_lon + rng.gauss(0, 0.005),
                    "speed_mps": round(rng.uniform(0, 30), 1),
                    "heading": round(rng.uniform(0, 360), 1),
                }
            elif dt == DataType.MEDIA_METADATA:
                fields = {
                    "track_title": f"Song {rng.randint(1, 50)}",
                    "artist": f"Artist {rng.randint(1, 10)}",
                }
            elif dt == DataType.ACCELERATION:
                fields = {
                    "longitudinal": round(rng.gauss(0, 2), 2),
                    "lateral": round(rng.gauss(0, 1), 2),
                    "vertical": round(rng.gauss(0, 0.3), 2),
                }
            elif dt == DataType.CAMERA_FRAME:
                fields = {
                    "frame_id": f"cam-{i:05d}",
                    "resolution": "1920x1080",
                    "exposure_ms": rng.randint(5, 30),
                }
            elif dt == DataType.LIDAR_CLOUD:
                fields = {
                    "cloud_id": f"lidar-{i:05d}",
                    "point_count": rng.randint(50000, 200000),
                    "scan_duration_ms": rng.randint(50, 100),
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
                    source=f"vehicle-{vehicle_seed}-{dt.value.lower()}",
                    data_type=dt,
                    fields=fields,
                    channel=channel,
                )
            )

        return records

    return _generate
