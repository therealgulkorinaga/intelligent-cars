"""Tests for the Chambers mock stakeholder endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_store():
    """Reset the in-memory store before each test."""
    client.delete("/admin/reset")
    yield


# ============================================================
# OEM  /oem/telemetry
# ============================================================


class TestOemTelemetry:
    def test_accepts_valid_sensor_health(self):
        resp = client.post(
            "/oem/telemetry",
            json={
                "session_id": "sess-001",
                "data_type": "sensor_health",
                "granularity": "anonymised",
                "fields": {"cpu_temp": 72.5, "battery_v": 12.4},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["stakeholder"] == "oem"

    def test_rejects_wrong_data_type(self):
        resp = client.post(
            "/oem/telemetry",
            json={
                "session_id": "sess-001",
                "data_type": "driving_behaviour",
                "granularity": "anonymised",
                "fields": {},
            },
        )
        assert resp.status_code == 422

    def test_rejects_invalid_granularity(self):
        resp = client.post(
            "/oem/telemetry",
            json={
                "session_id": "sess-001",
                "data_type": "sensor_health",
                "granularity": "raw",
                "fields": {},
            },
        )
        assert resp.status_code == 422

    def test_rejects_forbidden_gps_field(self):
        resp = client.post(
            "/oem/telemetry",
            json={
                "session_id": "sess-001",
                "data_type": "sensor_health",
                "granularity": "anonymised",
                "fields": {"cpu_temp": 72.5, "gps_position": "51.5,0.1"},
            },
        )
        assert resp.status_code == 422

    def test_accepts_aggregated_granularity(self):
        resp = client.post(
            "/oem/telemetry",
            json={
                "session_id": "sess-002",
                "data_type": "sensor_health",
                "granularity": "aggregated",
                "fields": {"avg_temp": 70.0},
            },
        )
        assert resp.status_code == 200


# ============================================================
# Insurer  /insurer/trip
# ============================================================


class TestInsurerTrip:
    def test_accepts_valid_driving_behaviour(self):
        resp = client.post(
            "/insurer/trip",
            json={
                "session_id": "sess-003",
                "data_type": "driving_behaviour",
                "granularity": "per_trip_score",
                "fields": {
                    "acceleration": 0.4,
                    "braking": 0.3,
                    "cornering_severity": 0.2,
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["stakeholder"] == "insurer"
        assert body["details"]["risk_score_received"] is True

    def test_rejects_wrong_data_type(self):
        resp = client.post(
            "/insurer/trip",
            json={
                "session_id": "sess-003",
                "data_type": "sensor_health",
                "granularity": "per_trip_score",
                "fields": {"acceleration": 0.4, "braking": 0.3, "cornering_severity": 0.2},
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_required_fields(self):
        resp = client.post(
            "/insurer/trip",
            json={
                "session_id": "sess-003",
                "data_type": "driving_behaviour",
                "granularity": "per_trip_score",
                "fields": {"acceleration": 0.4},  # missing braking and cornering_severity
            },
        )
        assert resp.status_code == 422

    def test_rejects_forbidden_gps_field(self):
        resp = client.post(
            "/insurer/trip",
            json={
                "session_id": "sess-003",
                "data_type": "driving_behaviour",
                "granularity": "per_trip_score",
                "fields": {
                    "acceleration": 0.4,
                    "braking": 0.3,
                    "cornering_severity": 0.2,
                    "gps_position": "51.5,0.1",
                },
            },
        )
        assert resp.status_code == 422


# ============================================================
# ADAS  /adas/event
# ============================================================


class TestAdasEvent:
    def _valid_payload(self) -> dict:
        return {
            "session_id": "sess-004",
            "trigger_type": "safety_critical",
            "window_start": "2026-04-01T10:00:00Z",
            "window_end": "2026-04-01T10:00:05Z",
            "data": {"speed_kmh": 60.0, "ttc_s": 0.8},
        }

    def test_accepts_safety_critical_event(self):
        resp = client.post("/adas/event", json=self._valid_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["stakeholder"] == "adas_supplier"
        assert "event_id" in body["details"]

    def test_rejects_non_safety_trigger(self):
        payload = self._valid_payload()
        payload["trigger_type"] = "normal"
        resp = client.post("/adas/event", json=payload)
        assert resp.status_code == 422


# ============================================================
# Tier-1  /tier1/diagnostics
# ============================================================


class TestTier1Diagnostics:
    def test_accepts_valid_component_telemetry(self):
        resp = client.post(
            "/tier1/diagnostics",
            json={
                "session_id": "sess-005",
                "data_type": "component_telemetry",
                "granularity": "raw",
                "fields": {"component_id": "brake_ctrl", "status": "ok"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["stakeholder"] == "tier1"

    def test_accepts_diagnostic_code(self):
        resp = client.post(
            "/tier1/diagnostics",
            json={
                "session_id": "sess-005",
                "data_type": "diagnostics",
                "granularity": "raw",
                "fields": {"dtc": "P0420"},
            },
        )
        assert resp.status_code == 200

    def test_rejects_wrong_data_type(self):
        resp = client.post(
            "/tier1/diagnostics",
            json={
                "session_id": "sess-005",
                "data_type": "sensor_health",
                "granularity": "raw",
                "fields": {},
            },
        )
        assert resp.status_code == 422

    def test_rejects_driver_identity_field(self):
        resp = client.post(
            "/tier1/diagnostics",
            json={
                "session_id": "sess-005",
                "data_type": "component_telemetry",
                "granularity": "raw",
                "fields": {"component_id": "ecu", "driver_id": "DRV-001"},
            },
        )
        assert resp.status_code == 422


# ============================================================
# Admin  /admin
# ============================================================


class TestAdmin:
    def test_received_returns_all_stakeholders(self):
        resp = client.get("/admin/received")
        assert resp.status_code == 200
        body = resp.json()
        assert "oem" in body
        assert "insurer" in body

    def test_received_by_stakeholder_oem(self):
        # Send one OEM record first
        client.post(
            "/oem/telemetry",
            json={
                "session_id": "s",
                "data_type": "sensor_health",
                "granularity": "anonymised",
                "fields": {},
            },
        )
        resp = client.get("/admin/received/oem")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_received_by_unknown_stakeholder_returns_404(self):
        resp = client.get("/admin/received/unknown_corp")
        assert resp.status_code == 404

    def test_stats_reflects_sent_records(self):
        client.post(
            "/oem/telemetry",
            json={
                "session_id": "s",
                "data_type": "sensor_health",
                "granularity": "anonymised",
                "fields": {},
            },
        )
        resp = client.get("/admin/stats")
        assert resp.status_code == 200
        assert resp.json()["oem_count"] == 1

    def test_reset_clears_data(self):
        client.post(
            "/oem/telemetry",
            json={
                "session_id": "s",
                "data_type": "sensor_health",
                "granularity": "anonymised",
                "fields": {},
            },
        )
        client.delete("/admin/reset")
        resp = client.get("/admin/stats")
        assert resp.json()["oem_count"] == 0


# ============================================================
# Rogue endpoints  /broker  /foreign
# ============================================================


class TestRogueEndpoints:
    def test_broker_returns_200_with_warning(self):
        resp = client.post("/broker/data", json={"payload": "secret"})
        assert resp.status_code == 200
        assert "SECURITY ALERT" in resp.json().get("warning", "")

    def test_foreign_returns_200_with_warning(self):
        resp = client.post("/foreign/telemetry", json={"data": "telemetry"})
        assert resp.status_code == 200
        assert "JURISDICTION ALERT" in resp.json().get("warning", "")
