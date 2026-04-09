"""Thread-safe in-memory storage for received stakeholder payloads."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any


class DataStore:
    """Singleton in-memory store shared across all endpoints.

    Every ``add`` call appends a timestamped record keyed by stakeholder
    name.  ``get_*`` helpers expose the data for test assertions via the
    ``/admin`` routes.
    """

    _instance: DataStore | None = None
    _lock_class = threading.Lock  # used for singleton creation

    def __new__(cls) -> DataStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_storage()
        return cls._instance

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_storage(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {
            "oem": [],
            "insurer": [],
            "adas": [],
            "tier1": [],
            "broker": [],
            "foreign": [],
        }
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, stakeholder: str, payload: dict[str, Any]) -> None:
        """Append *payload* under *stakeholder* key (thread-safe)."""
        with self._lock:
            record = {
                "received_at": datetime.utcnow().isoformat(),
                "payload": payload,
            }
            if stakeholder not in self._data:
                self._data[stakeholder] = []
            self._data[stakeholder].append(record)

    def get_all(self) -> dict[str, list[dict[str, Any]]]:
        """Return a snapshot of all stored data."""
        with self._lock:
            return {k: list(v) for k, v in self._data.items()}

    def get_by_stakeholder(self, name: str) -> list[dict[str, Any]]:
        """Return stored data for a single stakeholder."""
        with self._lock:
            return list(self._data.get(name, []))

    def get_stats(self) -> dict[str, int]:
        """Return payload counts per stakeholder."""
        with self._lock:
            return {
                "oem_count": len(self._data.get("oem", [])),
                "insurer_count": len(self._data.get("insurer", [])),
                "adas_count": len(self._data.get("adas", [])),
                "tier1_count": len(self._data.get("tier1", [])),
                "broker_count": len(self._data.get("broker", [])),
                "foreign_count": len(self._data.get("foreign", [])),
            }

    def reset(self) -> None:
        """Clear all stored data (thread-safe)."""
        with self._lock:
            for key in self._data:
                self._data[key] = []
