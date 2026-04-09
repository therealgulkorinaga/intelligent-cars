"""ROS 2 adapter for the Chambers simulation testbed.

Provides a full interface for ROS 2 topic-based ECU data collection,
Bluetooth pairing simulation, OBD-II diagnostics, and Wi-Fi hotspot
management. Actual ROS 2 dependencies (rclpy) are optional.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

import structlog

from chambers_sim.adapters.base import SimulatorAdapter
from chambers_sim.models.data_record import ChannelType, DataRecord, DataType

logger = structlog.get_logger(__name__)

# Attempt ROS 2 import
try:
    import rclpy  # type: ignore[import-not-found]
    from rclpy.node import Node  # type: ignore[import-not-found]

    ROS2_AVAILABLE = True
except ImportError:
    rclpy = None  # type: ignore[assignment]
    ROS2_AVAILABLE = False


# ---- Topic definitions ----

TOPICS = {
    # Powertrain ECU
    "/powertrain/speed": DataType.SPEED,
    "/powertrain/rpm": DataType.SENSOR_HEALTH,
    "/powertrain/brake_pressure": DataType.ACCELERATION,
    # Body ECU
    "/body/door_status": DataType.SENSOR_HEALTH,
    "/body/lights": DataType.SENSOR_HEALTH,
    "/body/seat_sensors": DataType.SENSOR_HEALTH,
    # ADAS ECU
    "/adas/perception": DataType.CAMERA_FRAME,
    "/adas/collision_warning": DataType.SEALED_EVENT,
    # Infotainment ECU
    "/infotainment/bluetooth_state": DataType.CONTACT_SYNC,
    "/infotainment/media": DataType.MEDIA_METADATA,
    "/infotainment/navigation": DataType.GNSS_POSITION,
    # Telematics ECU
    "/telematics/cellular": DataType.SENSOR_HEALTH,
    "/telematics/ota_status": DataType.DIAGNOSTIC_CODE,
}


def _ecu_message_to_record(
    topic: str,
    msg_data: dict[str, Any],
    session_id: str,
) -> DataRecord:
    """Convert a ROS 2 topic message into a Chambers DataRecord."""
    data_type = TOPICS.get(topic, DataType.SENSOR_HEALTH)

    # Determine channel from topic prefix
    channel = ChannelType.CELLULAR
    if "/infotainment/bluetooth" in topic:
        channel = ChannelType.BLUETOOTH
    elif "/infotainment/" in topic:
        channel = ChannelType.WIFI
    elif "/telematics/" in topic:
        channel = ChannelType.CELLULAR

    return DataRecord(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc),
        source=f"ros2:{topic}",
        data_type=data_type,
        fields=msg_data,
        channel=channel,
    )


# ---- Bluetooth Pairing Simulation ----


@dataclass
class BluetoothPairingSession:
    """Simulates a phone connecting via Bluetooth to the vehicle infotainment."""

    phone_id: str
    synced_contacts: list[dict[str, str]] = field(default_factory=list)
    call_history: list[dict[str, Any]] = field(default_factory=list)
    media_metadata: list[dict[str, str]] = field(default_factory=list)
    connected_at: float = field(default_factory=time.time)
    _disconnected: bool = False

    def pair(self, contacts: list[dict[str, str]] | None = None) -> list[DataRecord]:
        """Simulate initial pairing and contact sync."""
        self.synced_contacts = contacts or [
            {"name": "Alice Smith", "phone": "+44700100200"},
            {"name": "Bob Jones", "phone": "+44700300400"},
            {"name": "Carol White", "phone": "+44700500600"},
        ]
        records = []
        # Generate a ContactSync record for each synced batch
        records.append(
            DataRecord(
                session_id="",
                timestamp=datetime.now(timezone.utc),
                source=f"bluetooth:{self.phone_id}",
                data_type=DataType.CONTACT_SYNC,
                fields={
                    "phone_id_hash": hashlib.sha256(self.phone_id.encode()).hexdigest()[:16],
                    "contact_count": len(self.synced_contacts),
                    "sync_type": "full",
                    "contacts": self.synced_contacts,
                },
                channel=ChannelType.BLUETOOTH,
            )
        )
        return records

    def play_media(self, title: str, artist: str, album: str = "") -> DataRecord:
        """Simulate media playback metadata."""
        meta = {"title": title, "artist": artist, "album": album}
        self.media_metadata.append(meta)
        return DataRecord(
            session_id="",
            timestamp=datetime.now(timezone.utc),
            source=f"bluetooth:{self.phone_id}",
            data_type=DataType.MEDIA_METADATA,
            fields=meta,
            channel=ChannelType.BLUETOOTH,
        )

    def add_call(self, number: str, direction: str = "incoming", duration_s: int = 60) -> DataRecord:
        """Simulate a call event."""
        call = {
            "number_hash": hashlib.sha256(number.encode()).hexdigest()[:16],
            "direction": direction,
            "duration_seconds": duration_s,
        }
        self.call_history.append(call)
        return DataRecord(
            session_id="",
            timestamp=datetime.now(timezone.utc),
            source=f"bluetooth:{self.phone_id}",
            data_type=DataType.CONTACT_SYNC,
            fields=call,
            channel=ChannelType.BLUETOOTH,
        )

    def disconnect(self) -> list[DataRecord]:
        """Simulate phone disconnection -- signals session boundary."""
        self._disconnected = True
        return [
            DataRecord(
                session_id="",
                timestamp=datetime.now(timezone.utc),
                source=f"bluetooth:{self.phone_id}",
                data_type=DataType.CONTACT_SYNC,
                fields={
                    "phone_id_hash": hashlib.sha256(self.phone_id.encode()).hexdigest()[:16],
                    "event": "disconnect",
                    "contacts_to_purge": len(self.synced_contacts),
                    "call_history_to_purge": len(self.call_history),
                    "media_to_purge": len(self.media_metadata),
                },
                channel=ChannelType.BLUETOOTH,
            )
        ]

    @property
    def is_connected(self) -> bool:
        return not self._disconnected


# ---- OBD-II Diagnostic Simulation ----


@dataclass
class ObdDiagnosticHandler:
    """Simulates OBD-II diagnostic port responses.

    Handles standard PID requests with authentication awareness.
    """

    authenticated: bool = False
    _dtc_codes: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Pre-populate some typical DTCs
        self._dtc_codes = [
            {"dtc_code": "P0300", "severity": "moderate", "module": "engine", "description": "Random/Multiple Cylinder Misfire"},
            {"dtc_code": "P0171", "severity": "low", "module": "fuel_system", "description": "System Too Lean Bank 1"},
        ]

    # Standard OBD-II PIDs
    _PID_RESPONSES: ClassVar[dict[str, dict[str, Any]]] = {
        "0x0C": {"name": "engine_rpm", "value": 2500, "unit": "rpm"},
        "0x0D": {"name": "vehicle_speed", "value": 60, "unit": "km/h"},
        "0x05": {"name": "coolant_temp", "value": 90, "unit": "celsius"},
        "0x0F": {"name": "intake_air_temp", "value": 25, "unit": "celsius"},
        "0x11": {"name": "throttle_position", "value": 35, "unit": "percent"},
        "0x2F": {"name": "fuel_level", "value": 72, "unit": "percent"},
        "0x46": {"name": "ambient_temp", "value": 22, "unit": "celsius"},
        "0x51": {"name": "fuel_type", "value": 1, "unit": "enum"},
    }

    def authenticate(self, key: str) -> bool:
        """Authenticate for extended diagnostic access."""
        # Simple check: any non-empty key authenticates
        self.authenticated = bool(key)
        return self.authenticated

    def request_pid(self, pid: str, session_id: str = "") -> DataRecord | None:
        """Respond to a standard OBD-II PID request."""
        pid_data = self._PID_RESPONSES.get(pid)
        if pid_data is None:
            return None

        return DataRecord(
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            source=f"obd2:pid:{pid}",
            data_type=DataType.DIAGNOSTIC_CODE,
            fields={
                "pid": pid,
                **pid_data,
                "authenticated": self.authenticated,
            },
            channel=ChannelType.OBD_II,
        )

    def get_dtc_codes(self, session_id: str = "") -> list[DataRecord]:
        """Return stored diagnostic trouble codes."""
        records = []
        for dtc in self._dtc_codes:
            records.append(
                DataRecord(
                    session_id=session_id,
                    timestamp=datetime.now(timezone.utc),
                    source="obd2:dtc",
                    data_type=DataType.DIAGNOSTIC_CODE,
                    fields={**dtc, "authenticated": self.authenticated},
                    channel=ChannelType.OBD_II,
                )
            )
        return records

    def clear_dtc_codes(self) -> bool:
        """Clear DTCs (requires authentication)."""
        if not self.authenticated:
            logger.warning("dtc_clear_rejected", reason="not_authenticated")
            return False
        self._dtc_codes.clear()
        return True

    def add_dtc(self, dtc_code: str, severity: str, module: str, description: str = "") -> None:
        """Inject a new DTC for testing."""
        self._dtc_codes.append({
            "dtc_code": dtc_code,
            "severity": severity,
            "module": module,
            "description": description,
        })


# ---- Wi-Fi Hotspot Simulation ----


@dataclass
class ConnectedDevice:
    """A device connected to the vehicle's Wi-Fi hotspot."""

    device_id: str
    mac_hash: str
    connected_at: float = field(default_factory=time.time)
    bytes_up: int = 0
    bytes_down: int = 0


class WiFiHotspotManager:
    """Simulates the vehicle Wi-Fi hotspot with passthrough-only policy.

    No content inspection or logging of traffic content -- only connection
    metadata is tracked for the Chambers manifest.
    """

    def __init__(self, max_devices: int = 8) -> None:
        self.max_devices = max_devices
        self._devices: dict[str, ConnectedDevice] = {}

    def connect_device(self, device_id: str) -> DataRecord:
        """Register a device connecting to the hotspot."""
        mac_hash = hashlib.sha256(device_id.encode()).hexdigest()[:12]
        device = ConnectedDevice(device_id=device_id, mac_hash=mac_hash)
        self._devices[device_id] = device
        logger.info("wifi_device_connected", device_id=device_id)

        return DataRecord(
            session_id="",
            timestamp=datetime.now(timezone.utc),
            source="wifi:hotspot",
            data_type=DataType.SENSOR_HEALTH,
            fields={
                "event": "device_connect",
                "mac_hash": mac_hash,
                "connected_devices": len(self._devices),
                "max_devices": self.max_devices,
                # Passthrough-only: no content metadata
                "policy": "passthrough_only",
            },
            channel=ChannelType.WIFI,
        )

    def disconnect_device(self, device_id: str) -> DataRecord | None:
        """Unregister a device from the hotspot."""
        device = self._devices.pop(device_id, None)
        if device is None:
            return None

        duration = time.time() - device.connected_at
        return DataRecord(
            session_id="",
            timestamp=datetime.now(timezone.utc),
            source="wifi:hotspot",
            data_type=DataType.SENSOR_HEALTH,
            fields={
                "event": "device_disconnect",
                "mac_hash": device.mac_hash,
                "session_duration_s": round(duration, 1),
                "bytes_up": device.bytes_up,
                "bytes_down": device.bytes_down,
                "connected_devices": len(self._devices),
                "policy": "passthrough_only",
            },
            channel=ChannelType.WIFI,
        )

    def update_traffic(self, device_id: str, bytes_up: int, bytes_down: int) -> None:
        """Update traffic counters (no content inspection)."""
        device = self._devices.get(device_id)
        if device:
            device.bytes_up += bytes_up
            device.bytes_down += bytes_down

    def get_status(self) -> dict[str, Any]:
        """Return hotspot status summary."""
        return {
            "connected_devices": len(self._devices),
            "max_devices": self.max_devices,
            "policy": "passthrough_only",
            "devices": [
                {"mac_hash": d.mac_hash, "bytes_up": d.bytes_up, "bytes_down": d.bytes_down}
                for d in self._devices.values()
            ],
        }

    @property
    def connected_count(self) -> int:
        return len(self._devices)


# ---- ROS 2 Adapter ----


class Ros2Adapter(SimulatorAdapter):
    """Adapter that bridges ROS 2 ECU topics to the Chambers gateway.

    When ROS 2 (rclpy) is not installed, operates in simulation mode
    generating synthetic ECU data.
    """

    def __init__(
        self,
        gateway_url: str = "http://localhost:8080",
        namespace: str = "/chambers",
    ) -> None:
        self.gateway_url = gateway_url
        self.namespace = namespace
        self._node: Any = None
        self._session_id: str = ""
        self._vehicle_id: str = ""
        self._subscribers: dict[str, Any] = {}
        self._message_queue: list[DataRecord] = []

        # Sub-components
        self.bluetooth = BluetoothPairingSession(phone_id="")
        self.obd = ObdDiagnosticHandler()
        self.wifi = WiFiHotspotManager()

        self._running = False
        self._step_count = 0

    async def connect(self) -> None:
        """Initialize ROS 2 node or enter simulation mode."""
        if ROS2_AVAILABLE:
            rclpy.init()
            self._node = Node("chambers_sim_adapter", namespace=self.namespace)
            # Subscribe to all defined topics
            for topic in TOPICS:
                sub = self._node.create_subscription(
                    msg_type=self._get_msg_type(topic),
                    topic=topic,
                    callback=lambda msg, t=topic: self._on_message(t, msg),
                    qos_profile=10,
                )
                self._subscribers[topic] = sub
            logger.info("ros2_node_created", namespace=self.namespace)
        else:
            logger.info("ros2_simulation_mode", reason="rclpy not available")

    @staticmethod
    def _get_msg_type(topic: str) -> Any:
        """Return the ROS 2 message type for a topic (when rclpy is available)."""
        if not ROS2_AVAILABLE:
            return None
        # In a real deployment, this would map to specific ROS 2 message types.
        # For now, use a generic string message as a placeholder.
        from std_msgs.msg import String  # type: ignore[import-not-found]

        return String

    def _on_message(self, topic: str, msg: Any) -> None:
        """ROS 2 subscription callback."""
        # Convert ROS message to dict
        if hasattr(msg, "data"):
            import json as _json

            try:
                msg_data = _json.loads(msg.data)
            except (ValueError, AttributeError):
                msg_data = {"raw": str(msg.data)}
        else:
            msg_data = {"raw": str(msg)}

        record = _ecu_message_to_record(topic, msg_data, self._session_id)
        self._message_queue.append(record)

    async def start_session(self, vehicle_id: str) -> str:
        """Start a data session."""
        self._vehicle_id = vehicle_id
        self._session_id = f"ros2-{vehicle_id}-{uuid.uuid4().hex[:8]}"
        logger.info("session_started", vehicle_id=vehicle_id, session_id=self._session_id)
        return self._session_id

    async def get_records(self, vehicle_id: str) -> list[DataRecord]:
        """Drain the message queue and return pending records."""
        if ROS2_AVAILABLE and self._node:
            rclpy.spin_once(self._node, timeout_sec=0.01)

        records = list(self._message_queue)
        self._message_queue.clear()
        return records

    async def end_session(self, vehicle_id: str) -> None:
        """End session and clean up."""
        # Disconnect bluetooth if active
        if self.bluetooth.is_connected:
            disconnect_records = self.bluetooth.disconnect()
            self._message_queue.extend(disconnect_records)

        if ROS2_AVAILABLE and self._node:
            self._node.destroy_node()
            rclpy.shutdown()

        logger.info("session_ended", session_id=self._session_id)
        self._session_id = ""

    def _generate_synthetic_step(self) -> list[DataRecord]:
        """Generate synthetic ECU data for one simulation step (no ROS 2)."""
        import math
        import random

        now = datetime.now(timezone.utc)
        records: list[DataRecord] = []
        t = self._step_count

        # Powertrain: speed follows a sinusoidal pattern
        speed_kmh = 50 + 30 * math.sin(t * 0.05) + random.gauss(0, 2)
        speed_kmh = max(0, speed_kmh)
        records.append(
            DataRecord(
                session_id=self._session_id,
                timestamp=now,
                source="ros2:/powertrain/speed",
                data_type=DataType.SPEED,
                fields={
                    "speed_mps": round(speed_kmh / 3.6, 3),
                    "speed_kmh": round(speed_kmh, 2),
                    "road_type": "urban",
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Powertrain: RPM
        rpm = speed_kmh * 40 + random.gauss(0, 50)
        records.append(
            DataRecord(
                session_id=self._session_id,
                timestamp=now,
                source="ros2:/powertrain/rpm",
                data_type=DataType.SENSOR_HEALTH,
                fields={
                    "sensor_id": "engine_rpm",
                    "status": "ok",
                    "rpm": round(rpm),
                    "temperature": round(88 + random.gauss(0, 2), 1),
                    "uptime_hours": round(t / 3600, 2),
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Brake pressure (every 5 steps)
        if t % 5 == 0:
            brake_pressure = max(0, random.gauss(20, 15))
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="ros2:/powertrain/brake_pressure",
                    data_type=DataType.ACCELERATION,
                    fields={
                        "longitudinal": round(-brake_pressure * 0.1, 3),
                        "lateral": round(random.gauss(0, 0.5), 3),
                        "vertical": round(random.gauss(0, 0.1), 3),
                        "brake_pressure_bar": round(brake_pressure, 1),
                    },
                    channel=ChannelType.CELLULAR,
                )
            )

        # Body: door status (every 60 steps)
        if t % 60 == 0:
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="ros2:/body/door_status",
                    data_type=DataType.SENSOR_HEALTH,
                    fields={
                        "sensor_id": "body_doors",
                        "status": "ok",
                        "front_left": "closed",
                        "front_right": "closed",
                        "rear_left": "closed",
                        "rear_right": "closed",
                        "trunk": "closed",
                    },
                    channel=ChannelType.CELLULAR,
                )
            )

        # Navigation (every 10 steps)
        if t % 10 == 0:
            lat = 51.5074 + t * 0.0001 + random.gauss(0, 0.0001)
            lon = -0.1278 + t * 0.00005 + random.gauss(0, 0.0001)
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="ros2:/infotainment/navigation",
                    data_type=DataType.GNSS_POSITION,
                    fields={
                        "latitude": round(lat, 6),
                        "longitude": round(lon, 6),
                        "altitude": round(30 + random.gauss(0, 1), 1),
                    },
                    channel=ChannelType.WIFI,
                )
            )

        # Telematics cellular status (every 30 steps)
        if t % 30 == 0:
            records.append(
                DataRecord(
                    session_id=self._session_id,
                    timestamp=now,
                    source="ros2:/telematics/cellular",
                    data_type=DataType.SENSOR_HEALTH,
                    fields={
                        "sensor_id": "telematics_modem",
                        "status": "ok",
                        "signal_strength_dbm": round(-70 + random.gauss(0, 5)),
                        "network_type": "5G",
                        "uptime_hours": round(t / 3600, 2),
                    },
                    channel=ChannelType.CELLULAR,
                )
            )

        self._step_count += 1
        return records

    async def run(
        self,
        duration_seconds: float,
        callback: Callable[[list[DataRecord]], Awaitable[Any]] | Callable[[list[DataRecord]], Any],
    ) -> None:
        """Run the ROS 2 adapter loop."""
        self._running = True
        total_steps = int(duration_seconds)

        logger.info("ros2_run_start", total_steps=total_steps, mode="ros2" if ROS2_AVAILABLE else "synthetic")

        for step in range(total_steps):
            if not self._running:
                break

            if ROS2_AVAILABLE and self._node:
                rclpy.spin_once(self._node, timeout_sec=0.1)
                records = list(self._message_queue)
                self._message_queue.clear()
            else:
                records = self._generate_synthetic_step()

            if records:
                result = callback(records)
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result

            await asyncio.sleep(0.01)  # Yield to event loop

        self._running = False
        logger.info("ros2_run_complete")

    def stop(self) -> None:
        """Signal the run loop to stop."""
        self._running = False
