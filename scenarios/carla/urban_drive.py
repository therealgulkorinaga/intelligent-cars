#!/usr/bin/env python3
"""CARLA urban driving scenario for the Chambers Automotive Simulation Testbed.

Spawns an ego vehicle with a full sensor suite (camera, LiDAR, GNSS, IMU,
collision detector), populates the world with NPC traffic and pedestrians,
and streams data to the Chambers gateway via the adapter layer.

Usage:
    python urban_drive.py --host localhost --port 2000 --town Town03 --duration 300

Requirements:
    - CARLA simulator >= 0.9.14 running on the specified host/port
    - carla Python package (from CARLA PythonAPI)
    - chambers_sim package
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import queue
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("chambers.carla.urban_drive")

# ---------------------------------------------------------------------------
# Sensor configuration
# ---------------------------------------------------------------------------
SENSOR_SUITE = {
    "rgb_front": {
        "type": "sensor.camera.rgb",
        "transform": {"x": 1.5, "y": 0.0, "z": 2.4, "pitch": -5.0, "yaw": 0.0, "roll": 0.0},
        "attributes": {
            "image_size_x": "1280",
            "image_size_y": "720",
            "fov": "90",
            "sensor_tick": "0.1",
        },
    },
    "lidar_roof": {
        "type": "sensor.lidar.ray_cast",
        "transform": {"x": 0.0, "y": 0.0, "z": 2.8, "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "attributes": {
            "channels": "32",
            "range": "100.0",
            "points_per_second": "320000",
            "rotation_frequency": "10",
            "upper_fov": "10.0",
            "lower_fov": "-30.0",
            "sensor_tick": "0.1",
        },
    },
    "gnss": {
        "type": "sensor.other.gnss",
        "transform": {"x": 0.0, "y": 0.0, "z": 2.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "attributes": {
            "noise_alt_bias": "0.0",
            "noise_alt_stddev": "0.5",
            "noise_lat_bias": "0.0",
            "noise_lat_stddev": "0.00001",
            "noise_lon_bias": "0.0",
            "noise_lon_stddev": "0.00001",
            "sensor_tick": "0.1",
        },
    },
    "imu": {
        "type": "sensor.other.imu",
        "transform": {"x": 0.0, "y": 0.0, "z": 1.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "attributes": {
            "noise_accel_stddev_x": "0.1",
            "noise_accel_stddev_y": "0.1",
            "noise_accel_stddev_z": "0.1",
            "noise_gyro_stddev_x": "0.01",
            "noise_gyro_stddev_y": "0.01",
            "noise_gyro_stddev_z": "0.01",
            "sensor_tick": "0.05",
        },
    },
    "collision": {
        "type": "sensor.other.collision",
        "transform": {"x": 0.0, "y": 0.0, "z": 0.0, "pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        "attributes": {},
    },
}

# ---------------------------------------------------------------------------
# Data buffers for sensor callbacks
# ---------------------------------------------------------------------------
class SensorDataBuffer:
    """Thread-safe ring buffer collecting the most recent sensor readings."""

    def __init__(self, maxsize: int = 100) -> None:
        self.data: dict[str, queue.Queue] = {}
        self.maxsize = maxsize

    def register(self, sensor_name: str) -> None:
        self.data[sensor_name] = queue.Queue(maxsize=self.maxsize)

    def put(self, sensor_name: str, reading: Any) -> None:
        q = self.data.get(sensor_name)
        if q is None:
            return
        if q.full():
            try:
                q.get_nowait()
            except queue.Empty:
                pass
        q.put_nowait(reading)

    def get_latest(self, sensor_name: str) -> Any | None:
        q = self.data.get(sensor_name)
        if q is None:
            return None
        latest = None
        while not q.empty():
            try:
                latest = q.get_nowait()
            except queue.Empty:
                break
        return latest


# ---------------------------------------------------------------------------
# CARLA helpers
# ---------------------------------------------------------------------------
def make_transform(cfg: dict) -> Any:
    """Create a carla.Transform from config dict."""
    import carla

    return carla.Transform(
        carla.Location(x=cfg["x"], y=cfg["y"], z=cfg["z"]),
        carla.Rotation(pitch=cfg["pitch"], yaw=cfg["yaw"], roll=cfg["roll"]),
    )


def set_weather_clear_noon(world: Any) -> None:
    """Set weather to clear noon conditions."""
    import carla

    weather = carla.WeatherParameters(
        cloudiness=10.0,
        precipitation=0.0,
        precipitation_deposits=0.0,
        wind_intensity=5.0,
        sun_azimuth_angle=0.0,
        sun_altitude_angle=70.0,
        fog_density=0.0,
        fog_distance=0.0,
        fog_falloff=0.0,
        wetness=0.0,
    )
    world.set_weather(weather)
    logger.info("Weather set to clear noon")


def spawn_ego_vehicle(world: Any, blueprint_library: Any) -> Any:
    """Spawn the ego vehicle (Tesla Model 3 or fallback passenger car)."""
    # Try Tesla Model 3 first, fall back to any sedan
    ego_bp = None
    preferred_models = [
        "vehicle.tesla.model3",
        "vehicle.audi.a2",
        "vehicle.bmw.grandtourer",
        "vehicle.mercedes.coupe_2020",
    ]
    for model in preferred_models:
        bp = blueprint_library.find(model) if hasattr(blueprint_library, "find") else None
        if bp is None:
            candidates = blueprint_library.filter(model)
            if len(candidates) > 0:
                bp = candidates[0]
        if bp is not None:
            ego_bp = bp
            break

    if ego_bp is None:
        # Last resort: pick any passenger vehicle
        vehicles = blueprint_library.filter("vehicle.*")
        ego_bp = vehicles[0]

    # Set as hero (ego) vehicle
    if ego_bp.has_attribute("role_name"):
        ego_bp.set_attribute("role_name", "hero")

    # Pick a spawn point
    spawn_points = world.get_map().get_spawn_points()
    if not spawn_points:
        raise RuntimeError("No spawn points available on map")

    # Choose a spawn point that is on a reasonably straight road
    spawn_point = spawn_points[0]
    logger.info(
        "Spawning ego vehicle: %s at (%.1f, %.1f, %.1f)",
        ego_bp.id,
        spawn_point.location.x,
        spawn_point.location.y,
        spawn_point.location.z,
    )

    ego_vehicle = world.try_spawn_actor(ego_bp, spawn_point)
    if ego_vehicle is None:
        # Try other spawn points
        for sp in spawn_points[1:10]:
            ego_vehicle = world.try_spawn_actor(ego_bp, sp)
            if ego_vehicle is not None:
                break

    if ego_vehicle is None:
        raise RuntimeError("Failed to spawn ego vehicle at any spawn point")

    return ego_vehicle


def attach_sensors(
    world: Any,
    blueprint_library: Any,
    ego_vehicle: Any,
    sensor_buffer: SensorDataBuffer,
) -> list[Any]:
    """Attach all sensors from SENSOR_SUITE to the ego vehicle."""
    import carla
    import numpy as np

    sensors: list[Any] = []

    for sensor_name, cfg in SENSOR_SUITE.items():
        bp = blueprint_library.find(cfg["type"])
        for attr_name, attr_value in cfg["attributes"].items():
            if bp.has_attribute(attr_name):
                bp.set_attribute(attr_name, attr_value)

        transform = make_transform(cfg["transform"])
        sensor = world.spawn_actor(bp, transform, attach_to=ego_vehicle)
        sensor_buffer.register(sensor_name)

        # Register callback based on sensor type
        if "camera" in cfg["type"]:
            def _camera_cb(image, name=sensor_name):
                data = {
                    "frame": image.frame,
                    "timestamp": image.timestamp,
                    "width": image.width,
                    "height": image.height,
                    "fov": image.fov,
                    # Raw pixel data not stored in buffer to save memory;
                    # in production, write to disk or stream to adapter
                    "raw_data_size": len(image.raw_data),
                }
                sensor_buffer.put(name, data)
            sensor.listen(_camera_cb)

        elif "lidar" in cfg["type"]:
            def _lidar_cb(point_cloud, name=sensor_name):
                data = {
                    "frame": point_cloud.frame,
                    "timestamp": point_cloud.timestamp,
                    "channels": point_cloud.channels,
                    "point_count": len(point_cloud),
                    "horizontal_angle": point_cloud.horizontal_angle,
                }
                sensor_buffer.put(name, data)
            sensor.listen(_lidar_cb)

        elif "gnss" in cfg["type"]:
            def _gnss_cb(gnss_data, name=sensor_name):
                data = {
                    "frame": gnss_data.frame,
                    "timestamp": gnss_data.timestamp,
                    "latitude": gnss_data.latitude,
                    "longitude": gnss_data.longitude,
                    "altitude": gnss_data.altitude,
                }
                sensor_buffer.put(name, data)
            sensor.listen(_gnss_cb)

        elif "imu" in cfg["type"]:
            def _imu_cb(imu_data, name=sensor_name):
                data = {
                    "frame": imu_data.frame,
                    "timestamp": imu_data.timestamp,
                    "accelerometer": {
                        "x": imu_data.accelerometer.x,
                        "y": imu_data.accelerometer.y,
                        "z": imu_data.accelerometer.z,
                    },
                    "gyroscope": {
                        "x": imu_data.gyroscope.x,
                        "y": imu_data.gyroscope.y,
                        "z": imu_data.gyroscope.z,
                    },
                    "compass": imu_data.compass,
                }
                sensor_buffer.put(name, data)
            sensor.listen(_imu_cb)

        elif "collision" in cfg["type"]:
            def _collision_cb(event, name=sensor_name):
                data = {
                    "frame": event.frame,
                    "timestamp": event.timestamp,
                    "other_actor_id": event.other_actor.id if event.other_actor else None,
                    "other_actor_type": event.other_actor.type_id if event.other_actor else "unknown",
                    "impulse": {
                        "x": event.normal_impulse.x,
                        "y": event.normal_impulse.y,
                        "z": event.normal_impulse.z,
                    },
                    "intensity": math.sqrt(
                        event.normal_impulse.x ** 2
                        + event.normal_impulse.y ** 2
                        + event.normal_impulse.z ** 2
                    ),
                }
                sensor_buffer.put(name, data)
                logger.warning(
                    "COLLISION detected with %s (intensity: %.1f)",
                    data["other_actor_type"],
                    data["intensity"],
                )
            sensor.listen(_collision_cb)

        sensors.append(sensor)
        logger.info("Attached sensor: %s (%s)", sensor_name, cfg["type"])

    return sensors


def spawn_npc_traffic(
    client: Any,
    world: Any,
    blueprint_library: Any,
    num_vehicles: int = 50,
    num_walkers: int = 20,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Spawn NPC vehicles and pedestrians with AI controllers.

    Returns:
        Tuple of (npc_vehicles, walkers, walker_controllers)
    """
    import carla
    import random

    spawn_points = world.get_map().get_spawn_points()
    random.shuffle(spawn_points)

    # --- NPC Vehicles ---
    vehicle_bps = blueprint_library.filter("vehicle.*")
    # Filter out bicycles and motorcycles for urban scenario
    vehicle_bps = [
        bp for bp in vehicle_bps
        if int(bp.get_attribute("number_of_wheels")) >= 4
    ]

    npc_vehicles: list[Any] = []
    batch_cmds: list[Any] = []

    for i in range(min(num_vehicles, len(spawn_points) - 1)):
        bp = random.choice(vehicle_bps)
        if bp.has_attribute("color"):
            colors = bp.get_attribute("color").recommended_values
            bp.set_attribute("color", random.choice(colors))
        bp.set_attribute("role_name", "autopilot")

        spawn_point = spawn_points[i + 1]  # +1 to skip ego spawn point
        batch_cmds.append(
            carla.command.SpawnActor(bp, spawn_point).then(
                carla.command.SetAutopilot(carla.command.FutureActor, True)
            )
        )

    responses = client.apply_batch_sync(batch_cmds, True)
    for resp in responses:
        if resp.error:
            logger.debug("NPC vehicle spawn failed: %s", resp.error)
        else:
            npc_vehicles.append(resp.actor_id)

    logger.info("Spawned %d / %d NPC vehicles", len(npc_vehicles), num_vehicles)

    # --- Pedestrians ---
    walker_bps = blueprint_library.filter("walker.pedestrian.*")
    walker_spawn_points: list[Any] = []

    for _ in range(num_walkers):
        spawn_point = carla.Transform()
        loc = world.get_random_location_from_navigation()
        if loc is not None:
            spawn_point.location = loc
            walker_spawn_points.append(spawn_point)

    walkers: list[Any] = []
    walker_controllers: list[Any] = []

    # Spawn walkers
    batch_cmds = []
    for sp in walker_spawn_points:
        bp = random.choice(walker_bps)
        if bp.has_attribute("is_invincible"):
            bp.set_attribute("is_invincible", "false")
        batch_cmds.append(carla.command.SpawnActor(bp, sp))

    responses = client.apply_batch_sync(batch_cmds, True)
    walker_ids = []
    for resp in responses:
        if resp.error:
            logger.debug("Walker spawn failed: %s", resp.error)
        else:
            walker_ids.append(resp.actor_id)
            walkers.append(resp.actor_id)

    # Spawn walker AI controllers
    walker_controller_bp = blueprint_library.find("controller.ai.walker")
    batch_cmds = []
    for walker_id in walker_ids:
        batch_cmds.append(
            carla.command.SpawnActor(
                walker_controller_bp,
                carla.Transform(),
                world.get_actor(walker_id),
            )
        )

    responses = client.apply_batch_sync(batch_cmds, True)
    for resp in responses:
        if resp.error:
            logger.debug("Walker controller spawn failed: %s", resp.error)
        else:
            walker_controllers.append(resp.actor_id)

    # Start walking
    world.tick()  # Ensure controllers are ready
    for ctrl_id in walker_controllers:
        controller = world.get_actor(ctrl_id)
        if controller is not None:
            controller.start()
            dest = world.get_random_location_from_navigation()
            if dest is not None:
                controller.go_to_location(dest)
            controller.set_max_speed(1.0 + random.random() * 1.5)  # 1.0-2.5 m/s

    logger.info("Spawned %d / %d pedestrians", len(walkers), num_walkers)

    return npc_vehicles, walkers, walker_controllers


# ---------------------------------------------------------------------------
# Chambers data adapter bridge
# ---------------------------------------------------------------------------
def build_chambers_records(
    session_id: str,
    ego_vehicle: Any,
    sensor_buffer: SensorDataBuffer,
    step: int,
) -> list[dict[str, Any]]:
    """Convert current sensor state into Chambers-compatible data records.

    Returns a list of dicts that can be serialised and sent to the gateway.
    """
    now = datetime.now(timezone.utc).isoformat()
    vehicle_id = f"carla-ego-{ego_vehicle.id}"
    records: list[dict[str, Any]] = []

    # Vehicle kinematics
    transform = ego_vehicle.get_transform()
    velocity = ego_vehicle.get_velocity()
    accel = ego_vehicle.get_acceleration()
    speed_mps = math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)

    records.append({
        "session_id": session_id,
        "timestamp": now,
        "source": vehicle_id,
        "data_type": "Speed",
        "fields": {
            "speed_mps": round(speed_mps, 3),
            "speed_kmh": round(speed_mps * 3.6, 2),
            "road_type": "urban",
        },
        "channel": "Cellular",
    })

    records.append({
        "session_id": session_id,
        "timestamp": now,
        "source": vehicle_id,
        "data_type": "Acceleration",
        "fields": {
            "longitudinal": round(accel.x, 3),
            "lateral": round(accel.y, 3),
            "vertical": round(accel.z, 3),
        },
        "channel": "Cellular",
    })

    # GNSS
    gnss_data = sensor_buffer.get_latest("gnss")
    if gnss_data:
        records.append({
            "session_id": session_id,
            "timestamp": now,
            "source": vehicle_id,
            "data_type": "GnssPosition",
            "fields": {
                "latitude": gnss_data["latitude"],
                "longitude": gnss_data["longitude"],
                "altitude": gnss_data["altitude"],
                "heading": transform.rotation.yaw,
            },
            "channel": "Cellular",
        })

    # IMU
    imu_data = sensor_buffer.get_latest("imu")
    if imu_data:
        records.append({
            "session_id": session_id,
            "timestamp": now,
            "source": vehicle_id,
            "data_type": "ImuReading",
            "fields": {
                "accelerometer": imu_data["accelerometer"],
                "gyroscope": imu_data["gyroscope"],
                "compass": imu_data["compass"],
            },
            "channel": "Cellular",
        })

    # Camera frame metadata (not raw pixels)
    camera_data = sensor_buffer.get_latest("rgb_front")
    if camera_data:
        records.append({
            "session_id": session_id,
            "timestamp": now,
            "source": vehicle_id,
            "data_type": "CameraFrame",
            "fields": {
                "sensor_id": "rgb_front",
                "frame_number": camera_data["frame"],
                "width": camera_data["width"],
                "height": camera_data["height"],
                "fov": camera_data["fov"],
                "data_size_bytes": camera_data["raw_data_size"],
            },
            "channel": "Cellular",
        })

    # LiDAR metadata
    lidar_data = sensor_buffer.get_latest("lidar_roof")
    if lidar_data:
        records.append({
            "session_id": session_id,
            "timestamp": now,
            "source": vehicle_id,
            "data_type": "LidarCloud",
            "fields": {
                "sensor_id": "lidar_roof",
                "frame_number": lidar_data["frame"],
                "channels": lidar_data["channels"],
                "point_count": lidar_data["point_count"],
            },
            "channel": "Cellular",
        })

    # Collision event
    collision_data = sensor_buffer.get_latest("collision")
    if collision_data:
        records.append({
            "session_id": session_id,
            "timestamp": now,
            "source": vehicle_id,
            "data_type": "SealedEvent",
            "fields": {
                "trigger_type": "collision",
                "trigger_timestamp": now,
                "other_actor_type": collision_data["other_actor_type"],
                "impulse": collision_data["impulse"],
                "intensity": collision_data["intensity"],
            },
            "channel": "Cellular",
        })

    return records


def send_to_gateway(records: list[dict[str, Any]], gateway_url: str) -> None:
    """Send data records to the Chambers gateway via HTTP POST."""
    if not records:
        return

    try:
        import urllib.request

        payload = json.dumps({"records": records}).encode("utf-8")
        req = urllib.request.Request(
            f"{gateway_url}/api/v1/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status != 200:
                logger.warning("Gateway responded with status %d", resp.status)
    except Exception as e:
        # Gateway may not be running; log but do not crash
        logger.debug("Could not reach gateway: %s", e)


# ---------------------------------------------------------------------------
# Main simulation loop
# ---------------------------------------------------------------------------
def run_scenario(args: argparse.Namespace) -> None:
    """Execute the full urban driving scenario."""
    import carla

    session_id = f"carla-urban-{uuid.uuid4().hex[:8]}"
    logger.info("Starting Chambers CARLA urban drive scenario")
    logger.info("  Session ID: %s", session_id)
    logger.info("  Server: %s:%d", args.host, args.port)
    logger.info("  Map: %s", args.town)
    logger.info("  Duration: %d s", args.duration)

    # Track all actors for cleanup
    all_actors: list[Any] = []
    npc_vehicle_ids: list[int] = []
    walker_ids: list[int] = []
    walker_ctrl_ids: list[int] = []

    try:
        # Connect to CARLA
        client = carla.Client(args.host, args.port)
        client.set_timeout(30.0)
        logger.info("Connected to CARLA server (version %s)", client.get_server_version())

        # Load map
        available_maps = client.get_available_maps()
        target_map = None
        for m in available_maps:
            if args.town.lower() in m.lower():
                target_map = m
                break

        if target_map:
            logger.info("Loading map: %s", target_map)
            world = client.load_world(target_map)
        else:
            logger.warning(
                "Map '%s' not found. Available: %s. Using current map.",
                args.town,
                [os.path.basename(m) for m in available_maps],
            )
            world = client.get_world()

        # Set synchronous mode for deterministic simulation
        settings = world.get_settings()
        original_settings = world.get_settings()
        settings.synchronous_mode = True
        settings.fixed_delta_seconds = 0.05  # 20 FPS
        world.apply_settings(settings)

        # Enable traffic manager synchronous mode
        traffic_manager = client.get_trafficmanager(args.tm_port)
        traffic_manager.set_synchronous_mode(True)
        traffic_manager.set_global_distance_to_leading_vehicle(2.5)

        # Set weather
        set_weather_clear_noon(world)
        blueprint_library = world.get_blueprint_library()

        # Spawn ego vehicle
        ego_vehicle = spawn_ego_vehicle(world, blueprint_library)
        all_actors.append(ego_vehicle)

        # Attach sensors
        sensor_buffer = SensorDataBuffer()
        sensors = attach_sensors(world, blueprint_library, ego_vehicle, sensor_buffer)
        all_actors.extend(sensors)

        # Spawn NPC traffic
        npc_vehicle_ids, walker_ids, walker_ctrl_ids = spawn_npc_traffic(
            client, world, blueprint_library,
            num_vehicles=args.npc_vehicles,
            num_walkers=args.npc_walkers,
        )

        # Enable autopilot for ego vehicle
        ego_vehicle.set_autopilot(True, args.tm_port)

        # Load manifest if specified
        manifest = None
        if args.manifest:
            manifest_path = Path(args.manifest)
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                logger.info("Loaded preservation manifest: %s", args.manifest)
            else:
                logger.warning("Manifest file not found: %s", args.manifest)

        # --- Main simulation loop ---
        gateway_url = f"http://{args.gateway_host}:{args.gateway_port}"
        total_steps = int(args.duration / 0.05)  # 0.05s per tick
        report_interval = int(5.0 / 0.05)  # Report every 5 seconds
        gateway_interval = int(1.0 / 0.05)  # Send to gateway every 1 second

        logger.info("Running simulation for %d steps (%.0f seconds)...", total_steps, args.duration)

        # Graceful shutdown flag
        running = True

        def _signal_handler(sig: int, frame: Any) -> None:
            nonlocal running
            logger.info("Received signal %d, shutting down...", sig)
            running = False

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        total_records_sent = 0
        collision_count = 0
        start_time = time.monotonic()

        for step in range(total_steps):
            if not running:
                break

            world.tick()

            # Collect and send data at gateway interval
            if step % gateway_interval == 0:
                records = build_chambers_records(session_id, ego_vehicle, sensor_buffer, step)

                # Track collisions
                for rec in records:
                    if rec["data_type"] == "SealedEvent":
                        collision_count += 1

                send_to_gateway(records, gateway_url)
                total_records_sent += len(records)

            # Periodic status report
            if step > 0 and step % report_interval == 0:
                elapsed = time.monotonic() - start_time
                sim_time = step * 0.05
                velocity = ego_vehicle.get_velocity()
                speed_kmh = (
                    math.sqrt(velocity.x ** 2 + velocity.y ** 2 + velocity.z ** 2)
                    * 3.6
                )
                logger.info(
                    "Step %d/%d  sim_time=%.1fs  real_time=%.1fs  speed=%.1f km/h  "
                    "records_sent=%d  collisions=%d",
                    step, total_steps, sim_time, elapsed, speed_kmh,
                    total_records_sent, collision_count,
                )

        elapsed_total = time.monotonic() - start_time
        logger.info(
            "Simulation complete. Duration=%.1fs  Records=%d  Collisions=%d",
            elapsed_total, total_records_sent, collision_count,
        )

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception:
        logger.exception("Scenario failed")
    finally:
        # --- Cleanup ---
        logger.info("Cleaning up actors...")
        try:
            # Stop walker controllers
            for ctrl_id in walker_ctrl_ids:
                ctrl = world.get_actor(ctrl_id)
                if ctrl is not None:
                    ctrl.stop()

            # Destroy in batch
            destroy_ids = walker_ctrl_ids + walker_ids + npc_vehicle_ids
            if destroy_ids:
                import carla as carla_mod

                cmds = [carla_mod.command.DestroyActor(aid) for aid in destroy_ids]
                client.apply_batch_sync(cmds, True)

            # Destroy ego vehicle and sensors
            for actor in reversed(all_actors):
                if actor is not None and actor.is_alive:
                    actor.destroy()

            # Restore original settings
            world.apply_settings(original_settings)

            logger.info("All actors destroyed. Scenario ended cleanly.")
        except Exception:
            logger.exception("Error during cleanup")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chambers CARLA Urban Driving Scenario",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="localhost", help="CARLA server host")
    parser.add_argument("--port", type=int, default=2000, help="CARLA server port")
    parser.add_argument("--tm-port", type=int, default=8000, help="Traffic manager port")
    parser.add_argument("--town", default="Town03", help="CARLA map to load")
    parser.add_argument("--duration", type=float, default=300.0, help="Scenario duration in seconds")
    parser.add_argument("--npc-vehicles", type=int, default=50, help="Number of NPC vehicles")
    parser.add_argument("--npc-walkers", type=int, default=20, help="Number of pedestrians")
    parser.add_argument("--gateway-host", default="localhost", help="Chambers gateway host")
    parser.add_argument("--gateway-port", type=int, default=8080, help="Chambers gateway port")
    parser.add_argument(
        "--manifest",
        default=None,
        help="Path to Chambers preservation manifest JSON file",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_scenario(args)
