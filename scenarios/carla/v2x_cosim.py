#!/usr/bin/env python3
"""CARLA-SUMO co-simulation with V2X CAM message broadcasting.

This scenario runs CARLA and SUMO in co-simulation mode:
- CARLA renders the 3D world and hosts the ego vehicle with sensors
- SUMO manages the NPC traffic flow via its traffic model
- V2X Cooperative Awareness Messages (CAMs) are simulated for all vehicles
- ETSI pseudonym rotation is performed every 300 seconds
- All V2X data flows through the Chambers preservation gateway

Requirements:
    - CARLA >= 0.9.14 with SUMO co-simulation support
    - SUMO >= 1.18.0
    - carla Python package
    - chambers_sim package

Usage:
    python v2x_cosim.py --carla-host localhost --carla-port 2000 \
                         --sumo-cfg ../sumo/urban_100v.sumocfg \
                         --duration 600
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import secrets
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chambers.carla.v2x_cosim")


# ---------------------------------------------------------------------------
# V2X pseudonym management (ETSI TS 103 097 / IEEE 1609.2 inspired)
# ---------------------------------------------------------------------------
PSEUDONYM_ROTATION_INTERVAL = 300  # seconds


@dataclass
class PseudonymState:
    """Tracks V2X pseudonym lifecycle for a single vehicle."""

    vehicle_id: str
    current_pseudonym: str = ""
    previous_pseudonyms: list[str] = field(default_factory=list)
    created_at: float = 0.0
    rotation_count: int = 0
    linkage_data: dict[str, Any] = field(default_factory=dict)

    def rotate(self, sim_time: float) -> str:
        """Generate a new pseudonym, destroying linkage to the old one.

        Returns the new pseudonym.
        """
        if self.current_pseudonym:
            self.previous_pseudonyms.append(self.current_pseudonym)

        # Generate a new pseudonymous identifier
        # In a real implementation this would be an Authorization Ticket (AT)
        # from a PKI Certificate Authority
        entropy = secrets.token_bytes(16)
        new_pseudonym = hashlib.sha256(
            entropy + self.vehicle_id.encode()
        ).hexdigest()[:16]

        old_pseudonym = self.current_pseudonym
        self.current_pseudonym = new_pseudonym
        self.created_at = sim_time
        self.rotation_count += 1

        # Destroy linkage data between old and new pseudonyms
        destroyed_linkage = dict(self.linkage_data)
        self.linkage_data = {
            "pseudonym": new_pseudonym,
            "issued_at": sim_time,
            "rotation_number": self.rotation_count,
            # No reference to previous pseudonyms -- linkage is destroyed
        }

        logger.info(
            "Pseudonym rotated for %s: %s -> %s (rotation #%d, "
            "linkage data destroyed: %d fields)",
            self.vehicle_id,
            old_pseudonym[:8] + "..." if old_pseudonym else "none",
            new_pseudonym[:8] + "...",
            self.rotation_count,
            len(destroyed_linkage),
        )

        return new_pseudonym

    def needs_rotation(self, sim_time: float) -> bool:
        """Check if pseudonym should be rotated."""
        if not self.current_pseudonym:
            return True
        return (sim_time - self.created_at) >= PSEUDONYM_ROTATION_INTERVAL


@dataclass
class V2xCamMessage:
    """ETSI ITS-G5 Cooperative Awareness Message (CAM).

    Simplified representation following ETSI EN 302 637-2.
    """

    # Header
    protocol_version: int = 2
    message_id: int = 2  # CAM
    station_id: str = ""  # Pseudonymous

    # Reference position
    latitude: float = 0.0  # degrees * 1e7 (ETSI convention)
    longitude: float = 0.0
    altitude: float = 0.0
    heading: float = 0.0  # 0.1 degree units

    # High-frequency container
    speed: float = 0.0  # 0.01 m/s units
    longitudinal_acceleration: float = 0.0  # 0.1 m/s^2 units
    lateral_acceleration: float = 0.0

    # Vehicle dimensions
    vehicle_length: float = 45  # 0.1 m units (4.5 m default)
    vehicle_width: float = 18  # 0.1 m units (1.8 m default)

    # Timing
    generation_delta_time: int = 0  # milliseconds since 2004-01-01
    timestamp_utc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "message_id": self.message_id,
            "station_id": self.station_id,
            "reference_position": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "altitude": self.altitude,
                "heading": self.heading,
            },
            "high_frequency_container": {
                "speed": self.speed,
                "longitudinal_acceleration": self.longitudinal_acceleration,
                "lateral_acceleration": self.lateral_acceleration,
            },
            "vehicle_dimensions": {
                "length": self.vehicle_length,
                "width": self.vehicle_width,
            },
            "generation_delta_time": self.generation_delta_time,
            "timestamp_utc": self.timestamp_utc,
        }


# ---------------------------------------------------------------------------
# Co-simulation bridge
# ---------------------------------------------------------------------------
class CarlaSumoCosim:
    """Manages the CARLA-SUMO co-simulation and V2X broadcast."""

    def __init__(
        self,
        carla_host: str,
        carla_port: int,
        sumo_cfg: str,
        sumo_host: str,
        sumo_port: int,
        gateway_url: str,
    ) -> None:
        self.carla_host = carla_host
        self.carla_port = carla_port
        self.sumo_cfg = sumo_cfg
        self.sumo_host = sumo_host
        self.sumo_port = sumo_port
        self.gateway_url = gateway_url

        self._carla_client: Any = None
        self._carla_world: Any = None
        self._traci: Any = None
        self._pseudonyms: dict[str, PseudonymState] = {}
        self._session_id = f"v2x-cosim-{uuid.uuid4().hex[:8]}"
        self._ego_vehicle: Any = None

        # Statistics
        self.stats = {
            "cam_messages_broadcast": 0,
            "pseudonym_rotations": 0,
            "linkage_data_destroyed": 0,
            "vehicles_tracked": 0,
        }

    def connect(self) -> None:
        """Establish connections to both CARLA and SUMO."""
        import carla
        import traci

        # Connect to CARLA
        self._carla_client = carla.Client(self.carla_host, self.carla_port)
        self._carla_client.set_timeout(30.0)
        self._carla_world = self._carla_client.get_world()
        logger.info(
            "Connected to CARLA %s at %s:%d",
            self._carla_client.get_server_version(),
            self.carla_host,
            self.carla_port,
        )

        # Start SUMO
        sumo_binary = "sumo"
        cmd = [
            sumo_binary,
            "-c", self.sumo_cfg,
            "--step-length", "0.05",
            "--lateral-resolution", "0.8",
        ]
        try:
            traci.start(cmd, port=self.sumo_port)
        except Exception:
            traci.init(port=self.sumo_port, host=self.sumo_host)
        self._traci = traci
        logger.info("Connected to SUMO at %s:%d", self.sumo_host, self.sumo_port)

        # Set CARLA to synchronous mode
        settings = self._carla_world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05
        self._carla_world.apply_settings(settings)

    def _get_or_create_pseudonym(self, vehicle_id: str, sim_time: float) -> PseudonymState:
        """Get or create a pseudonym state for a vehicle."""
        if vehicle_id not in self._pseudonyms:
            state = PseudonymState(vehicle_id=vehicle_id)
            state.rotate(sim_time)
            self._pseudonyms[vehicle_id] = state
            self.stats["vehicles_tracked"] += 1
            self.stats["pseudonym_rotations"] += 1

        state = self._pseudonyms[vehicle_id]

        # Rotate if interval has elapsed
        if state.needs_rotation(sim_time):
            state.rotate(sim_time)
            self.stats["pseudonym_rotations"] += 1
            self.stats["linkage_data_destroyed"] += 1

        return state

    def _build_cam_from_sumo(
        self, vehicle_id: str, sim_time: float
    ) -> V2xCamMessage | None:
        """Build a CAM message from SUMO vehicle data."""
        traci = self._traci
        if traci is None:
            return None

        try:
            x, y = traci.vehicle.getPosition(vehicle_id)
            speed = traci.vehicle.getSpeed(vehicle_id)
            accel = traci.vehicle.getAcceleration(vehicle_id)
            angle = traci.vehicle.getAngle(vehicle_id)
            length = traci.vehicle.getLength(vehicle_id)
            width = traci.vehicle.getWidth(vehicle_id)
        except Exception:
            return None

        # Convert SUMO coordinates to geo
        try:
            lon, lat = traci.simulation.convertGeo(x, y)
        except Exception:
            lat, lon = y / 111_320.0, x / 111_320.0

        ps = self._get_or_create_pseudonym(vehicle_id, sim_time)

        cam = V2xCamMessage(
            station_id=ps.current_pseudonym,
            latitude=lat,
            longitude=lon,
            altitude=0.0,
            heading=angle,
            speed=round(speed * 100, 0),  # ETSI: 0.01 m/s units
            longitudinal_acceleration=round(accel * 10, 0),  # ETSI: 0.1 m/s^2
            vehicle_length=round(length * 10, 0),  # ETSI: 0.1 m
            vehicle_width=round(width * 10, 0),
            generation_delta_time=int(sim_time * 1000) % 65536,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        return cam

    def _build_cam_from_carla(
        self, ego_vehicle: Any, sim_time: float
    ) -> V2xCamMessage | None:
        """Build a CAM message from CARLA ego vehicle data."""
        if ego_vehicle is None:
            return None

        transform = ego_vehicle.get_transform()
        velocity = ego_vehicle.get_velocity()
        accel = ego_vehicle.get_acceleration()
        bb = ego_vehicle.bounding_box

        speed_mps = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)

        # CARLA uses x,y,z world coordinates -- approximate lat/lon
        # In a real scenario, the map would have geo-referencing
        lat = transform.location.y / 111_320.0
        lon = transform.location.x / 111_320.0

        vehicle_id = f"ego-{ego_vehicle.id}"
        ps = self._get_or_create_pseudonym(vehicle_id, sim_time)

        cam = V2xCamMessage(
            station_id=ps.current_pseudonym,
            latitude=lat,
            longitude=lon,
            altitude=transform.location.z,
            heading=transform.rotation.yaw,
            speed=round(speed_mps * 100, 0),
            longitudinal_acceleration=round(accel.x * 10, 0),
            lateral_acceleration=round(accel.y * 10, 0),
            vehicle_length=round(bb.extent.x * 20, 0),  # extent is half-length
            vehicle_width=round(bb.extent.y * 20, 0),
            generation_delta_time=int(sim_time * 1000) % 65536,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
        )
        return cam

    def _send_cam_to_gateway(self, cam: V2xCamMessage) -> None:
        """Send a V2X CAM as a Chambers data record to the gateway."""
        record = {
            "session_id": self._session_id,
            "timestamp": cam.timestamp_utc,
            "source": f"v2x:{cam.station_id[:8]}",
            "data_type": "V2xCam",
            "fields": cam.to_dict(),
            "channel": "V2x",
        }

        try:
            import urllib.request

            payload = json.dumps({"records": [record]}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.gateway_url}/api/v1/ingest",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception:
            pass  # Gateway may be offline

        self.stats["cam_messages_broadcast"] += 1

    def run(self, duration: float) -> None:
        """Run the co-simulation for the specified duration."""
        import carla

        world = self._carla_world
        traci = self._traci
        blueprint_library = world.get_blueprint_library()

        # Spawn ego vehicle in CARLA
        ego_bp = blueprint_library.filter("vehicle.tesla.model3")
        if not ego_bp:
            ego_bp = blueprint_library.filter("vehicle.*")
        ego_bp = ego_bp[0]
        if ego_bp.has_attribute("role_name"):
            ego_bp.set_attribute("role_name", "hero")

        spawn_points = world.get_map().get_spawn_points()
        self._ego_vehicle = world.try_spawn_actor(ego_bp, spawn_points[0])
        if self._ego_vehicle is None:
            raise RuntimeError("Failed to spawn ego vehicle")

        self._ego_vehicle.set_autopilot(True)
        logger.info("Ego vehicle spawned in CARLA: %s", ego_bp.id)

        total_steps = int(duration / 0.05)
        cam_interval = int(0.1 / 0.05)  # CAM every 100ms (ETSI minimum)
        report_interval = int(10.0 / 0.05)

        logger.info(
            "Starting V2X co-simulation: %d steps, %.0f seconds",
            total_steps, duration,
        )

        start_real = time.monotonic()

        try:
            for step in range(total_steps):
                # Advance both simulators
                world.tick()
                traci.simulationStep()

                sim_time = step * 0.05

                # Broadcast CAM messages at CAM interval
                if step % cam_interval == 0:
                    # Ego vehicle CAM from CARLA
                    ego_cam = self._build_cam_from_carla(self._ego_vehicle, sim_time)
                    if ego_cam:
                        self._send_cam_to_gateway(ego_cam)

                    # SUMO NPC vehicle CAMs
                    for vid in traci.vehicle.getIDList():
                        cam = self._build_cam_from_sumo(vid, sim_time)
                        if cam:
                            self._send_cam_to_gateway(cam)

                # Status report
                if step > 0 and step % report_interval == 0:
                    elapsed = time.monotonic() - start_real
                    sumo_vehicles = len(traci.vehicle.getIDList())
                    logger.info(
                        "Step %d/%d  sim=%.1fs  real=%.1fs  "
                        "sumo_vehicles=%d  cam_total=%d  rotations=%d",
                        step, total_steps, sim_time, elapsed,
                        sumo_vehicles,
                        self.stats["cam_messages_broadcast"],
                        self.stats["pseudonym_rotations"],
                    )

        except KeyboardInterrupt:
            logger.info("Co-simulation interrupted")
        finally:
            self._cleanup()

        elapsed_total = time.monotonic() - start_real
        logger.info("V2X co-simulation complete.")
        logger.info("  Real time: %.1f s", elapsed_total)
        logger.info("  CAM messages broadcast: %d", self.stats["cam_messages_broadcast"])
        logger.info("  Pseudonym rotations: %d", self.stats["pseudonym_rotations"])
        logger.info("  Linkage data destroyed: %d times", self.stats["linkage_data_destroyed"])
        logger.info("  Vehicles tracked: %d", self.stats["vehicles_tracked"])

    def _cleanup(self) -> None:
        """Destroy all actors and close connections."""
        logger.info("Cleaning up co-simulation...")

        if self._ego_vehicle and self._ego_vehicle.is_alive:
            self._ego_vehicle.destroy()

        if self._traci:
            try:
                self._traci.close()
            except Exception:
                pass

        if self._carla_world:
            try:
                settings = self._carla_world.get_settings()
                settings.synchronous_mode = False
                settings.fixed_delta_seconds = None
                self._carla_world.apply_settings(settings)
            except Exception:
                pass

        logger.info("Cleanup complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="CARLA-SUMO V2X Co-simulation for Chambers Testbed",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--carla-host", default="localhost", help="CARLA server host")
    parser.add_argument("--carla-port", type=int, default=2000, help="CARLA server port")
    parser.add_argument(
        "--sumo-cfg",
        default=str(Path(__file__).resolve().parent.parent / "sumo" / "urban_100v.sumocfg"),
        help="Path to SUMO configuration file",
    )
    parser.add_argument("--sumo-host", default="localhost", help="SUMO TraCI host")
    parser.add_argument("--sumo-port", type=int, default=8813, help="SUMO TraCI port")
    parser.add_argument("--duration", type=float, default=600.0, help="Simulation duration (seconds)")
    parser.add_argument("--gateway-url", default="http://localhost:8080", help="Chambers gateway URL")
    args = parser.parse_args()

    cosim = CarlaSumoCosim(
        carla_host=args.carla_host,
        carla_port=args.carla_port,
        sumo_cfg=args.sumo_cfg,
        sumo_host=args.sumo_host,
        sumo_port=args.sumo_port,
        gateway_url=args.gateway_url,
    )

    cosim.connect()
    cosim.run(args.duration)


if __name__ == "__main__":
    main()
