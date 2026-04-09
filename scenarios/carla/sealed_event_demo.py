#!/usr/bin/env python3
"""Demonstrate sealed ADAS event capture in CARLA.

This scenario scripts a near-collision at an intersection:
1. Ego vehicle approaches a green light
2. An NPC vehicle runs the red light from a cross street
3. Collision detector triggers
4. A sealed event is captured (5 seconds before, 2 seconds after)
5. The event data structure is displayed, showing what is retained
   vs. what is "burned" (destroyed) per the Chambers preservation manifest

This demonstrates the core Chambers concept: safety-critical data is
preserved in a sealed, immutable envelope while non-essential personal
data is destroyed.

Usage:
    python sealed_event_demo.py --host localhost --port 2000

Requirements:
    - CARLA >= 0.9.14
    - carla Python package
"""

from __future__ import annotations

import argparse
import collections
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
logger = logging.getLogger("chambers.carla.sealed_event_demo")


# ---------------------------------------------------------------------------
# Sealed event ring buffer
# ---------------------------------------------------------------------------
WINDOW_BEFORE = 5.0  # seconds before trigger
WINDOW_AFTER = 2.0   # seconds after trigger
TICK_RATE = 0.05      # 20 FPS


@dataclass
class SensorSnapshot:
    """One timestep of sensor data."""

    sim_time: float
    timestamp_utc: str
    position: dict[str, float]
    velocity: dict[str, float]
    acceleration: dict[str, float]
    heading: float
    speed_mps: float
    camera_frame_id: int | None = None
    camera_data_size: int = 0
    lidar_point_count: int = 0
    imu: dict[str, Any] | None = None
    gnss: dict[str, float] | None = None


class RingBuffer:
    """Fixed-size ring buffer holding the last N seconds of snapshots."""

    def __init__(self, duration_seconds: float, tick_rate: float) -> None:
        self.max_size = int(duration_seconds / tick_rate) + 1
        self._buffer: collections.deque[SensorSnapshot] = collections.deque(
            maxlen=self.max_size
        )

    def append(self, snapshot: SensorSnapshot) -> None:
        self._buffer.append(snapshot)

    def get_all(self) -> list[SensorSnapshot]:
        return list(self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)


@dataclass
class SealedEvent:
    """An immutable, cryptographically-sealed event capture.

    Once sealed, the event cannot be modified.  The hash covers all
    retained data fields, providing tamper evidence.
    """

    event_id: str
    trigger_type: str
    trigger_timestamp: str
    trigger_sim_time: float
    window_start: str
    window_end: str

    # Retained sensor data (what the ADAS supplier receives)
    speed_trace: list[dict[str, Any]]
    imu_trace: list[dict[str, Any]]
    gnss_trace: list[dict[str, Any]]
    camera_frame_ids: list[int]
    lidar_snapshot_counts: list[int]
    acceleration_trace: list[dict[str, Any]]

    # Event context
    other_actor_type: str = ""
    collision_intensity: float = 0.0

    # Seal
    seal_hash: str = ""
    sealed_at: str = ""

    # What was burned (metadata about destroyed fields, not the fields themselves)
    burned_fields: list[str] = field(default_factory=list)

    def compute_seal(self) -> str:
        """Compute a SHA-256 hash over the retained data."""
        data_to_hash = json.dumps(
            {
                "event_id": self.event_id,
                "trigger_type": self.trigger_type,
                "trigger_timestamp": self.trigger_timestamp,
                "window_start": self.window_start,
                "window_end": self.window_end,
                "speed_trace_hash": hashlib.sha256(
                    json.dumps(self.speed_trace).encode()
                ).hexdigest(),
                "imu_trace_hash": hashlib.sha256(
                    json.dumps(self.imu_trace).encode()
                ).hexdigest(),
                "gnss_trace_hash": hashlib.sha256(
                    json.dumps(self.gnss_trace).encode()
                ).hexdigest(),
                "acceleration_trace_hash": hashlib.sha256(
                    json.dumps(self.acceleration_trace).encode()
                ).hexdigest(),
                "camera_frame_ids": self.camera_frame_ids,
                "lidar_snapshot_counts": self.lidar_snapshot_counts,
            },
            sort_keys=True,
        ).encode()

        self.seal_hash = hashlib.sha256(data_to_hash).hexdigest()
        self.sealed_at = datetime.now(timezone.utc).isoformat()
        return self.seal_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "trigger_type": self.trigger_type,
            "trigger_timestamp": self.trigger_timestamp,
            "trigger_sim_time": self.trigger_sim_time,
            "window": {
                "start": self.window_start,
                "end": self.window_end,
                "duration_seconds": WINDOW_BEFORE + WINDOW_AFTER,
            },
            "retained_data": {
                "speed_trace": {
                    "count": len(self.speed_trace),
                    "sample": self.speed_trace[:3] if self.speed_trace else [],
                },
                "acceleration_trace": {
                    "count": len(self.acceleration_trace),
                    "sample": self.acceleration_trace[:3] if self.acceleration_trace else [],
                },
                "imu_trace": {
                    "count": len(self.imu_trace),
                },
                "gnss_trace": {
                    "count": len(self.gnss_trace),
                },
                "camera_frames_captured": len(self.camera_frame_ids),
                "lidar_snapshots_captured": len(self.lidar_snapshot_counts),
            },
            "event_context": {
                "other_actor_type": self.other_actor_type,
                "collision_intensity": self.collision_intensity,
            },
            "seal": {
                "hash": self.seal_hash,
                "algorithm": "SHA-256",
                "sealed_at": self.sealed_at,
            },
            "burned_fields": self.burned_fields,
            "manifest_reference": {
                "stakeholder": "adas-mobileye",
                "legal_basis": "LegitimateInterest",
                "retention": "P730D",
                "purpose": "Safety algorithm improvement and validation",
            },
        }


def create_sealed_event(
    snapshots: list[SensorSnapshot],
    trigger_time: float,
    trigger_type: str,
    other_actor_type: str,
    collision_intensity: float,
) -> SealedEvent:
    """Create a sealed event from the ring buffer snapshots."""
    event_id = f"sealed-{uuid.uuid4().hex[:12]}"

    speed_trace = []
    imu_trace = []
    gnss_trace = []
    camera_ids = []
    lidar_counts = []
    accel_trace = []

    for snap in snapshots:
        speed_trace.append({
            "sim_time": snap.sim_time,
            "speed_mps": round(snap.speed_mps, 3),
        })
        accel_trace.append({
            "sim_time": snap.sim_time,
            "longitudinal": snap.acceleration.get("x", 0.0),
            "lateral": snap.acceleration.get("y", 0.0),
            "vertical": snap.acceleration.get("z", 0.0),
        })
        if snap.imu:
            imu_trace.append({
                "sim_time": snap.sim_time,
                "accelerometer": snap.imu.get("accelerometer", {}),
                "gyroscope": snap.imu.get("gyroscope", {}),
            })
        if snap.gnss:
            gnss_trace.append({
                "sim_time": snap.sim_time,
                "latitude": snap.gnss.get("latitude", 0.0),
                "longitude": snap.gnss.get("longitude", 0.0),
                "altitude": snap.gnss.get("altitude", 0.0),
            })
        if snap.camera_frame_id is not None:
            camera_ids.append(snap.camera_frame_id)
        if snap.lidar_point_count > 0:
            lidar_counts.append(snap.lidar_point_count)

    window_start = snapshots[0].timestamp_utc if snapshots else ""
    window_end = snapshots[-1].timestamp_utc if snapshots else ""

    event = SealedEvent(
        event_id=event_id,
        trigger_type=trigger_type,
        trigger_timestamp=datetime.now(timezone.utc).isoformat(),
        trigger_sim_time=trigger_time,
        window_start=window_start,
        window_end=window_end,
        speed_trace=speed_trace,
        imu_trace=imu_trace,
        gnss_trace=gnss_trace,
        camera_frame_ids=camera_ids,
        lidar_snapshot_counts=lidar_counts,
        acceleration_trace=accel_trace,
        other_actor_type=other_actor_type,
        collision_intensity=collision_intensity,
        burned_fields=[
            "driver_face_crop",
            "cabin_audio",
            "passenger_detection",
            "phone_bluetooth_id",
            "infotainment_state",
            "personal_navigation_history",
            "contact_list_sync_data",
            "voice_command_recordings",
        ],
    )

    event.compute_seal()
    return event


# ---------------------------------------------------------------------------
# CARLA scenario scripting
# ---------------------------------------------------------------------------
def run_demo(args: argparse.Namespace) -> None:
    """Run the sealed event demonstration."""
    import carla

    logger.info("=== Chambers Sealed Event Demo ===")
    logger.info("Session: sealed-event-demo-%s", uuid.uuid4().hex[:8])

    all_actors: list[Any] = []
    collision_detected = False
    collision_data: dict[str, Any] = {}

    try:
        # Connect
        client = carla.Client(args.host, args.port)
        client.set_timeout(30.0)
        logger.info("Connected to CARLA %s", client.get_server_version())

        # Load Town03 (has good intersections)
        available_maps = client.get_available_maps()
        target = None
        for m in available_maps:
            if "town03" in m.lower():
                target = m
                break
        if target:
            world = client.load_world(target)
        else:
            world = client.get_world()
        logger.info("Map loaded: %s", world.get_map().name)

        # Synchronous mode
        settings = world.get_settings()
        original_settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = TICK_RATE
        world.apply_settings(settings)

        traffic_manager = client.get_trafficmanager(args.tm_port)
        traffic_manager.set_synchronous_mode(True)

        # Clear weather for visibility
        weather = carla.WeatherParameters.ClearNoon
        world.set_weather(weather)

        blueprint_library = world.get_blueprint_library()
        game_map = world.get_map()
        spawn_points = game_map.get_spawn_points()

        # --- Find an intersection for the scenario ---
        # We need two spawn points that converge at an intersection.
        # Pick a spawn point for ego and find a perpendicular one for NPC.
        ego_spawn = spawn_points[0]

        # Find a spawn point at approximately 90 degrees to ego
        npc_spawn = None
        ego_yaw = ego_spawn.rotation.yaw
        for sp in spawn_points[1:]:
            yaw_diff = abs((sp.rotation.yaw - ego_yaw + 180) % 360 - 180)
            dist = sp.location.distance(ego_spawn.location)
            # Perpendicular approach within 50-100m
            if 60 < yaw_diff < 120 and 30 < dist < 100:
                npc_spawn = sp
                break

        if npc_spawn is None:
            # Fall back to any nearby spawn point
            for sp in spawn_points[1:]:
                dist = sp.location.distance(ego_spawn.location)
                if 20 < dist < 80:
                    npc_spawn = sp
                    break
            if npc_spawn is None:
                npc_spawn = spawn_points[min(5, len(spawn_points) - 1)]

        # --- Spawn ego vehicle ---
        ego_bp = blueprint_library.filter("vehicle.tesla.model3")
        if not ego_bp:
            ego_bp = blueprint_library.filter("vehicle.*")
        ego_bp = ego_bp[0]
        ego_bp.set_attribute("role_name", "hero")
        ego_bp.set_attribute("color", "0,120,255")

        ego_vehicle = world.try_spawn_actor(ego_bp, ego_spawn)
        if ego_vehicle is None:
            raise RuntimeError("Failed to spawn ego vehicle")
        all_actors.append(ego_vehicle)
        logger.info(
            "Ego spawned at (%.1f, %.1f) heading %.1f",
            ego_spawn.location.x, ego_spawn.location.y, ego_spawn.rotation.yaw,
        )

        # --- Spawn NPC red-light runner ---
        npc_bp = blueprint_library.filter("vehicle.audi.a2")
        if not npc_bp:
            npc_bp = blueprint_library.filter("vehicle.*")
        npc_bp = npc_bp[0]
        if npc_bp.has_attribute("color"):
            npc_bp.set_attribute("color", "255,0,0")
        npc_bp.set_attribute("role_name", "npc_red_runner")

        npc_vehicle = world.try_spawn_actor(npc_bp, npc_spawn)
        if npc_vehicle is None:
            raise RuntimeError("Failed to spawn NPC vehicle")
        all_actors.append(npc_vehicle)
        logger.info(
            "NPC (red-light runner) spawned at (%.1f, %.1f) heading %.1f",
            npc_spawn.location.x, npc_spawn.location.y, npc_spawn.rotation.yaw,
        )

        # --- Attach collision detector to ego ---
        collision_bp = blueprint_library.find("sensor.other.collision")
        collision_sensor = world.spawn_actor(
            collision_bp,
            carla.Transform(),
            attach_to=ego_vehicle,
        )
        all_actors.append(collision_sensor)

        def _on_collision(event: Any) -> None:
            nonlocal collision_detected, collision_data
            if not collision_detected:
                collision_detected = True
                impulse = event.normal_impulse
                intensity = math.sqrt(impulse.x ** 2 + impulse.y ** 2 + impulse.z ** 2)
                collision_data = {
                    "other_actor_type": event.other_actor.type_id if event.other_actor else "unknown",
                    "intensity": intensity,
                    "impulse": {"x": impulse.x, "y": impulse.y, "z": impulse.z},
                }
                logger.warning(
                    "COLLISION with %s (intensity: %.1f)",
                    collision_data["other_actor_type"],
                    intensity,
                )

        collision_sensor.listen(_on_collision)

        # --- Attach IMU and GNSS to ego ---
        imu_bp = blueprint_library.find("sensor.other.imu")
        imu_sensor = world.spawn_actor(
            imu_bp,
            carla.Transform(carla.Location(z=1.0)),
            attach_to=ego_vehicle,
        )
        all_actors.append(imu_sensor)

        imu_data_latest: dict[str, Any] = {}

        def _on_imu(data: Any) -> None:
            imu_data_latest["accelerometer"] = {
                "x": round(data.accelerometer.x, 4),
                "y": round(data.accelerometer.y, 4),
                "z": round(data.accelerometer.z, 4),
            }
            imu_data_latest["gyroscope"] = {
                "x": round(data.gyroscope.x, 4),
                "y": round(data.gyroscope.y, 4),
                "z": round(data.gyroscope.z, 4),
            }
            imu_data_latest["compass"] = round(data.compass, 2)

        imu_sensor.listen(_on_imu)

        gnss_bp = blueprint_library.find("sensor.other.gnss")
        gnss_sensor = world.spawn_actor(
            gnss_bp,
            carla.Transform(carla.Location(z=2.0)),
            attach_to=ego_vehicle,
        )
        all_actors.append(gnss_sensor)

        gnss_data_latest: dict[str, float] = {}

        def _on_gnss(data: Any) -> None:
            gnss_data_latest["latitude"] = data.latitude
            gnss_data_latest["longitude"] = data.longitude
            gnss_data_latest["altitude"] = data.altitude

        gnss_sensor.listen(_on_gnss)

        # --- Configure driving behaviour ---
        # Ego: cautious driver following traffic rules
        ego_vehicle.set_autopilot(True, args.tm_port)
        traffic_manager.vehicle_percentage_speed_difference(ego_vehicle, 10.0)
        traffic_manager.distance_to_leading_vehicle(ego_vehicle, 3.0)

        # NPC: aggressive driver ignoring traffic lights
        npc_vehicle.set_autopilot(True, args.tm_port)
        traffic_manager.ignore_lights_percentage(npc_vehicle, 100.0)
        traffic_manager.ignore_signs_percentage(npc_vehicle, 100.0)
        traffic_manager.vehicle_percentage_speed_difference(npc_vehicle, -30.0)  # 30% faster
        traffic_manager.distance_to_leading_vehicle(npc_vehicle, 0.5)

        logger.info("NPC configured to run red lights at high speed")

        # --- Initialise ring buffer ---
        ring = RingBuffer(WINDOW_BEFORE, TICK_RATE)

        # --- Main loop ---
        max_duration = 60.0  # Maximum scenario time (seconds)
        total_steps = int(max_duration / TICK_RATE)
        post_collision_steps = int(WINDOW_AFTER / TICK_RATE)
        post_collision_counter = -1
        sealed_event: SealedEvent | None = None

        logger.info("Running scenario (max %.0f seconds)...", max_duration)
        logger.info("Waiting for collision event...")

        frame_counter = 0

        for step in range(total_steps):
            world.tick()
            sim_time = step * TICK_RATE

            # Collect snapshot
            transform = ego_vehicle.get_transform()
            velocity = ego_vehicle.get_velocity()
            accel = ego_vehicle.get_acceleration()
            speed_mps = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)

            snapshot = SensorSnapshot(
                sim_time=sim_time,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                position={
                    "x": round(transform.location.x, 3),
                    "y": round(transform.location.y, 3),
                    "z": round(transform.location.z, 3),
                },
                velocity={
                    "x": round(velocity.x, 3),
                    "y": round(velocity.y, 3),
                    "z": round(velocity.z, 3),
                },
                acceleration={
                    "x": round(accel.x, 3),
                    "y": round(accel.y, 3),
                    "z": round(accel.z, 3),
                },
                heading=round(transform.rotation.yaw, 2),
                speed_mps=round(speed_mps, 3),
                camera_frame_id=frame_counter,
                camera_data_size=1280 * 720 * 4,  # Simulated BGRA
                lidar_point_count=64000,  # Simulated
                imu=dict(imu_data_latest) if imu_data_latest else None,
                gnss=dict(gnss_data_latest) if gnss_data_latest else None,
            )
            frame_counter += 1

            ring.append(snapshot)

            # Handle collision trigger
            if collision_detected and post_collision_counter < 0:
                logger.info(
                    "Collision triggered at sim_time=%.2f s. "
                    "Capturing %.1f s post-event data...",
                    sim_time, WINDOW_AFTER,
                )
                post_collision_counter = 0

            if post_collision_counter >= 0:
                post_collision_counter += 1
                if post_collision_counter >= post_collision_steps:
                    # Capture the sealed event
                    all_snapshots = ring.get_all()
                    sealed_event = create_sealed_event(
                        snapshots=all_snapshots,
                        trigger_time=sim_time - WINDOW_AFTER,
                        trigger_type="collision",
                        other_actor_type=collision_data.get("other_actor_type", "unknown"),
                        collision_intensity=collision_data.get("intensity", 0.0),
                    )
                    logger.info("Sealed event captured and hashed.")
                    break

            # Periodic status
            if step > 0 and step % int(5.0 / TICK_RATE) == 0:
                ego_loc = ego_vehicle.get_transform().location
                npc_loc = npc_vehicle.get_transform().location
                dist = ego_loc.distance(npc_loc)
                logger.info(
                    "  t=%.1fs  ego_speed=%.1f km/h  ego-npc_dist=%.1f m  "
                    "buffer_size=%d",
                    sim_time, speed_mps * 3.6, dist, len(ring),
                )

        # --- Display results ---
        print("\n" + "=" * 72)

        if sealed_event:
            print("SEALED EVENT CAPTURED")
            print("=" * 72)

            event_dict = sealed_event.to_dict()
            print(json.dumps(event_dict, indent=2))

            print("\n" + "-" * 72)
            print("DATA RETENTION SUMMARY")
            print("-" * 72)
            retained = event_dict["retained_data"]
            print(f"  RETAINED (sent to ADAS supplier per manifest):")
            print(f"    Speed trace:        {retained['speed_trace']['count']} samples")
            print(f"    Acceleration trace: {retained['acceleration_trace']['count']} samples")
            print(f"    IMU trace:          {retained['imu_trace']['count']} samples")
            print(f"    GNSS trace:         {retained['gnss_trace']['count']} samples")
            print(f"    Camera frames:      {retained['camera_frames_captured']} frame IDs")
            print(f"    LiDAR snapshots:    {retained['lidar_snapshots_captured']} snapshots")
            print()
            print(f"  BURNED (destroyed per Chambers preservation rules):")
            for field_name in event_dict["burned_fields"]:
                print(f"    [X] {field_name}")
            print()
            print(f"  Seal hash (SHA-256):  {event_dict['seal']['hash']}")
            print(f"  Sealed at:            {event_dict['seal']['sealed_at']}")
            print()
            print(
                "  The seal hash provides tamper evidence. Any modification to\n"
                "  the retained data would produce a different hash, alerting\n"
                "  all parties to potential data integrity issues."
            )

            # Write sealed event to file
            output_path = Path(__file__).resolve().parent / "sealed_event_output.json"
            output_path.write_text(
                json.dumps(event_dict, indent=2), encoding="utf-8"
            )
            print(f"\n  Full event data written to: {output_path}")

        else:
            print("NO COLLISION OCCURRED")
            print("=" * 72)
            print(
                "The scripted NPC did not collide with the ego vehicle within\n"
                "the time limit. This can happen depending on spawn points and\n"
                "traffic conditions. Try re-running or adjusting spawn points."
            )
            print()
            print("Demonstrating sealed event from simulated data instead...")
            print()

            # Create a demonstration sealed event from collected data
            all_snapshots = ring.get_all()
            if all_snapshots:
                demo_event = create_sealed_event(
                    snapshots=all_snapshots[-100:],  # Last 5 seconds
                    trigger_time=all_snapshots[-1].sim_time,
                    trigger_type="near_miss_simulated",
                    other_actor_type="vehicle.audi.a2",
                    collision_intensity=0.0,
                )
                event_dict = demo_event.to_dict()
                print(json.dumps(event_dict, indent=2))

                output_path = Path(__file__).resolve().parent / "sealed_event_output.json"
                output_path.write_text(
                    json.dumps(event_dict, indent=2), encoding="utf-8"
                )
                print(f"\n  Demo event data written to: {output_path}")

        print("=" * 72)

    except KeyboardInterrupt:
        logger.info("Demo interrupted by user")
    except Exception:
        logger.exception("Demo failed")
    finally:
        logger.info("Cleaning up...")
        for actor in reversed(all_actors):
            if actor is not None and actor.is_alive:
                actor.destroy()
        try:
            world.apply_settings(original_settings)
        except Exception:
            pass
        logger.info("Cleanup complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chambers Sealed ADAS Event Demo",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="localhost", help="CARLA server host")
    parser.add_argument("--port", type=int, default=2000, help="CARLA server port")
    parser.add_argument("--tm-port", type=int, default=8000, help="Traffic manager port")
    args = parser.parse_args()
    run_demo(args)


if __name__ == "__main__":
    main()
