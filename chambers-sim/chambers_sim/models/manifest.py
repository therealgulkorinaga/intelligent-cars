"""Pydantic models for the Chambers preservation manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field

from chambers_sim.models.data_record import (
    DataType,
    Granularity,
    Jurisdiction,
    LegalBasis,
)


class CategoryDeclaration(BaseModel):
    """Declares what data a stakeholder may access and at what granularity."""

    data_type: DataType
    fields: list[str] = Field(default_factory=list)
    excluded_fields: list[str] = Field(default_factory=list)
    granularity: Granularity = Granularity.RAW
    retention: str = "P30D"
    purpose: str = ""
    jurisdiction: Jurisdiction = Jurisdiction.EU


class StakeholderDeclaration(BaseModel):
    """Declares a stakeholder's access rights and legal basis."""

    id: str
    role: str
    legal_basis: LegalBasis = LegalBasis.CONSENT
    consent_ref: str = ""
    categories: list[CategoryDeclaration] = Field(default_factory=list)


class MandatoryRetention(BaseModel):
    """Data that must be retained regardless of consent (regulatory requirement)."""

    type: DataType
    regulation: str = ""
    treatment: str = "encrypted_archive"


class PreservationManifest(BaseModel):
    """The core manifest that governs how vehicle data is routed and preserved."""

    manifest_version: str = "1.0"
    vehicle_id: str = ""
    session_id: str = ""
    stakeholders: list[StakeholderDeclaration] = Field(default_factory=list)
    mandatory_retention: list[MandatoryRetention] = Field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> Self:
        """Load a manifest from a JSON file on disk."""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    @classmethod
    def default_demo_manifest(cls) -> Self:
        """Return the demo manifest from PRD Section 7.

        Stakeholders: OEM, insurer, ADAS supplier, Tier-1 diagnostics.
        """
        return cls(
            manifest_version="1.0",
            vehicle_id="demo-vehicle-001",
            session_id="",
            stakeholders=[
                # OEM: gets anonymised position, speed, acceleration, sensor health,
                # driving behaviour (aggregated), diagnostic codes
                StakeholderDeclaration(
                    id="oem-stellantis",
                    role="OEM",
                    legal_basis=LegalBasis.CONTRACT,
                    consent_ref="contract-vehicle-purchase-2024",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.POSITION,
                            fields=["latitude", "longitude", "altitude", "heading"],
                            excluded_fields=[],
                            granularity=Granularity.ANONYMISED,
                            retention="P90D",
                            purpose="Fleet telemetry and warranty analytics",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.SPEED,
                            fields=["speed_mps", "speed_limit", "road_type"],
                            excluded_fields=[],
                            granularity=Granularity.RAW,
                            retention="P90D",
                            purpose="Powertrain performance monitoring",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.ACCELERATION,
                            fields=["longitudinal", "lateral", "vertical"],
                            excluded_fields=[],
                            granularity=Granularity.RAW,
                            retention="P90D",
                            purpose="Suspension and dynamics tuning",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.SENSOR_HEALTH,
                            fields=["sensor_id", "status", "temperature", "uptime_hours"],
                            excluded_fields=[],
                            granularity=Granularity.RAW,
                            retention="P365D",
                            purpose="Predictive maintenance",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.DRIVING_BEHAVIOUR,
                            fields=["score", "harsh_braking_count", "harsh_accel_count"],
                            excluded_fields=["raw_speed_trace", "raw_accel_trace"],
                            granularity=Granularity.AGGREGATED,
                            retention="P30D",
                            purpose="Quality and safety analytics",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.DIAGNOSTIC_CODE,
                            fields=["dtc_code", "severity", "module", "mileage_km"],
                            excluded_fields=[],
                            granularity=Granularity.RAW,
                            retention="P365D",
                            purpose="Warranty and recall management",
                            jurisdiction=Jurisdiction.EU,
                        ),
                    ],
                ),
                # Insurer: gets per-trip driving behaviour score only, no GPS
                StakeholderDeclaration(
                    id="insurer-allianz",
                    role="Insurer",
                    legal_basis=LegalBasis.CONSENT,
                    consent_ref="consent-telematics-policy-2024-001",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.DRIVING_BEHAVIOUR,
                            fields=[
                                "score",
                                "distance_km",
                                "duration_minutes",
                                "time_of_day_bucket",
                            ],
                            excluded_fields=[
                                "raw_speed_trace",
                                "raw_accel_trace",
                                "latitude",
                                "longitude",
                                "route",
                            ],
                            granularity=Granularity.PER_TRIP_SCORE,
                            retention="P365D",
                            purpose="Usage-based insurance premium calculation",
                            jurisdiction=Jurisdiction.EU,
                        ),
                    ],
                ),
                # ADAS supplier: gets sealed events only (crash / near-miss)
                StakeholderDeclaration(
                    id="adas-mobileye",
                    role="ADAS Supplier",
                    legal_basis=LegalBasis.LEGITIMATE_INTEREST,
                    consent_ref="lia-adas-safety-improvement",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.SEALED_EVENT,
                            fields=[
                                "trigger_type",
                                "trigger_timestamp",
                                "window_start",
                                "window_end",
                                "camera_frames",
                                "lidar_snapshots",
                                "imu_trace",
                                "gnss_trace",
                                "speed_trace",
                            ],
                            excluded_fields=["driver_face_crop", "cabin_audio"],
                            granularity=Granularity.RAW,
                            retention="P730D",
                            purpose="Safety algorithm improvement and validation",
                            jurisdiction=Jurisdiction.EU,
                        ),
                    ],
                ),
                # Tier-1 diagnostics supplier
                StakeholderDeclaration(
                    id="tier1-bosch",
                    role="Tier-1 Supplier",
                    legal_basis=LegalBasis.CONTRACT,
                    consent_ref="contract-component-supply-2024",
                    categories=[
                        CategoryDeclaration(
                            data_type=DataType.SENSOR_HEALTH,
                            fields=["sensor_id", "status", "temperature", "error_count"],
                            excluded_fields=["firmware_version", "serial_number"],
                            granularity=Granularity.RAW,
                            retention="P180D",
                            purpose="Component reliability analysis",
                            jurisdiction=Jurisdiction.EU,
                        ),
                        CategoryDeclaration(
                            data_type=DataType.DIAGNOSTIC_CODE,
                            fields=["dtc_code", "severity", "module"],
                            excluded_fields=["vin", "mileage_km", "driver_id"],
                            granularity=Granularity.RAW,
                            retention="P180D",
                            purpose="Defect trend analysis",
                            jurisdiction=Jurisdiction.EU,
                        ),
                    ],
                ),
            ],
            mandatory_retention=[
                MandatoryRetention(
                    type=DataType.SEALED_EVENT,
                    regulation="EU-EDR-2024",
                    treatment="encrypted_archive",
                ),
                MandatoryRetention(
                    type=DataType.DIAGNOSTIC_CODE,
                    regulation="EU-Type-Approval",
                    treatment="encrypted_archive",
                ),
            ],
        )
