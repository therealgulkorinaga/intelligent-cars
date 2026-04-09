"""SUMO TraCI adapter for the Chambers simulation testbed."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from chambers_sim.adapters.base import SimulatorAdapter
from chambers_sim.models.data_record import ChannelType, DataRecord, DataType

logger = structlog.get_logger(__name__)

# TraCI constants for subscriptions
VAR_POSITION = 0x42  # tc.VAR_POSITION
VAR_SPEED = 0x40  # tc.VAR_SPEED
VAR_ACCELERATION = 0x72  # tc.VAR_ACCELERATION
VAR_ANGLE = 0x43  # tc.VAR_ANGLE
VAR_ROUTE_ID = 0x53  # tc.VAR_ROUTE_ID
VAR_FUEL_CONSUMPTION = 0x65  # tc.VAR_FUELCONSUMPTION
VAR_CO2_EMISSION = 0x60  # tc.VAR_CO2EMISSION


@dataclass
class VehicleSessionState:
    """Tracks per-vehicle state across simulation steps."""

    session_id: str
    vehicle_id: str
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    speed_history: list[float] = field(default_factory=list)
    accel_history: list[float] = field(default_factory=list)
    position_history: list[tuple[float, float]] = field(default_factory=list)
    step_count: int = 0


def driving_behaviour_score(
    speed_history: list[float],
    accel_history: list[float],
) -> dict[str, Any]:
    """Compute a driving behaviour score from speed and acceleration history.

    Scoring rules:
    - Harsh braking: acceleration < -3 m/s^2
    - Harsh acceleration: acceleration > 3 m/s^2
    - Cornering severity: approximated from sequential lateral acceleration changes
    - Score: starts at 100, deducted per infraction
    """
    harsh_braking_count = sum(1 for a in accel_history if a < -3.0)
    harsh_accel_count = sum(1 for a in accel_history if a > 3.0)

    # Approximate cornering severity from speed variance in short windows
    cornering_events = 0
    if len(speed_history) >= 3:
        for i in range(1, len(speed_history) - 1):
            speed_delta = abs(speed_history[i] - speed_history[i - 1])
            accel_delta = abs(accel_history[i] - accel_history[i - 1]) if i < len(accel_history) else 0
            # Lateral acceleration proxy: speed * yaw_rate; approximate via deltas
            if speed_delta > 2.0 and accel_delta > 1.5:
                cornering_events += 1

    # Score calculation: start at 100, deduct per infraction
    score = 100.0
    score -= harsh_braking_count * 5.0
    score -= harsh_accel_count * 4.0
    score -= cornering_events * 3.0
    score = max(0.0, min(100.0, score))

    avg_speed = sum(speed_history) / len(speed_history) if speed_history else 0.0
    distance_km = sum(speed_history) * 1.0 / 1000.0  # each step ~1s at given speed

    return {
        "score": round(score, 1),
        "harsh_braking_count": harsh_braking_count,
        "harsh_accel_count": harsh_accel_count,
        "cornering_events": cornering_events,
        "average_speed_mps": round(avg_speed, 2),
        "distance_km": round(distance_km, 3),
        "duration_minutes": round(len(speed_history) / 60.0, 2),
        "time_of_day_bucket": _time_of_day_bucket(datetime.now(timezone.utc)),
        "raw_speed_trace": speed_history[-20:],  # last 20 entries for context
        "raw_accel_trace": accel_history[-20:],
    }


def anonymise_position(lat: float, lon: float) -> tuple[float, float]:
    """Reduce position precision to approximately a 1km grid.

    Truncates to ~3 decimal places for latitude (~111m per 0.001 degree)
    and adjusts longitude similarly.  The result snaps to a grid cell center.
    """
    # ~0.009 degrees latitude ~ 1 km
    grid_size = 0.009
    anon_lat = round(math.floor(lat / grid_size) * grid_size + grid_size / 2, 4)
    anon_lon = round(math.floor(lon / grid_size) * grid_size + grid_size / 2, 4)
    return anon_lat, anon_lon


def _time_of_day_bucket(dt: datetime) -> str:
    """Map a datetime to a coarse time-of-day bucket."""
    hour = dt.hour
    if 6 <= hour < 10:
        return "morning_rush"
    elif 10 <= hour < 16:
        return "midday"
    elif 16 <= hour < 20:
        return "evening_rush"
    else:
        return "night"


class SumoAdapter(SimulatorAdapter):
    """Adapter that bridges Eclipse SUMO via TraCI to the Chambers gateway."""

    def __init__(
        self,
        sumo_config_path: str,
        host: str = "localhost",
        port: int = 8813,
        gateway_url: str = "http://localhost:8080",
        *,
        use_gui: bool = False,
    ) -> None:
        self.sumo_config_path = sumo_config_path
        self.host = host
        self.port = port
        self.gateway_url = gateway_url
        self.use_gui = use_gui
        self._traci: Any = None
        self._vehicle_sessions: dict[str, VehicleSessionState] = {}
        self._known_vehicles: set[str] = set()
        self._step_length: float = 1.0  # seconds per simulation step

    async def connect(self) -> None:
        """Start SUMO via TraCI or connect to a running instance."""
        import traci

        self._traci = traci

        sumo_binary = "sumo-gui" if self.use_gui else "sumo"
        cmd = [sumo_binary, "-c", self.sumo_config_path, "--step-length", str(self._step_length)]

        try:
            traci.start(cmd, port=self.port)
            logger.info("sumo_started", config=self.sumo_config_path, port=self.port)
        except Exception:
            # Fall back to connecting to an already-running instance
            traci.init(port=self.port, host=self.host)
            logger.info("sumo_connected", host=self.host, port=self.port)

    async def start_session(self, vehicle_id: str) -> str:
        """Start a data session for a vehicle that has entered the simulation."""
        session_id = f"sumo-{vehicle_id}-{uuid.uuid4().hex[:8]}"
        self._vehicle_sessions[vehicle_id] = VehicleSessionState(
            session_id=session_id,
            vehicle_id=vehicle_id,
        )
        logger.info("session_started", vehicle_id=vehicle_id, session_id=session_id)
        return session_id

    async def get_records(self, vehicle_id: str) -> list[DataRecord]:
        """Get current data records for a vehicle from TraCI subscription results."""
        traci = self._traci
        state = self._vehicle_sessions.get(vehicle_id)
        if state is None or traci is None:
            return []

        now = datetime.now(timezone.utc)
        records: list[DataRecord] = []

        try:
            sub_results = traci.vehicle.getSubscriptionResults(vehicle_id)
        except Exception:
            # Vehicle may have left; read individual values as fallback
            sub_results = None

        if sub_results:
            x, y = sub_results.get(VAR_POSITION, (0.0, 0.0))
            speed = sub_results.get(VAR_SPEED, 0.0)
            accel = sub_results.get(VAR_ACCELERATION, 0.0)
            angle = sub_results.get(VAR_ANGLE, 0.0)
            route_id = sub_results.get(VAR_ROUTE_ID, "")
            fuel = sub_results.get(VAR_FUEL_CONSUMPTION, 0.0)
            co2 = sub_results.get(VAR_CO2_EMISSION, 0.0)
        else:
            try:
                x, y = traci.vehicle.getPosition(vehicle_id)
                speed = traci.vehicle.getSpeed(vehicle_id)
                accel = traci.vehicle.getAcceleration(vehicle_id)
                angle = traci.vehicle.getAngle(vehicle_id)
                route_id = traci.vehicle.getRouteID(vehicle_id)
                fuel = traci.vehicle.getFuelConsumption(vehicle_id)
                co2 = traci.vehicle.getCO2Emission(vehicle_id)
            except Exception as exc:
                logger.warning("vehicle_data_unavailable", vehicle_id=vehicle_id, error=str(exc))
                return []

        # Convert SUMO x/y (metres in network coords) to pseudo lat/lon
        # Real conversion would use traci.simulation.convertGeo; approximate here
        try:
            lon, lat = traci.simulation.convertGeo(x, y)
        except Exception:
            lat, lon = y / 111_320.0, x / 111_320.0

        state.speed_history.append(speed)
        state.accel_history.append(accel)
        state.position_history.append((lat, lon))
        state.step_count += 1

        # Position record
        records.append(
            DataRecord(
                session_id=state.session_id,
                timestamp=now,
                source=f"sumo:{vehicle_id}",
                data_type=DataType.POSITION,
                fields={
                    "latitude": lat,
                    "longitude": lon,
                    "altitude": 0.0,
                    "heading": angle,
                    "x_sumo": x,
                    "y_sumo": y,
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Speed record
        records.append(
            DataRecord(
                session_id=state.session_id,
                timestamp=now,
                source=f"sumo:{vehicle_id}",
                data_type=DataType.SPEED,
                fields={
                    "speed_mps": round(speed, 3),
                    "speed_kmh": round(speed * 3.6, 2),
                    "road_type": "urban",
                    "route_id": route_id,
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Acceleration record
        records.append(
            DataRecord(
                session_id=state.session_id,
                timestamp=now,
                source=f"sumo:{vehicle_id}",
                data_type=DataType.ACCELERATION,
                fields={
                    "longitudinal": round(accel, 3),
                    "lateral": 0.0,  # SUMO doesn't natively provide lateral accel
                    "vertical": 0.0,
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Sensor health (fuel & emissions as proxy)
        records.append(
            DataRecord(
                session_id=state.session_id,
                timestamp=now,
                source=f"sumo:{vehicle_id}",
                data_type=DataType.SENSOR_HEALTH,
                fields={
                    "sensor_id": "powertrain",
                    "status": "ok",
                    "temperature": 85.0,
                    "uptime_hours": round(state.step_count * self._step_length / 3600, 2),
                    "fuel_consumption_ml_s": round(fuel, 4),
                    "co2_emission_mg_s": round(co2, 4),
                },
                channel=ChannelType.CELLULAR,
            )
        )

        # Periodic driving behaviour record (every 30 steps)
        if state.step_count > 0 and state.step_count % 30 == 0:
            behaviour = driving_behaviour_score(state.speed_history, state.accel_history)
            records.append(
                DataRecord(
                    session_id=state.session_id,
                    timestamp=now,
                    source=f"sumo:{vehicle_id}",
                    data_type=DataType.DRIVING_BEHAVIOUR,
                    fields=behaviour,
                    channel=ChannelType.CELLULAR,
                )
            )

        return records

    async def end_session(self, vehicle_id: str) -> None:
        """End the data session for a vehicle that has left the simulation."""
        state = self._vehicle_sessions.pop(vehicle_id, None)
        if state:
            logger.info(
                "session_ended",
                vehicle_id=vehicle_id,
                session_id=state.session_id,
                steps=state.step_count,
            )
        self._known_vehicles.discard(vehicle_id)

    async def run(
        self,
        duration_seconds: float,
        callback: Callable[[list[DataRecord]], Awaitable[Any]] | Callable[[list[DataRecord]], Any],
    ) -> None:
        """Run the SUMO simulation for the given duration, calling back with records."""
        traci = self._traci
        if traci is None:
            raise RuntimeError("Not connected. Call connect() first.")

        total_steps = int(duration_seconds / self._step_length)
        logger.info("simulation_run_start", total_steps=total_steps, duration=duration_seconds)

        for step in range(total_steps):
            try:
                traci.simulationStep()
            except Exception as exc:
                logger.error("simulation_step_failed", step=step, error=str(exc))
                break

            # Detect new and departed vehicles
            current_vehicles = set(traci.vehicle.getIDList())
            new_vehicles = current_vehicles - self._known_vehicles
            departed_vehicles = self._known_vehicles - current_vehicles

            # Start sessions for new vehicles
            for vid in new_vehicles:
                await self.start_session(vid)
                # Subscribe to data
                try:
                    traci.vehicle.subscribe(
                        vid,
                        [
                            VAR_POSITION,
                            VAR_SPEED,
                            VAR_ACCELERATION,
                            VAR_ANGLE,
                            VAR_ROUTE_ID,
                            VAR_FUEL_CONSUMPTION,
                            VAR_CO2_EMISSION,
                        ],
                    )
                except Exception as exc:
                    logger.warning("subscription_failed", vehicle_id=vid, error=str(exc))

            # End sessions for departed vehicles
            for vid in departed_vehicles:
                await self.end_session(vid)

            self._known_vehicles = current_vehicles

            # Collect records from all active vehicles
            all_records: list[DataRecord] = []
            for vid in current_vehicles:
                records = await self.get_records(vid)
                all_records.extend(records)

            # Invoke callback
            if all_records:
                result = callback(all_records)
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result

        # Clean up remaining sessions
        for vid in list(self._vehicle_sessions.keys()):
            await self.end_session(vid)

        # Close SUMO
        try:
            traci.close()
            logger.info("sumo_closed")
        except Exception:
            pass
