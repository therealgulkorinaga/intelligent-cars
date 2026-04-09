"""Tests for the SUMO adapter (using a mock TraCI module)."""

from __future__ import annotations

import asyncio
import types
from unittest.mock import MagicMock, patch

import pytest

from chambers_sim.adapters.sumo_adapter import (
    VAR_ACCELERATION,
    VAR_ANGLE,
    VAR_CO2_EMISSION,
    VAR_FUEL_CONSUMPTION,
    VAR_POSITION,
    VAR_ROUTE_ID,
    VAR_SPEED,
    SumoAdapter,
    VehicleSessionState,
    anonymise_position,
    driving_behaviour_score,
)
from chambers_sim.models.data_record import DataType


# ---- Helper: mock TraCI module ----


def _make_mock_traci(
    vehicle_ids_per_step: list[list[str]] | None = None,
    vehicle_data: dict[str, dict] | None = None,
) -> MagicMock:
    """Create a mock traci module with configurable vehicle data."""
    traci = MagicMock()

    if vehicle_ids_per_step is None:
        vehicle_ids_per_step = [["veh_0"]] * 10

    step_counter = {"n": 0}

    def simulation_step():
        step_counter["n"] += 1

    traci.simulationStep = simulation_step

    def get_id_list():
        idx = min(step_counter["n"], len(vehicle_ids_per_step) - 1)
        return vehicle_ids_per_step[idx]

    traci.vehicle.getIDList = get_id_list

    # Default vehicle data
    default_data = {
        VAR_POSITION: (500.0, 300.0),
        VAR_SPEED: 13.89,
        VAR_ACCELERATION: 0.5,
        VAR_ANGLE: 90.0,
        VAR_ROUTE_ID: "route_0",
        VAR_FUEL_CONSUMPTION: 2.5,
        VAR_CO2_EMISSION: 150.0,
    }

    def get_subscription_results(vid):
        if vehicle_data and vid in vehicle_data:
            merged = dict(default_data)
            merged.update(vehicle_data[vid])
            return merged
        return dict(default_data)

    traci.vehicle.getSubscriptionResults = get_subscription_results
    traci.vehicle.subscribe = MagicMock()

    # Geo conversion mock
    def convert_geo(x, y):
        return (x / 111320.0, y / 111320.0)

    traci.simulation.convertGeo = convert_geo
    traci.close = MagicMock()
    traci.start = MagicMock()

    return traci


# ---- Tests ----


class TestVehicleLifecycleMapping:
    """Test that vehicle departure/arrival maps to session start/end."""

    @pytest.mark.asyncio
    async def test_session_created_on_vehicle_entry(self):
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[
                [],  # step 0: no vehicles
                ["veh_0"],  # step 1: vehicle appears
                ["veh_0"],  # step 2: still present
            ]
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        collected_records = []

        async def callback(records):
            collected_records.extend(records)

        await adapter.run(3, callback)

        # A session should have been created for veh_0
        assert len(collected_records) > 0
        session_ids = {r.session_id for r in collected_records}
        assert len(session_ids) == 1
        assert any("veh_0" in sid for sid in session_ids)

    @pytest.mark.asyncio
    async def test_session_ended_on_vehicle_departure(self):
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[
                ["veh_0"],  # step 0
                ["veh_0"],  # step 1
                [],  # step 2: vehicle left
                [],  # step 3
            ]
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        await adapter.run(4, lambda records: None)

        # After the vehicle departs, the session should be ended
        assert "veh_0" not in adapter._vehicle_sessions

    @pytest.mark.asyncio
    async def test_records_contain_expected_data_types(self):
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[["veh_0"]] * 5
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        collected = []

        async def callback(records):
            collected.extend(records)

        await adapter.run(5, callback)

        data_types = {r.data_type for r in collected}
        assert DataType.POSITION in data_types
        assert DataType.SPEED in data_types
        assert DataType.ACCELERATION in data_types
        assert DataType.SENSOR_HEALTH in data_types


class TestDrivingBehaviourScore:
    """Test the driving behaviour scoring function."""

    def test_perfect_driver(self):
        """No harsh events -> score near 100."""
        speeds = [15.0] * 50
        accels = [0.5] * 50

        result = driving_behaviour_score(speeds, accels)
        assert result["score"] == 100.0
        assert result["harsh_braking_count"] == 0
        assert result["harsh_accel_count"] == 0

    def test_harsh_braking_reduces_score(self):
        """Several harsh braking events should reduce the score."""
        speeds = [20.0] * 50
        accels = [0.0] * 50
        # Inject harsh braking events
        accels[10] = -4.0
        accels[20] = -5.0
        accels[30] = -3.5

        result = driving_behaviour_score(speeds, accels)
        assert result["harsh_braking_count"] == 3
        assert result["score"] < 100.0
        # 3 harsh brakes * 5 points = 15 deducted -> score = 85
        assert result["score"] == 85.0

    def test_harsh_acceleration_reduces_score(self):
        """Harsh acceleration events should reduce score."""
        speeds = [15.0] * 30
        accels = [0.0] * 30
        accels[5] = 4.0
        accels[15] = 3.5

        result = driving_behaviour_score(speeds, accels)
        assert result["harsh_accel_count"] == 2
        assert result["score"] < 100.0
        # 2 * 4 = 8 deducted -> 92
        assert result["score"] == 92.0

    def test_combined_events(self):
        """Both harsh braking and acceleration."""
        speeds = [18.0] * 40
        accels = [0.0] * 40
        accels[5] = -4.0  # harsh brake
        accels[15] = 4.0  # harsh accel

        result = driving_behaviour_score(speeds, accels)
        assert result["harsh_braking_count"] == 1
        assert result["harsh_accel_count"] == 1
        # 1*5 + 1*4 = 9 deducted -> 91
        assert result["score"] == 91.0

    def test_score_does_not_go_below_zero(self):
        """Score is clamped at 0."""
        speeds = [20.0] * 100
        # All harsh braking
        accels = [-5.0] * 100

        result = driving_behaviour_score(speeds, accels)
        assert result["score"] == 0.0

    def test_empty_histories(self):
        """Empty input returns default score."""
        result = driving_behaviour_score([], [])
        assert result["score"] == 100.0
        assert result["average_speed_mps"] == 0.0

    def test_distance_and_duration(self):
        """Check distance and duration calculations."""
        speeds = [10.0] * 120  # 120 steps at 10 m/s
        accels = [0.0] * 120

        result = driving_behaviour_score(speeds, accels)
        assert result["distance_km"] == 1.2  # 120 * 10 / 1000
        assert result["duration_minutes"] == 2.0  # 120 / 60


class TestPositionAnonymisation:
    """Test the position anonymisation function."""

    def test_position_changes(self):
        """Anonymised position should differ from the original."""
        lat, lon = 51.507400, -0.127800
        anon_lat, anon_lon = anonymise_position(lat, lon)

        # The anonymised position should be different from exact input
        assert anon_lat != lat or anon_lon != lon

    def test_nearby_positions_snap_to_same_grid(self):
        """Two positions within the same ~1km grid cell should snap to the same point."""
        lat1, lon1 = 51.5074, -0.1278
        lat2, lon2 = 51.5076, -0.1275  # ~20m away

        anon1 = anonymise_position(lat1, lon1)
        anon2 = anonymise_position(lat2, lon2)

        assert anon1 == anon2

    def test_distant_positions_snap_to_different_grids(self):
        """Positions >1km apart should snap to different grid cells."""
        lat1, lon1 = 51.5074, -0.1278
        lat2, lon2 = 51.5200, -0.1278  # ~1.4km north

        anon1 = anonymise_position(lat1, lon1)
        anon2 = anonymise_position(lat2, lon2)

        assert anon1 != anon2

    def test_precision_reduction(self):
        """Result should have reduced decimal precision."""
        lat, lon = 51.50743217, -0.12784592
        anon_lat, anon_lon = anonymise_position(lat, lon)

        # Should be rounded to 4 decimal places
        assert anon_lat == round(anon_lat, 4)
        assert anon_lon == round(anon_lon, 4)


class TestConcurrentVehicles:
    """Test handling of multiple concurrent vehicles."""

    @pytest.mark.asyncio
    async def test_multiple_vehicles_tracked(self):
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[
                ["veh_0"],  # step 0
                ["veh_0", "veh_1"],  # step 1: veh_1 joins
                ["veh_0", "veh_1", "veh_2"],  # step 2: veh_2 joins
                ["veh_0", "veh_1", "veh_2"],  # step 3
            ]
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        collected = []

        async def callback(records):
            collected.extend(records)

        await adapter.run(4, callback)

        # Should have records from all three vehicles
        sources = {r.source for r in collected}
        assert any("veh_0" in s for s in sources)
        assert any("veh_1" in s for s in sources)
        assert any("veh_2" in s for s in sources)

        # Should have unique session IDs
        session_ids = {r.session_id for r in collected}
        assert len(session_ids) == 3

    @pytest.mark.asyncio
    async def test_vehicle_departure_mid_simulation(self):
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[
                ["veh_0", "veh_1"],  # step 0
                ["veh_0", "veh_1"],  # step 1
                ["veh_0"],  # step 2: veh_1 departs
                ["veh_0"],  # step 3
                [],  # step 4: veh_0 also departs
            ]
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        await adapter.run(5, lambda records: None)

        # Both vehicles should have ended sessions
        assert len(adapter._vehicle_sessions) == 0

    @pytest.mark.asyncio
    async def test_different_vehicle_data(self):
        """Each vehicle can have different telemetry values."""
        mock_traci = _make_mock_traci(
            vehicle_ids_per_step=[["veh_0", "veh_1"]] * 3,
            vehicle_data={
                "veh_0": {VAR_SPEED: 10.0},
                "veh_1": {VAR_SPEED: 25.0},
            },
        )

        adapter = SumoAdapter(sumo_config_path="dummy.sumocfg")
        adapter._traci = mock_traci
        adapter._step_length = 1.0

        collected = []

        async def callback(records):
            collected.extend(records)

        await adapter.run(3, callback)

        veh0_speeds = [
            r.fields["speed_mps"]
            for r in collected
            if "veh_0" in r.source and r.data_type == DataType.SPEED
        ]
        veh1_speeds = [
            r.fields["speed_mps"]
            for r in collected
            if "veh_1" in r.source and r.data_type == DataType.SPEED
        ]

        assert all(s == 10.0 for s in veh0_speeds)
        assert all(s == 25.0 for s in veh1_speeds)
