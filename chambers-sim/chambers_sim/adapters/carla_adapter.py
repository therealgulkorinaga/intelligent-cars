"""CARLA simulator adapter for the Chambers simulation testbed.

Works gracefully without CARLA installed -- all CARLA imports are deferred.
"""

from __future__ import annotations

import asyncio
import collections
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from chambers_sim.adapters.base import SimulatorAdapter
from chambers_sim.models.data_record import ChannelType, DataRecord, DataType

logger = structlog.get_logger(__name__)

# Attempt to import CARLA; set flag if unavailable
try:
    import carla as _carla_module

    CARLA_AVAILABLE = True
except ImportError:
    _carla_module = None  # type: ignore[assignment]
    CARLA_AVAILABLE = False


@dataclass
class SealedEventData:
    """Captured sensor data for a sealed event (crash / near-miss)."""

    trigger_type: str
    trigger_timestamp: datetime
    window_start: datetime
    window_end: datetime
    sensor_data: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class SealedEventCapture:
    """Rolling buffer that captures sealed events on collision trigger.

    Maintains a rolling window of the last *pre_seconds* of all sensor data.
    When triggered, freezes the buffer and captures *post_seconds* more data,
    then packages the result as a SealedEvent.
    """

    def __init__(self, pre_seconds: float = 5.0, post_seconds: float = 2.0) -> None:
        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self._buffer: dict[str, collections.deque[dict[str, Any]]] = {}
        self._triggered = False
        self._trigger_time: float | None = None
        self._trigger_type: str = ""
        self._post_buffer: dict[str, list[dict[str, Any]]] = {}
        self._completed_events: list[SealedEventData] = []

    def append(self, sensor_name: str, data: dict[str, Any]) -> None:
        """Add a sensor reading to the rolling buffer or post-capture buffer."""
        now = time.time()
        entry = {**data, "_capture_ts": now}

        if self._triggered:
            # Still capturing post-trigger data
            self._post_buffer.setdefault(sensor_name, []).append(entry)
            if self._trigger_time and (now - self._trigger_time) >= self.post_seconds:
                self._finalize_event()
        else:
            # Normal rolling buffer
            if sensor_name not in self._buffer:
                self._buffer[sensor_name] = collections.deque()
            self._buffer[sensor_name].append(entry)
            # Evict entries older than pre_seconds
            cutoff = now - self.pre_seconds
            while self._buffer[sensor_name] and self._buffer[sensor_name][0]["_capture_ts"] < cutoff:
                self._buffer[sensor_name].popleft()

    def trigger(self, trigger_type: str = "collision") -> None:
        """Trigger a sealed event capture."""
        if self._triggered:
            return
        self._triggered = True
        self._trigger_time = time.time()
        self._trigger_type = trigger_type
        self._post_buffer = {}
        logger.info("sealed_event_triggered", trigger_type=trigger_type)

    def _finalize_event(self) -> None:
        """Package the captured data into a SealedEventData."""
        trigger_ts = datetime.fromtimestamp(self._trigger_time or time.time(), tz=timezone.utc)
        window_start = datetime.fromtimestamp(
            (self._trigger_time or time.time()) - self.pre_seconds, tz=timezone.utc
        )
        window_end = datetime.fromtimestamp(
            (self._trigger_time or time.time()) + self.post_seconds, tz=timezone.utc
        )

        sensor_data: dict[str, list[dict[str, Any]]] = {}
        for sensor_name, deq in self._buffer.items():
            sensor_data[sensor_name] = [
                {k: v for k, v in entry.items() if k != "_capture_ts"} for entry in deq
            ]
        for sensor_name, entries in self._post_buffer.items():
            existing = sensor_data.get(sensor_name, [])
            existing.extend(
                {k: v for k, v in entry.items() if k != "_capture_ts"} for entry in entries
            )
            sensor_data[sensor_name] = existing

        event = SealedEventData(
            trigger_type=self._trigger_type,
            trigger_timestamp=trigger_ts,
            window_start=window_start,
            window_end=window_end,
            sensor_data=sensor_data,
        )
        self._completed_events.append(event)
        logger.info("sealed_event_finalized", trigger_type=self._trigger_type)

        # Reset
        self._triggered = False
        self._trigger_time = None
        self._trigger_type = ""
        self._post_buffer = {}
        # Clear buffer after sealing
        self._buffer.clear()

    def pop_events(self) -> list[SealedEventData]:
        """Return and clear any completed sealed events."""
        events = list(self._completed_events)
        self._completed_events.clear()
        return events


class V2xManager:
    """Simulates V2X CAM (Cooperative Awareness Message) broadcasts.

    Implements pseudonym rotation every *rotation_interval* seconds.
    """

    def __init__(
        self,
        rotation_interval: float = 300.0,
        vehicle_length: float = 4.5,
        vehicle_width: float = 1.8,
    ) -> None:
        self.rotation_interval = rotation_interval
        self.vehicle_length = vehicle_length
        self.vehicle_width = vehicle_width
        self._pseudonym: str = self._generate_pseudonym()
        self._last_rotation: float = time.time()
        self._rotation_count: int = 0

    @staticmethod
    def _generate_pseudonym() -> str:
        return f"v2x-{uuid.uuid4().hex[:12]}"

    def get_cam_message(
        self,
        lat: float,
        lon: float,
        speed: float,
        heading: float,
        session_id: str,
    ) -> tuple[DataRecord, bool]:
        """Generate a CAM message. Returns (record, pseudonym_rotated)."""
        now_ts = time.time()
        rotated = False

        if (now_ts - self._last_rotation) >= self.rotation_interval:
            self._pseudonym = self._generate_pseudonym()
            self._last_rotation = now_ts
            self._rotation_count += 1
            rotated = True
            logger.info("v2x_pseudonym_rotated", new_pseudonym=self._pseudonym)

        record = DataRecord(
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            source=f"v2x:{self._pseudonym}",
            data_type=DataType.V2X_CAM,
            fields={
                "pseudonym": self._pseudonym,
                "latitude": lat,
                "longitude": lon,
                "speed_mps": speed,
                "heading_deg": heading,
                "vehicle_length_m": self.vehicle_length,
                "vehicle_width_m": self.vehicle_width,
                "rotation_count": self._rotation_count,
            },
            channel=ChannelType.V2X,
        )
        return record, rotated

    @property
    def current_pseudonym(self) -> str:
        return self._pseudonym


class CarlaAdapter(SimulatorAdapter):
    """Adapter that bridges CARLA simulator to the Chambers gateway.

    Falls back gracefully when CARLA is not installed.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 2000,
        town: str = "Town01",
        gateway_url: str = "http://localhost:8080",
    ) -> None:
        self.host = host
        self.port = port
        self.town = town
        self.gateway_url = gateway_url
        self._client: Any = None
        self._world: Any = None
        self._ego_vehicle: Any = None
        self._sensors: dict[str, Any] = {}
        self._sensor_data: dict[str, list[dict[str, Any]]] = {}
        self._session_id: str = ""
        self._sealed_capture = SealedEventCapture()
        self._v2x_manager = V2xManager()
        self._running = False

    async def connect(self) -> None:
        """Connect to the CARLA server and load the specified town."""
        if not CARLA_AVAILABLE:
            raise RuntimeError(
                "CARLA Python package is not installed. "
                "Install it with: pip install carla"
            )

        self._client = _carla_module.Client(self.host, self.port)
        self._client.set_timeout(30.0)
        self._world = self._client.load_world(self.town)

        settings = self._world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05  # 20 FPS
        self._world.apply_settings(settings)

        logger.info("carla_connected", host=self.host, port=self.port, town=self.town)

    async def start_session(self, vehicle_id: str) -> str:
        """Start a data session for the ego vehicle."""
        self._session_id = f"carla-{vehicle_id}-{uuid.uuid4().hex[:8]}"
        logger.info("session_started", vehicle_id=vehicle_id, session_id=self._session_id)
        return self._session_id

    def setup_ego_vehicle(self, spawn_point: int = 0) -> None:
        """Spawn the ego vehicle at the given spawn point index."""
        if self._world is None:
            raise RuntimeError("Not connected to CARLA.")

        blueprint_library = self._world.get_blueprint_library()
        vehicle_bp = blueprint_library.filter("vehicle.tesla.model3")[0]

        spawn_points = self._world.get_map().get_spawn_points()
        if spawn_point >= len(spawn_points):
            spawn_point = 0
        transform = spawn_points[spawn_point]

        self._ego_vehicle = self._world.spawn_actor(vehicle_bp, transform)
        self._ego_vehicle.set_autopilot(True)
        logger.info("ego_vehicle_spawned", spawn_point=spawn_point)

    def setup_sensors(self) -> None:
        """Attach the full sensor suite to the ego vehicle."""
        if self._ego_vehicle is None:
            raise RuntimeError("Ego vehicle not spawned.")

        bp_library = self._world.get_blueprint_library()

        # RGB Camera (front)
        camera_bp = bp_library.find("sensor.camera.rgb")
        camera_bp.set_attribute("image_size_x", "1920")
        camera_bp.set_attribute("image_size_y", "1080")
        camera_bp.set_attribute("fov", "90")
        camera_transform = _carla_module.Transform(
            _carla_module.Location(x=1.5, z=2.4)
        )
        camera = self._world.spawn_actor(camera_bp, camera_transform, attach_to=self._ego_vehicle)
        camera.listen(self._on_camera_image)
        self._sensors["camera_front"] = camera

        # LiDAR (roof)
        lidar_bp = bp_library.find("sensor.lidar.ray_cast")
        lidar_bp.set_attribute("channels", "64")
        lidar_bp.set_attribute("range", "100.0")
        lidar_bp.set_attribute("points_per_second", "1200000")
        lidar_bp.set_attribute("rotation_frequency", "20")
        lidar_transform = _carla_module.Transform(
            _carla_module.Location(x=0.0, z=2.8)
        )
        lidar = self._world.spawn_actor(lidar_bp, lidar_transform, attach_to=self._ego_vehicle)
        lidar.listen(self._on_lidar_measurement)
        self._sensors["lidar_roof"] = lidar

        # GNSS
        gnss_bp = bp_library.find("sensor.other.gnss")
        gnss_transform = _carla_module.Transform(_carla_module.Location(x=0.0, z=1.5))
        gnss = self._world.spawn_actor(gnss_bp, gnss_transform, attach_to=self._ego_vehicle)
        gnss.listen(self._on_gnss_measurement)
        self._sensors["gnss"] = gnss

        # IMU
        imu_bp = bp_library.find("sensor.other.imu")
        imu_transform = _carla_module.Transform(_carla_module.Location(x=0.0, z=0.5))
        imu = self._world.spawn_actor(imu_bp, imu_transform, attach_to=self._ego_vehicle)
        imu.listen(self._on_imu_measurement)
        self._sensors["imu"] = imu

        # Collision detector
        collision_bp = bp_library.find("sensor.other.collision")
        collision = self._world.spawn_actor(
            collision_bp,
            _carla_module.Transform(),
            attach_to=self._ego_vehicle,
        )
        collision.listen(self._on_collision)
        self._sensors["collision"] = collision

        logger.info("sensors_attached", sensors=list(self._sensors.keys()))

    # ---- Sensor Callbacks ----

    def _on_camera_image(self, image: Any) -> None:
        """Process camera frame -- store metadata, not raw pixels."""
        data = {
            "width": image.width,
            "height": image.height,
            "fov": image.fov,
            "timestamp": image.timestamp,
            "frame_number": image.frame,
        }
        self._sensor_data.setdefault("camera_front", []).append(data)
        self._sealed_capture.append("camera_front", data)

    def _on_lidar_measurement(self, measurement: Any) -> None:
        """Process LiDAR measurement -- store summary statistics."""
        raw_data = measurement.raw_data
        point_count = len(raw_data) // 16  # Each point is 16 bytes (x,y,z,intensity)

        # Compute range statistics from channels
        channels = measurement.channels
        horizontal_angle = measurement.horizontal_angle

        data = {
            "point_count": point_count,
            "channels": channels,
            "horizontal_angle": horizontal_angle,
            "timestamp": measurement.timestamp,
            "frame_number": measurement.frame,
            "min_range_m": 0.5,
            "max_range_m": 100.0,
        }
        self._sensor_data.setdefault("lidar_roof", []).append(data)
        self._sealed_capture.append("lidar_roof", data)

    def _on_gnss_measurement(self, measurement: Any) -> None:
        """Process GNSS measurement."""
        data = {
            "latitude": measurement.latitude,
            "longitude": measurement.longitude,
            "altitude": measurement.altitude,
            "timestamp": measurement.timestamp,
        }
        self._sensor_data.setdefault("gnss", []).append(data)
        self._sealed_capture.append("gnss", data)

    def _on_imu_measurement(self, measurement: Any) -> None:
        """Process IMU measurement."""
        accel = measurement.accelerometer
        gyro = measurement.gyroscope

        data = {
            "accelerometer_x": accel.x,
            "accelerometer_y": accel.y,
            "accelerometer_z": accel.z,
            "gyroscope_x": gyro.x,
            "gyroscope_y": gyro.y,
            "gyroscope_z": gyro.z,
            "compass": measurement.compass,
            "timestamp": measurement.timestamp,
        }
        self._sensor_data.setdefault("imu", []).append(data)
        self._sealed_capture.append("imu", data)

    def _on_collision(self, event: Any) -> None:
        """Handle collision event -- triggers sealed event capture."""
        other_actor = event.other_actor
        impulse = event.normal_impulse
        intensity = (impulse.x**2 + impulse.y**2 + impulse.z**2) ** 0.5

        logger.warning(
            "collision_detected",
            other_actor=other_actor.type_id if other_actor else "unknown",
            intensity=intensity,
        )
        self._sealed_capture.trigger(trigger_type="collision")

    # ---- DataRecord conversion ----

    def _flush_sensor_records(self) -> list[DataRecord]:
        """Convert buffered sensor data to DataRecord objects and clear buffers."""
        records: list[DataRecord] = []
        now = datetime.now(timezone.utc)

        # Camera frames
        for frame_data in self._sensor_data.get("camera_front", []):
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="carla:camera_front",
                    data_type=DataType.CAMERA_FRAME,
                    fields=frame_data,
                    channel=ChannelType.CELLULAR,
                )
            )

        # LiDAR
        for lidar_data in self._sensor_data.get("lidar_roof", []):
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="carla:lidar_roof",
                    data_type=DataType.LIDAR_CLOUD,
                    fields=lidar_data,
                    channel=ChannelType.CELLULAR,
                )
            )

        # GNSS
        for gnss_data in self._sensor_data.get("gnss", []):
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="carla:gnss",
                    data_type=DataType.GNSS_POSITION,
                    fields=gnss_data,
                    channel=ChannelType.CELLULAR,
                )
            )

        # IMU
        for imu_data in self._sensor_data.get("imu", []):
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="carla:imu",
                    data_type=DataType.IMU_READING,
                    fields=imu_data,
                    channel=ChannelType.CELLULAR,
                )
            )

        # Sealed events
        for event in self._sealed_capture.pop_events():
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=event.trigger_timestamp,
                    source="carla:sealed_event",
                    data_type=DataType.SEALED_EVENT,
                    fields={
                        "trigger_type": event.trigger_type,
                        "trigger_timestamp": event.trigger_timestamp.isoformat(),
                        "window_start": event.window_start.isoformat(),
                        "window_end": event.window_end.isoformat(),
                        "camera_frames": event.sensor_data.get("camera_front", []),
                        "lidar_snapshots": event.sensor_data.get("lidar_roof", []),
                        "imu_trace": event.sensor_data.get("imu", []),
                        "gnss_trace": event.sensor_data.get("gnss", []),
                        "speed_trace": [],
                    },
                    channel=ChannelType.CELLULAR,
                )
            )

        # V2X CAM
        gnss_entries = self._sensor_data.get("gnss", [])
        if gnss_entries:
            latest_gnss = gnss_entries[-1]
            imu_entries = self._sensor_data.get("imu", [])
            heading = imu_entries[-1].get("compass", 0.0) if imu_entries else 0.0
            cam_record, rotated = self._v2x_manager.get_cam_message(
                lat=latest_gnss["latitude"],
                lon=latest_gnss["longitude"],
                speed=0.0,
                heading=heading,
                session_id=self._session_id,
            )
            records.append(cam_record)

        self._sensor_data.clear()
        return records

    async def get_records(self, vehicle_id: str) -> list[DataRecord]:
        """Retrieve buffered records for the ego vehicle."""
        return self._flush_sensor_records()

    async def end_session(self, vehicle_id: str) -> None:
        """Clean up sensors and destroy ego vehicle."""
        for name, sensor in self._sensors.items():
            try:
                sensor.stop()
                sensor.destroy()
            except Exception as exc:
                logger.warning("sensor_cleanup_failed", sensor=name, error=str(exc))
        self._sensors.clear()

        if self._ego_vehicle is not None:
            try:
                self._ego_vehicle.destroy()
            except Exception:
                pass
            self._ego_vehicle = None

        logger.info("session_ended", session_id=self._session_id)
        self._session_id = ""

    async def run(
        self,
        duration_seconds: float,
        callback: Callable[[list[DataRecord]], Awaitable[Any]] | Callable[[list[DataRecord]], Any],
    ) -> None:
        """Run the CARLA simulation for the given duration."""
        if self._world is None:
            raise RuntimeError("Not connected. Call connect() first.")
        if self._ego_vehicle is None:
            raise RuntimeError("Ego vehicle not spawned. Call setup_ego_vehicle() first.")

        self._running = True
        step_dt = 0.05  # 20 FPS
        total_steps = int(duration_seconds / step_dt)

        logger.info("carla_run_start", total_steps=total_steps, duration=duration_seconds)

        for step in range(total_steps):
            if not self._running:
                break

            self._world.tick()

            # Flush sensor data every 20 steps (~1 second)
            if step > 0 and step % 20 == 0:
                records = self._flush_sensor_records()
                if records:
                    result = callback(records)
                    if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                        await result

            # Small yield to let asyncio event loop breathe
            await asyncio.sleep(0)

        self._running = False
        logger.info("carla_run_complete", steps_executed=total_steps)

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._running = False
