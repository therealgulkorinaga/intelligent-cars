"""Abstract base class for simulator adapters."""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
from typing import Any

from chambers_sim.models.data_record import DataRecord


class SimulatorAdapter(abc.ABC):
    """Abstract base for all simulator adapters.

    Each adapter bridges a specific simulator (SUMO, CARLA, ROS2) to the
    Chambers gateway by converting simulator-native data into DataRecord
    objects.
    """

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish connection to the simulator."""

    @abc.abstractmethod
    async def start_session(self, vehicle_id: str) -> str:
        """Start a data session for a vehicle, returning a session_id."""

    @abc.abstractmethod
    async def get_records(self, vehicle_id: str) -> list[DataRecord]:
        """Retrieve current data records for a vehicle."""

    @abc.abstractmethod
    async def end_session(self, vehicle_id: str) -> None:
        """End the data session for a vehicle."""

    @abc.abstractmethod
    async def run(
        self,
        duration_seconds: float,
        callback: Callable[[list[DataRecord]], Awaitable[Any]] | Callable[[list[DataRecord]], Any],
    ) -> None:
        """Run the main simulation loop for the given duration.

        At each simulation step, collected records are passed to *callback*.
        """
