use crate::core::{
    ChambersError, DataRecord, DataType, Granularity, Jurisdiction, LegalBasis, Result,
    StakeholderId,
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

// ── Manifest schema ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreservationManifest {
    pub manifest_version: String,
    pub vehicle_id: String,
    pub session_id: Option<String>,
    pub stakeholders: Vec<StakeholderDeclaration>,
    pub mandatory_retention: Vec<MandatoryRetention>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StakeholderDeclaration {
    pub id: String,
    pub role: String,
    pub legal_basis: LegalBasis,
    pub categories: Vec<CategoryDeclaration>,
    pub endpoint_jurisdiction: Jurisdiction,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CategoryDeclaration {
    pub data_type: DataType,
    pub fields: Option<Vec<String>>,
    pub excluded_fields: Option<Vec<String>>,
    pub granularity: Granularity,
    pub retention: String,
    pub purpose: String,
    pub jurisdiction: Vec<Jurisdiction>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MandatoryRetention {
    pub authority: String,
    pub data_types: Vec<DataType>,
    pub minimum_retention: String,
    pub legal_reference: String,
}

// ── FilteredDataRecord ─────────────────────────────────────────────────

/// The subset of a DataRecord that a stakeholder is allowed to see,
/// after field filtering, anonymisation, and granularity reduction.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FilteredDataRecord {
    pub data_type: DataType,
    pub timestamp: DateTime<Utc>,
    pub source: String,
    pub granularity: Granularity,
    pub fields: HashMap<String, serde_json::Value>,
}

// ── Revocation tracking ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RevocationRecord {
    pub stakeholder_id: StakeholderId,
    pub revoked_at: DateTime<Utc>,
}

// ── ManifestEvaluator ──────────────────────────────────────────────────

/// Evaluates incoming DataRecords against a PreservationManifest,
/// producing per-stakeholder filtered views.
pub struct ManifestEvaluator {
    manifest: Arc<Mutex<PreservationManifest>>,
    revocations: Arc<Mutex<Vec<RevocationRecord>>>,
}

impl ManifestEvaluator {
    pub fn new(manifest: PreservationManifest) -> Self {
        Self {
            manifest: Arc::new(Mutex::new(manifest)),
            revocations: Arc::new(Mutex::new(Vec::new())),
        }
    }

    /// Replace the active manifest (e.g., after OTA update).
    pub fn update_manifest(&self, manifest: PreservationManifest) {
        let mut m = self.manifest.lock().unwrap();
        *m = manifest;
    }

    /// Evaluate a DataRecord and return the per-stakeholder filtered views.
    /// Each stakeholder only gets the fields they declared at the declared granularity.
    pub fn evaluate(
        &self,
        record: &DataRecord,
    ) -> Result<Vec<(StakeholderId, FilteredDataRecord)>> {
        let manifest = self.manifest.lock().map_err(|e| {
            ChambersError::Manifest(format!("Failed to lock manifest: {}", e))
        })?;
        let revocations = self.revocations.lock().map_err(|e| {
            ChambersError::Manifest(format!("Failed to lock revocations: {}", e))
        })?;

        let mut results = Vec::new();

        for stakeholder in &manifest.stakeholders {
            let sid = StakeholderId::new(&stakeholder.id);

            // Skip revoked stakeholders
            if revocations.iter().any(|r| r.stakeholder_id == sid) {
                continue;
            }

            // Find matching category declaration for this data type
            let matching_category = stakeholder
                .categories
                .iter()
                .find(|cat| cat.data_type == record.data_type);

            let category = match matching_category {
                Some(c) => c,
                None => continue, // stakeholder did not declare this data type
            };

            // Jurisdiction check: stakeholder endpoint must be in declared jurisdictions
            if !category.jurisdiction.contains(&stakeholder.endpoint_jurisdiction) {
                continue; // jurisdiction mismatch — block
            }

            // Build filtered fields
            let filtered_fields =
                Self::filter_fields(&record.fields, &category);

            // Apply granularity
            let final_fields =
                Self::apply_granularity(filtered_fields, &category.granularity, &record.data_type);

            let filtered = FilteredDataRecord {
                data_type: record.data_type.clone(),
                timestamp: record.timestamp,
                source: record.source.clone(),
                granularity: category.granularity.clone(),
                fields: final_fields,
            };

            results.push((sid, filtered));
        }

        Ok(results)
    }

    /// Check whether a specific stakeholder + data_type + jurisdiction combination is allowed.
    pub fn is_jurisdiction_allowed(
        &self,
        stakeholder_id: &str,
        data_type: &DataType,
        target_jurisdiction: &Jurisdiction,
    ) -> Result<bool> {
        let manifest = self.manifest.lock().map_err(|e| {
            ChambersError::Manifest(format!("Failed to lock manifest: {}", e))
        })?;

        for stakeholder in &manifest.stakeholders {
            if stakeholder.id == stakeholder_id {
                for cat in &stakeholder.categories {
                    if cat.data_type == *data_type {
                        return Ok(cat.jurisdiction.contains(target_jurisdiction));
                    }
                }
            }
        }
        Ok(false)
    }

    /// Revoke consent for a stakeholder. Returns the timestamp of revocation.
    pub fn revoke_consent(&self, stakeholder_id: &StakeholderId) -> Result<DateTime<Utc>> {
        let manifest = self.manifest.lock().map_err(|e| {
            ChambersError::Manifest(format!("Failed to lock manifest: {}", e))
        })?;

        // Verify stakeholder exists
        let exists = manifest
            .stakeholders
            .iter()
            .any(|s| s.id == stakeholder_id.0);

        if !exists {
            return Err(ChambersError::StakeholderNotFound(
                stakeholder_id.0.clone(),
            ));
        }

        drop(manifest);

        let now = Utc::now();
        let mut revocations = self.revocations.lock().map_err(|e| {
            ChambersError::Manifest(format!("Failed to lock revocations: {}", e))
        })?;

        // Don't double-revoke
        if !revocations
            .iter()
            .any(|r| r.stakeholder_id == *stakeholder_id)
        {
            revocations.push(RevocationRecord {
                stakeholder_id: stakeholder_id.clone(),
                revoked_at: now,
            });
        }

        Ok(now)
    }

    /// Check whether a stakeholder has been revoked.
    pub fn is_revoked(&self, stakeholder_id: &StakeholderId) -> bool {
        let revocations = self.revocations.lock().unwrap();
        revocations
            .iter()
            .any(|r| r.stakeholder_id == *stakeholder_id)
    }

    /// Get a copy of the current manifest.
    pub fn get_manifest(&self) -> PreservationManifest {
        self.manifest.lock().unwrap().clone()
    }

    // ── Private helpers ────────────────────────────────────────────────

    /// Filter fields according to the category declaration:
    /// - If `fields` is Some, only include those fields.
    /// - If `excluded_fields` is Some, exclude those fields.
    fn filter_fields(
        record_fields: &HashMap<String, serde_json::Value>,
        category: &CategoryDeclaration,
    ) -> HashMap<String, serde_json::Value> {
        let mut result = record_fields.clone();

        // If explicit field allowlist, keep only those
        if let Some(ref allowed) = category.fields {
            result.retain(|k, _| allowed.contains(k));
        }

        // Remove excluded fields
        if let Some(ref excluded) = category.excluded_fields {
            for field in excluded {
                result.remove(field);
            }
        }

        result
    }

    /// Apply granularity transformations:
    /// - Raw: no change
    /// - Aggregated: round numeric values, coarsen timestamps
    /// - Anonymised: strip identity fields, hash identifiers, coarsen location
    /// - PerTripScore: collapse all fields into a single score
    fn apply_granularity(
        mut fields: HashMap<String, serde_json::Value>,
        granularity: &Granularity,
        _data_type: &DataType,
    ) -> HashMap<String, serde_json::Value> {
        match granularity {
            Granularity::Raw => fields,

            Granularity::Aggregated => {
                // Round numeric values to reduce precision
                for (_k, v) in fields.iter_mut() {
                    if let Some(n) = v.as_f64() {
                        *v = serde_json::Value::Number(
                            serde_json::Number::from_f64((n * 10.0).round() / 10.0)
                                .unwrap_or_else(|| serde_json::Number::from(0)),
                        );
                    }
                }
                // Strip identity-like fields
                let identity_fields = [
                    "driver_id",
                    "vin",
                    "name",
                    "email",
                    "phone",
                    "contact",
                    "ssn",
                    "license_plate",
                ];
                for f in &identity_fields {
                    fields.remove(*f);
                }
                fields
            }

            Granularity::Anonymised => {
                // Strip all identity fields
                let identity_fields = [
                    "driver_id",
                    "vin",
                    "name",
                    "email",
                    "phone",
                    "contact",
                    "ssn",
                    "license_plate",
                    "device_id",
                    "imei",
                    "mac_address",
                ];
                for f in &identity_fields {
                    fields.remove(*f);
                }

                // GPS-related fields that get special coarsening treatment
                let gps_fields = ["latitude", "longitude"];

                // Round non-GPS numeric values aggressively (to whole numbers)
                for (k, v) in fields.iter_mut() {
                    if gps_fields.contains(&k.as_str()) {
                        continue; // GPS handled separately below
                    }
                    if let Some(n) = v.as_f64() {
                        *v = serde_json::Value::Number(
                            serde_json::Number::from_f64(n.round())
                                .unwrap_or_else(|| serde_json::Number::from(0)),
                        );
                    }
                }

                // Coarsen GPS coordinates (reduce to ~1km resolution = 2 decimal places)
                if let Some(lat) = fields.get("latitude").and_then(|v| v.as_f64()) {
                    fields.insert(
                        "latitude".to_string(),
                        serde_json::Value::Number(
                            serde_json::Number::from_f64((lat * 100.0).round() / 100.0)
                                .unwrap_or_else(|| serde_json::Number::from(0)),
                        ),
                    );
                }
                if let Some(lon) = fields.get("longitude").and_then(|v| v.as_f64()) {
                    fields.insert(
                        "longitude".to_string(),
                        serde_json::Value::Number(
                            serde_json::Number::from_f64((lon * 100.0).round() / 100.0)
                                .unwrap_or_else(|| serde_json::Number::from(0)),
                        ),
                    );
                }

                fields
            }

            Granularity::PerTripScore => {
                // Collapse all numeric fields into a single aggregate score.
                // Score = average of all numeric values, clamped to 0..100.
                let numeric_values: Vec<f64> = fields
                    .values()
                    .filter_map(|v| v.as_f64())
                    .collect();

                let score = if numeric_values.is_empty() {
                    50.0 // default neutral score
                } else {
                    let avg: f64 =
                        numeric_values.iter().sum::<f64>() / numeric_values.len() as f64;
                    avg.max(0.0).min(100.0)
                };

                let mut result = HashMap::new();
                result.insert(
                    "trip_score".to_string(),
                    serde_json::Value::Number(
                        serde_json::Number::from_f64((score * 10.0).round() / 10.0)
                            .unwrap_or_else(|| serde_json::Number::from(0)),
                    ),
                );
                result
            }
        }
    }
}

/// Build the demo manifest from PRD Section 7 for testing.
pub fn demo_manifest(vehicle_id: &str) -> PreservationManifest {
    PreservationManifest {
        manifest_version: "1.0".to_string(),
        vehicle_id: vehicle_id.to_string(),
        session_id: None,
        stakeholders: vec![
            StakeholderDeclaration {
                id: "oem-cloud".to_string(),
                role: "OEM".to_string(),
                legal_basis: LegalBasis::LegitimateInterest,
                endpoint_jurisdiction: Jurisdiction::EU,
                categories: vec![
                    CategoryDeclaration {
                        data_type: DataType::Position,
                        fields: None,
                        excluded_fields: Some(vec!["driver_id".to_string()]),
                        granularity: Granularity::Anonymised,
                        retention: "P730D".to_string(),
                        purpose: "Fleet analytics and route optimisation".to_string(),
                        jurisdiction: vec![Jurisdiction::EU, Jurisdiction::UK],
                    },
                    CategoryDeclaration {
                        data_type: DataType::Speed,
                        fields: None,
                        excluded_fields: None,
                        granularity: Granularity::Aggregated,
                        retention: "P365D".to_string(),
                        purpose: "Vehicle performance analytics".to_string(),
                        jurisdiction: vec![Jurisdiction::EU, Jurisdiction::UK],
                    },
                    CategoryDeclaration {
                        data_type: DataType::DiagnosticCode,
                        fields: None,
                        excluded_fields: None,
                        granularity: Granularity::Raw,
                        retention: "P1095D".to_string(),
                        purpose: "Warranty and recall management".to_string(),
                        jurisdiction: vec![Jurisdiction::EU, Jurisdiction::UK],
                    },
                    CategoryDeclaration {
                        data_type: DataType::SensorHealth,
                        fields: None,
                        excluded_fields: None,
                        granularity: Granularity::Raw,
                        retention: "P365D".to_string(),
                        purpose: "Predictive maintenance".to_string(),
                        jurisdiction: vec![Jurisdiction::EU, Jurisdiction::UK],
                    },
                ],
            },
            StakeholderDeclaration {
                id: "insurer-api".to_string(),
                role: "Insurer".to_string(),
                legal_basis: LegalBasis::ExplicitConsent,
                endpoint_jurisdiction: Jurisdiction::EU,
                categories: vec![
                    CategoryDeclaration {
                        data_type: DataType::DrivingBehaviour,
                        fields: None,
                        excluded_fields: Some(vec![
                            "latitude".to_string(),
                            "longitude".to_string(),
                            "driver_id".to_string(),
                        ]),
                        granularity: Granularity::PerTripScore,
                        retention: "P90D".to_string(),
                        purpose: "Usage-based insurance scoring".to_string(),
                        jurisdiction: vec![Jurisdiction::EU],
                    },
                    CategoryDeclaration {
                        data_type: DataType::Speed,
                        fields: Some(vec![
                            "avg_speed".to_string(),
                            "max_speed".to_string(),
                        ]),
                        excluded_fields: None,
                        granularity: Granularity::Aggregated,
                        retention: "P90D".to_string(),
                        purpose: "Risk assessment".to_string(),
                        jurisdiction: vec![Jurisdiction::EU],
                    },
                ],
            },
            StakeholderDeclaration {
                id: "adas-supplier".to_string(),
                role: "ADAS Supplier".to_string(),
                legal_basis: LegalBasis::ContractualNecessity,
                endpoint_jurisdiction: Jurisdiction::EU,
                categories: vec![CategoryDeclaration {
                    data_type: DataType::AdasEvent,
                    fields: None,
                    excluded_fields: Some(vec!["driver_id".to_string(), "vin".to_string()]),
                    granularity: Granularity::Raw,
                    retention: "P180D".to_string(),
                    purpose: "ADAS algorithm improvement".to_string(),
                    jurisdiction: vec![Jurisdiction::EU, Jurisdiction::US],
                }],
            },
            StakeholderDeclaration {
                id: "city-transport".to_string(),
                role: "Smart City Authority".to_string(),
                legal_basis: LegalBasis::LegitimateInterest,
                endpoint_jurisdiction: Jurisdiction::EU,
                categories: vec![CategoryDeclaration {
                    data_type: DataType::V2xCam,
                    fields: None,
                    excluded_fields: Some(vec![
                        "vin".to_string(),
                        "driver_id".to_string(),
                        "license_plate".to_string(),
                    ]),
                    granularity: Granularity::Anonymised,
                    retention: "P30D".to_string(),
                    purpose: "Traffic flow optimisation".to_string(),
                    jurisdiction: vec![Jurisdiction::EU],
                }],
            },
        ],
        mandatory_retention: vec![MandatoryRetention {
            authority: "EU Type-Approval (UNECE R157)".to_string(),
            data_types: vec![DataType::AdasEvent, DataType::BrakeEvent],
            minimum_retention: "P1825D".to_string(),
            legal_reference: "UNECE R157 §5.4".to_string(),
        }],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::SessionId;
    use std::collections::HashMap;

    fn make_speed_record(session_id: SessionId) -> DataRecord {
        let mut fields = HashMap::new();
        fields.insert(
            "avg_speed".to_string(),
            serde_json::Value::Number(serde_json::Number::from_f64(65.7).unwrap()),
        );
        fields.insert(
            "max_speed".to_string(),
            serde_json::Value::Number(serde_json::Number::from_f64(120.3).unwrap()),
        );
        fields.insert(
            "driver_id".to_string(),
            serde_json::Value::String("DRV-001".to_string()),
        );
        DataRecord::new(session_id, "speed-sensor", DataType::Speed, fields)
    }

    #[test]
    fn oem_gets_aggregated_speed_without_driver_id() {
        let manifest = demo_manifest("VIN-TEST-001");
        let evaluator = ManifestEvaluator::new(manifest);
        let session = SessionId::new();
        let record = make_speed_record(session);

        let results = evaluator.evaluate(&record).unwrap();
        let oem_result = results.iter().find(|(id, _)| id.0 == "oem-cloud");
        assert!(oem_result.is_some());
        let (_, filtered) = oem_result.unwrap();
        assert_eq!(filtered.granularity, Granularity::Aggregated);
        // driver_id should be stripped by Aggregated granularity
        assert!(!filtered.fields.contains_key("driver_id"));
    }

    #[test]
    fn insurer_gets_only_declared_speed_fields() {
        let manifest = demo_manifest("VIN-TEST-001");
        let evaluator = ManifestEvaluator::new(manifest);
        let session = SessionId::new();
        let record = make_speed_record(session);

        let results = evaluator.evaluate(&record).unwrap();
        let insurer_result = results.iter().find(|(id, _)| id.0 == "insurer-api");
        assert!(insurer_result.is_some());
        let (_, filtered) = insurer_result.unwrap();
        // Insurer declared fields: avg_speed, max_speed only
        assert!(filtered.fields.contains_key("avg_speed"));
        assert!(filtered.fields.contains_key("max_speed"));
        assert!(!filtered.fields.contains_key("driver_id"));
    }

    #[test]
    fn revocation_blocks_stakeholder() {
        let manifest = demo_manifest("VIN-TEST-001");
        let evaluator = ManifestEvaluator::new(manifest);
        let session = SessionId::new();
        let record = make_speed_record(session);

        // Before revocation: insurer gets data
        let results = evaluator.evaluate(&record).unwrap();
        assert!(results.iter().any(|(id, _)| id.0 == "insurer-api"));

        // Revoke insurer
        evaluator
            .revoke_consent(&StakeholderId::new("insurer-api"))
            .unwrap();

        // After revocation: insurer gets nothing
        let results = evaluator.evaluate(&record).unwrap();
        assert!(!results.iter().any(|(id, _)| id.0 == "insurer-api"));
    }
}
