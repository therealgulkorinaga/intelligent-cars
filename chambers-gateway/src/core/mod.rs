use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;
use uuid::Uuid;

// ── SessionId ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct SessionId(pub Uuid);

impl SessionId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }

    pub fn from_uuid(u: Uuid) -> Self {
        Self(u)
    }
}

impl fmt::Display for SessionId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl Default for SessionId {
    fn default() -> Self {
        Self::new()
    }
}

// ── StakeholderId ──────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct StakeholderId(pub String);

impl StakeholderId {
    pub fn new(id: impl Into<String>) -> Self {
        Self(id.into())
    }
}

impl fmt::Display for StakeholderId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

// ── DataType ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DataType {
    Position,
    Speed,
    Acceleration,
    SensorHealth,
    DrivingBehaviour,
    CameraFrame,
    LidarCloud,
    GnssPosition,
    ImuReading,
    DiagnosticCode,
    ContactSync,
    MediaMetadata,
    V2xCam,
    AdasEvent,
    BrakeEvent,
    SteeringAngle,
    ThrottlePosition,
    FuelLevel,
    BatteryState,
    TirePressure,
    AmbientTemperature,
    OccupantWeight,
    SeatbeltStatus,
    AirbagStatus,
    Custom(String),
}

impl fmt::Display for DataType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DataType::Custom(s) => write!(f, "Custom({})", s),
            other => write!(f, "{:?}", other),
        }
    }
}

// ── DataCategory ───────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DataCategory {
    Telemetry,
    Safety,
    Diagnostics,
    PersonalIdentifiable,
    Location,
    Media,
    V2x,
    Adas,
    Insurance,
}

impl DataCategory {
    pub fn from_data_type(dt: &DataType) -> Self {
        match dt {
            DataType::Position | DataType::GnssPosition => DataCategory::Location,
            DataType::Speed | DataType::Acceleration | DataType::ImuReading => {
                DataCategory::Telemetry
            }
            DataType::SensorHealth | DataType::DiagnosticCode => DataCategory::Diagnostics,
            DataType::DrivingBehaviour
            | DataType::BrakeEvent
            | DataType::SteeringAngle
            | DataType::ThrottlePosition => DataCategory::Insurance,
            DataType::CameraFrame | DataType::LidarCloud => DataCategory::Media,
            DataType::V2xCam => DataCategory::V2x,
            DataType::AdasEvent => DataCategory::Adas,
            DataType::ContactSync | DataType::MediaMetadata | DataType::OccupantWeight => {
                DataCategory::PersonalIdentifiable
            }
            DataType::AirbagStatus | DataType::SeatbeltStatus => DataCategory::Safety,
            DataType::FuelLevel
            | DataType::BatteryState
            | DataType::TirePressure
            | DataType::AmbientTemperature => DataCategory::Telemetry,
            DataType::Custom(_) => DataCategory::Telemetry,
        }
    }
}

impl fmt::Display for DataCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}", self)
    }
}

// ── Granularity ────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Granularity {
    Raw,
    Aggregated,
    Anonymised,
    PerTripScore,
}

// ── Jurisdiction ───────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Jurisdiction {
    EU,
    US,
    CN,
    UK,
    Other(String),
}

impl fmt::Display for Jurisdiction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Jurisdiction::Other(s) => write!(f, "Other({})", s),
            other => write!(f, "{:?}", other),
        }
    }
}

// ── LegalBasis ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum LegalBasis {
    LegitimateInterest,
    ExplicitConsent,
    ContractualNecessity,
    LegalObligation,
}

// ── ChannelType ────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ChannelType {
    Cellular,
    Bluetooth,
    WiFi,
    ObdII,
    V2x,
}

// ── EnforcementPoint ───────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum EnforcementPoint {
    EP1Cellular,
    EP2Bluetooth,
    EP3ObdII,
    EP4V2x,
    EP5WiFi,
}

// ── DataRecord ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataRecord {
    pub session_id: SessionId,
    pub timestamp: DateTime<Utc>,
    pub source: String,
    pub data_type: DataType,
    pub fields: HashMap<String, serde_json::Value>,
    pub raw_bytes: Option<Vec<u8>>,
}

impl DataRecord {
    pub fn new(
        session_id: SessionId,
        source: impl Into<String>,
        data_type: DataType,
        fields: HashMap<String, serde_json::Value>,
    ) -> Self {
        Self {
            session_id,
            timestamp: Utc::now(),
            source: source.into(),
            data_type,
            fields,
            raw_bytes: None,
        }
    }

    pub fn with_raw_bytes(mut self, bytes: Vec<u8>) -> Self {
        self.raw_bytes = Some(bytes);
        self
    }

    pub fn category(&self) -> DataCategory {
        DataCategory::from_data_type(&self.data_type)
    }
}

// ── Errors ─────────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum ChambersError {
    #[error("HSM error: {0}")]
    Hsm(String),

    #[error("Session not found: {0}")]
    SessionNotFound(SessionId),

    #[error("Key not found: handle {0}")]
    KeyNotFound(u64),

    #[error("Key destroyed: handle {0}")]
    KeyDestroyed(u64),

    #[error("Encryption error: {0}")]
    Encryption(String),

    #[error("Decryption error: {0}")]
    Decryption(String),

    #[error("Manifest error: {0}")]
    Manifest(String),

    #[error("Audit error: {0}")]
    Audit(String),

    #[error("Burn error: {0}")]
    Burn(String),

    #[error("Gateway error: {0}")]
    Gateway(String),

    #[error("Jurisdiction blocked: {0}")]
    JurisdictionBlocked(String),

    #[error("Stakeholder not found: {0}")]
    StakeholderNotFound(String),

    #[error("Fallback mode active — all telemetry blocked")]
    FallbackMode,

    #[error("Serialization error: {0}")]
    Serialization(String),

    #[error("Database error: {0}")]
    Database(String),
}

impl From<serde_json::Error> for ChambersError {
    fn from(e: serde_json::Error) -> Self {
        ChambersError::Serialization(e.to_string())
    }
}

impl From<rusqlite::Error> for ChambersError {
    fn from(e: rusqlite::Error) -> Self {
        ChambersError::Database(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, ChambersError>;
