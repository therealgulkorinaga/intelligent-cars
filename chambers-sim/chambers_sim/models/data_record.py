"""Pydantic models matching the Rust core types for vehicle data records."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DataType(str, Enum):
    """Classification of vehicle data types aligned with Rust core."""

    POSITION = "Position"
    SPEED = "Speed"
    ACCELERATION = "Acceleration"
    SENSOR_HEALTH = "SensorHealth"
    DRIVING_BEHAVIOUR = "DrivingBehaviour"
    CAMERA_FRAME = "CameraFrame"
    LIDAR_CLOUD = "LidarCloud"
    GNSS_POSITION = "GnssPosition"
    IMU_READING = "ImuReading"
    DIAGNOSTIC_CODE = "DiagnosticCode"
    CONTACT_SYNC = "ContactSync"
    MEDIA_METADATA = "MediaMetadata"
    V2X_CAM = "V2xCam"
    SEALED_EVENT = "SealedEvent"


class Granularity(str, Enum):
    """Data granularity/precision level."""

    RAW = "Raw"
    AGGREGATED = "Aggregated"
    ANONYMISED = "Anonymised"
    PER_TRIP_SCORE = "PerTripScore"


class Jurisdiction(str, Enum):
    """Legal jurisdiction governing data processing."""

    EU = "EU"
    US = "US"
    CN = "CN"
    UK = "UK"


class LegalBasis(str, Enum):
    """Legal basis for data processing under GDPR and equivalent regulations."""

    CONSENT = "Consent"
    LEGITIMATE_INTEREST = "LegitimateInterest"
    CONTRACT = "Contract"
    LEGAL_OBLIGATION = "LegalObligation"
    VITAL_INTEREST = "VitalInterest"
    PUBLIC_TASK = "PublicTask"


class ChannelType(str, Enum):
    """Communication channel through which data is collected or transmitted."""

    CELLULAR = "Cellular"
    BLUETOOTH = "Bluetooth"
    WIFI = "WiFi"
    OBD_II = "ObdII"
    V2X = "V2x"


class DataRecord(BaseModel):
    """A single vehicle data record produced by a simulator adapter."""

    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    data_type: DataType
    fields: dict[str, Any] = Field(default_factory=dict)
    channel: ChannelType = ChannelType.CELLULAR

    model_config = {"use_enum_values": False}


class FilteredDataRecord(BaseModel):
    """A data record after privacy filtering for a specific stakeholder."""

    stakeholder_id: str
    data_type: DataType
    fields: dict[str, Any] = Field(default_factory=dict)
    granularity: Granularity = Granularity.RAW


class ProcessingResult(BaseModel):
    """Result of processing a data record through the gateway."""

    records_transmitted: list[FilteredDataRecord] = Field(default_factory=list)
    records_blocked: list[str] = Field(default_factory=list)


class BurnReceipt(BaseModel):
    """Cryptographic receipt confirming data destruction across all layers."""

    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    layers_completed: list[str] = Field(default_factory=list)
    success: bool = True


class SessionSummary(BaseModel):
    """Summary statistics for a completed data session."""

    session_id: str
    start: datetime
    end: datetime
    records_generated: int = 0
    records_transmitted: int = 0
    records_blocked: int = 0
    records_burned: int = 0
    stakeholder_breakdown: dict[str, dict[str, int]] = Field(default_factory=dict)
