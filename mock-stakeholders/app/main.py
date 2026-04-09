"""Chambers Mock Stakeholder API.

FastAPI application that simulates the external endpoints each stakeholder
operates.  The Chambers gateway routes filtered data to these endpoints
according to the preservation manifest.  Each route validates that it only
receives what the manifest allows and rejects anything else -- mirroring
real-world stakeholder contracts.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException

from app.models import (
    AcceptResponse,
    SealedEventPayload,
    StatsResponse,
    TelemetryPayload,
)
from app.storage import DataStore

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log: structlog.BoundLogger = structlog.get_logger()

# ---------------------------------------------------------------------------
# App & store
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Chambers Mock Stakeholders",
    description="Simulated external stakeholder endpoints for the Chambers Automotive Simulation Testbed",
    version="0.1.0",
)

store = DataStore()


# ===================================================================
# OEM CLOUD  /oem
# ===================================================================

ALLOWED_OEM_GRANULARITIES = {"anonymised", "aggregated", "anonymised_aggregate"}


@app.post("/oem/telemetry", response_model=AcceptResponse)
async def oem_telemetry(payload: TelemetryPayload) -> AcceptResponse:
    """Accept anonymised sensor-health aggregates for the OEM.

    Rejects:
    - Any data_type other than ``sensor_health``
    - Granularity that is not ``anonymised`` or ``aggregated``
    - Payloads containing raw GPS, driving behaviour data, or camera frames
    """
    logger = log.bind(stakeholder="oem", session_id=payload.session_id)

    # -- type gate ---------------------------------------------------------
    if payload.data_type != "sensor_health":
        logger.warning(
            "oem_rejected",
            reason="invalid data_type",
            data_type=payload.data_type,
        )
        raise HTTPException(
            status_code=422,
            detail=f"OEM endpoint only accepts sensor_health, got '{payload.data_type}'",
        )

    # -- granularity gate --------------------------------------------------
    if payload.granularity not in ALLOWED_OEM_GRANULARITIES:
        logger.warning(
            "oem_rejected",
            reason="invalid granularity",
            granularity=payload.granularity,
        )
        raise HTTPException(
            status_code=422,
            detail=f"OEM requires anonymised/aggregated granularity, got '{payload.granularity}'",
        )

    # -- forbidden fields gate ---------------------------------------------
    forbidden_keys = {"gps_position", "driving_behaviour", "camera_frame", "raw_gps", "route"}
    present_forbidden = forbidden_keys & set(payload.fields.keys())
    if present_forbidden:
        logger.warning(
            "oem_rejected",
            reason="forbidden fields present",
            fields=sorted(present_forbidden),
        )
        raise HTTPException(
            status_code=422,
            detail=f"OEM endpoint rejects fields: {sorted(present_forbidden)}",
        )

    # -- accept ------------------------------------------------------------
    store.add("oem", payload.model_dump(mode="json"))
    logger.info("oem_accepted", data_type=payload.data_type, granularity=payload.granularity)

    return AcceptResponse(
        status="accepted",
        stakeholder="oem",
        timestamp=datetime.utcnow(),
    )


# ===================================================================
# INSURER  /insurer
# ===================================================================

REQUIRED_INSURER_FIELDS = {"acceleration", "braking", "cornering_severity"}
FORBIDDEN_INSURER_FIELDS = {"gps_position", "timestamps", "route"}


@app.post("/insurer/trip", response_model=AcceptResponse)
async def insurer_trip(payload: TelemetryPayload) -> AcceptResponse:
    """Accept driving-behaviour trip scores for the insurer.

    Rejects:
    - Any data_type other than ``driving_behaviour``
    - Payloads missing required scoring fields
    - Payloads that contain GPS, timestamps, or route data
    """
    logger = log.bind(stakeholder="insurer", session_id=payload.session_id)

    # -- type gate ---------------------------------------------------------
    if payload.data_type != "driving_behaviour":
        logger.warning(
            "insurer_rejected",
            reason="invalid data_type",
            data_type=payload.data_type,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Insurer endpoint only accepts driving_behaviour, got '{payload.data_type}'",
        )

    # -- required fields ---------------------------------------------------
    missing = REQUIRED_INSURER_FIELDS - set(payload.fields.keys())
    if missing:
        logger.warning(
            "insurer_rejected",
            reason="missing required fields",
            missing=sorted(missing),
        )
        raise HTTPException(
            status_code=422,
            detail=f"Insurer requires fields: {sorted(REQUIRED_INSURER_FIELDS)}; missing {sorted(missing)}",
        )

    # -- forbidden fields --------------------------------------------------
    present_forbidden = FORBIDDEN_INSURER_FIELDS & set(payload.fields.keys())
    if present_forbidden:
        logger.warning(
            "insurer_rejected",
            reason="forbidden fields present",
            fields=sorted(present_forbidden),
        )
        raise HTTPException(
            status_code=422,
            detail=f"Insurer endpoint rejects fields: {sorted(present_forbidden)}",
        )

    # -- accept ------------------------------------------------------------
    store.add("insurer", payload.model_dump(mode="json"))
    logger.info("insurer_accepted", data_type=payload.data_type)

    return AcceptResponse(
        status="accepted",
        stakeholder="insurer",
        timestamp=datetime.utcnow(),
        details={"risk_score_received": True},
    )


# ===================================================================
# ADAS SUPPLIER  /adas
# ===================================================================

@app.post("/adas/event", response_model=AcceptResponse)
async def adas_event(payload: SealedEventPayload) -> AcceptResponse:
    """Accept sealed safety-critical events for the ADAS supplier.

    Rejects:
    - Trigger type other than ``safety_critical``
    """
    logger = log.bind(stakeholder="adas_supplier", session_id=payload.session_id)

    # -- trigger gate ------------------------------------------------------
    if payload.trigger_type != "safety_critical":
        logger.warning(
            "adas_rejected",
            reason="non-safety trigger",
            trigger_type=payload.trigger_type,
        )
        raise HTTPException(
            status_code=422,
            detail=f"ADAS endpoint only accepts safety_critical triggers, got '{payload.trigger_type}'",
        )

    # -- accept ------------------------------------------------------------
    event_id = str(uuid.uuid4())
    store.add("adas", payload.model_dump(mode="json"))
    logger.info("adas_accepted", trigger=payload.trigger_type, event_id=event_id)

    return AcceptResponse(
        status="accepted",
        stakeholder="adas_supplier",
        timestamp=datetime.utcnow(),
        details={"event_id": event_id},
    )


# ===================================================================
# TIER-1 SUPPLIER  /tier1
# ===================================================================

ALLOWED_TIER1_TYPES = {"component_telemetry", "diagnostic_code", "diagnostics"}
FORBIDDEN_TIER1_FIELDS = {"driver_id", "driver_name", "driver_identity", "personal_id"}


@app.post("/tier1/diagnostics", response_model=AcceptResponse)
async def tier1_diagnostics(payload: TelemetryPayload) -> AcceptResponse:
    """Accept component-specific diagnostics for the Tier-1 supplier.

    Rejects:
    - Any data_type not related to diagnostics / component telemetry
    - Payloads containing driver identity information
    """
    logger = log.bind(stakeholder="tier1", session_id=payload.session_id)

    # -- type gate ---------------------------------------------------------
    if payload.data_type not in ALLOWED_TIER1_TYPES:
        logger.warning(
            "tier1_rejected",
            reason="invalid data_type",
            data_type=payload.data_type,
        )
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tier-1 endpoint only accepts {sorted(ALLOWED_TIER1_TYPES)}, "
                f"got '{payload.data_type}'"
            ),
        )

    # -- no driver identity ------------------------------------------------
    present_forbidden = FORBIDDEN_TIER1_FIELDS & set(payload.fields.keys())
    if present_forbidden:
        logger.warning(
            "tier1_rejected",
            reason="driver identity present",
            fields=sorted(present_forbidden),
        )
        raise HTTPException(
            status_code=422,
            detail=f"Tier-1 endpoint rejects driver identity fields: {sorted(present_forbidden)}",
        )

    # -- accept ------------------------------------------------------------
    store.add("tier1", payload.model_dump(mode="json"))
    logger.info("tier1_accepted", data_type=payload.data_type)

    return AcceptResponse(
        status="accepted",
        stakeholder="tier1",
        timestamp=datetime.utcnow(),
    )


# ===================================================================
# ADMIN / TEST  /admin
# ===================================================================

@app.get("/admin/received")
async def admin_received() -> dict[str, Any]:
    """Return all data received across every stakeholder endpoint."""
    return store.get_all()


@app.get("/admin/received/{stakeholder}")
async def admin_received_by_stakeholder(stakeholder: str) -> list[dict[str, Any]]:
    """Return data received by a specific stakeholder."""
    valid = {"oem", "insurer", "adas", "tier1", "broker", "foreign"}
    if stakeholder not in valid:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown stakeholder '{stakeholder}'. Valid: {sorted(valid)}",
        )
    return store.get_by_stakeholder(stakeholder)


@app.delete("/admin/reset")
async def admin_reset() -> dict[str, str]:
    """Clear all stored data (used between test runs)."""
    store.reset()
    log.info("admin_reset", message="all stored data cleared")
    return {"status": "reset"}


@app.get("/admin/stats", response_model=StatsResponse)
async def admin_stats() -> StatsResponse:
    """Return payload counts per stakeholder."""
    return StatsResponse(**store.get_stats())


# ===================================================================
# DATA BROKER (rogue)  /broker
# ===================================================================

@app.post("/broker/data")
async def broker_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Rogue data-broker endpoint -- should NEVER receive real data.

    Always returns 200 so the caller thinks delivery succeeded, but the
    system logs a **SECURITY ALERT**.  Used by threat simulation T3.
    """
    log.error(
        "SECURITY_ALERT",
        message="Data broker endpoint received data -- potential third-party selling attempt",
        payload_keys=sorted(payload.keys()) if payload else [],
    )
    store.add("broker", payload)
    return {"status": "accepted", "stakeholder": "broker", "warning": "SECURITY ALERT LOGGED"}


# ===================================================================
# FOREIGN ENDPOINT  /foreign
# ===================================================================

@app.post("/foreign/telemetry")
async def foreign_telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Non-EU jurisdiction endpoint -- should NEVER receive data.

    The Chambers gateway must block transfers to non-EU endpoints based
    on the manifest's ``jurisdiction`` constraint.  Returns 200 but logs
    a security alert.  Used by threat simulation T4.
    """
    log.error(
        "SECURITY_ALERT",
        message="Foreign (non-EU) endpoint received data -- jurisdiction block failed",
        payload_keys=sorted(payload.keys()) if payload else [],
    )
    store.add("foreign", payload)
    return {"status": "accepted", "stakeholder": "foreign", "warning": "JURISDICTION ALERT LOGGED"}
