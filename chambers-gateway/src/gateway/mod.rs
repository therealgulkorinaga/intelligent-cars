use crate::audit::{AuditLog, EventType};
use crate::burn::{BurnEngine, BurnReceipt, SessionDataStore};
use crate::core::{ChambersError, DataCategory, DataRecord, Result, SessionId, StakeholderId};
use crate::hsm::{KeyHandle, SoftwareHsm};
use crate::manifest::{ManifestEvaluator, PreservationManifest};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

// ── Session state ──────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct SessionState {
    pub session_id: SessionId,
    pub vehicle_id: String,
    pub key_handle: KeyHandle,
    pub started_at: DateTime<Utc>,
    pub fallback_mode: bool,
    pub records_processed: usize,
}

// ── Processing result ──────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransmissionRecord {
    pub stakeholder_id: String,
    pub category: String,
    pub granularity: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlockRecord {
    pub category: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProcessingResult {
    pub session_id: SessionId,
    pub records_transmitted: Vec<TransmissionRecord>,
    pub records_blocked: Vec<BlockRecord>,
}

// ── Gateway metrics ────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GatewayMetrics {
    pub total_processed: usize,
    pub total_transmitted: usize,
    pub total_blocked: usize,
    pub total_burned: usize,
    pub active_sessions: usize,
}

// ── Gateway ────────────────────────────────────────────────────────────

/// The central orchestrator that wires together HSM, manifest evaluator,
/// burn engine, and audit log.
pub struct Gateway {
    active_sessions: Arc<Mutex<HashMap<SessionId, SessionState>>>,
    hsm: SoftwareHsm,
    evaluators: Arc<Mutex<HashMap<SessionId, ManifestEvaluator>>>,
    burn_engine: BurnEngine,
    audit_log: Arc<AuditLog>,
    data_store: Arc<Mutex<SessionDataStore>>,
    stakeholder_endpoints: HashMap<String, String>,
    metrics: Arc<Mutex<GatewayMetrics>>,
}

impl Gateway {
    pub fn new(audit_db_path: &str) -> Result<Self> {
        let hsm = SoftwareHsm::new();
        let data_store = Arc::new(Mutex::new(SessionDataStore::new()));
        let burn_engine = BurnEngine::new(hsm.clone(), data_store.clone());
        let audit_log = Arc::new(AuditLog::new(audit_db_path)?);

        // Mock stakeholder endpoints
        let mut endpoints = HashMap::new();
        endpoints.insert(
            "oem-cloud".to_string(),
            "https://oem.example.com/ingest".to_string(),
        );
        endpoints.insert(
            "insurer-api".to_string(),
            "https://insurer.example.com/telemetry".to_string(),
        );
        endpoints.insert(
            "adas-supplier".to_string(),
            "https://adas.example.com/events".to_string(),
        );
        endpoints.insert(
            "city-transport".to_string(),
            "https://city.example.com/v2x".to_string(),
        );

        Ok(Self {
            active_sessions: Arc::new(Mutex::new(HashMap::new())),
            hsm,
            evaluators: Arc::new(Mutex::new(HashMap::new())),
            burn_engine,
            audit_log,
            data_store,
            stakeholder_endpoints: endpoints,
            metrics: Arc::new(Mutex::new(GatewayMetrics::default())),
        })
    }

    /// Create with an externally-provided AuditLog (useful for testing).
    pub fn with_audit_log(audit_log: Arc<AuditLog>) -> Result<Self> {
        let hsm = SoftwareHsm::new();
        let data_store = Arc::new(Mutex::new(SessionDataStore::new()));
        let burn_engine = BurnEngine::new(hsm.clone(), data_store.clone());

        let mut endpoints = HashMap::new();
        endpoints.insert(
            "oem-cloud".to_string(),
            "https://oem.example.com/ingest".to_string(),
        );
        endpoints.insert(
            "insurer-api".to_string(),
            "https://insurer.example.com/telemetry".to_string(),
        );
        endpoints.insert(
            "adas-supplier".to_string(),
            "https://adas.example.com/events".to_string(),
        );
        endpoints.insert(
            "city-transport".to_string(),
            "https://city.example.com/v2x".to_string(),
        );

        Ok(Self {
            active_sessions: Arc::new(Mutex::new(HashMap::new())),
            hsm,
            evaluators: Arc::new(Mutex::new(HashMap::new())),
            burn_engine,
            audit_log,
            data_store,
            stakeholder_endpoints: endpoints,
            metrics: Arc::new(Mutex::new(GatewayMetrics::default())),
        })
    }

    /// Start a new session: generate a key via HSM, create session state,
    /// load manifest, log SessionStart.
    pub fn start_session(
        &self,
        vehicle_id: &str,
        manifest: PreservationManifest,
    ) -> Result<SessionId> {
        let session_id = SessionId::new();

        // Generate encryption key in HSM
        let (key_handle, _) = self.hsm.generate_key()?;

        // Register with burn engine
        self.burn_engine
            .register_session(&session_id, key_handle);

        // Create session state
        let state = SessionState {
            session_id: session_id.clone(),
            vehicle_id: vehicle_id.to_string(),
            key_handle,
            started_at: Utc::now(),
            fallback_mode: false,
            records_processed: 0,
        };

        // Store session
        {
            let mut sessions = self.active_sessions.lock().unwrap();
            sessions.insert(session_id.clone(), state);
        }

        // Create manifest evaluator for this session
        {
            let evaluator = ManifestEvaluator::new(manifest);
            let mut evaluators = self.evaluators.lock().unwrap();
            evaluators.insert(session_id.clone(), evaluator);
        }

        // Update metrics
        {
            let mut m = self.metrics.lock().unwrap();
            m.active_sessions += 1;
        }

        // Audit log
        self.audit_log.log_event(
            &session_id,
            EventType::SessionStart,
            &serde_json::json!({
                "vehicle_id": vehicle_id,
                "key_handle": key_handle,
            }),
        )?;

        self.audit_log.log_event(
            &session_id,
            EventType::ManifestLoaded,
            &serde_json::json!({
                "vehicle_id": vehicle_id,
            }),
        )?;

        Ok(session_id)
    }

    /// Process a data record: encrypt, evaluate against manifest,
    /// route to stakeholders, log decisions.
    pub fn process_record(
        &self,
        session_id: &SessionId,
        record: &DataRecord,
    ) -> Result<ProcessingResult> {
        // Get session state
        let (key_handle, fallback) = {
            let mut sessions = self.active_sessions.lock().unwrap();
            let state = sessions
                .get_mut(session_id)
                .ok_or_else(|| ChambersError::SessionNotFound(session_id.clone()))?;

            if state.fallback_mode {
                // In fallback mode, block ALL telemetry
                return Ok(ProcessingResult {
                    session_id: session_id.clone(),
                    records_transmitted: vec![],
                    records_blocked: vec![BlockRecord {
                        category: record.category().to_string(),
                        reason: "HSM fallback mode - all telemetry blocked".to_string(),
                    }],
                });
            }

            state.records_processed += 1;
            (state.key_handle, state.fallback_mode)
        };

        let _ = fallback; // already checked above

        // Encrypt the raw record and store it
        let serialized = serde_json::to_vec(record)?;
        let ciphertext = self.hsm.encrypt(key_handle, &serialized)?;

        // Store encrypted data
        {
            let mut store = self.data_store.lock().unwrap();
            store.store(session_id, ciphertext.data.clone());
        }

        // Log DataGenerated
        self.audit_log.log_event(
            session_id,
            EventType::DataGenerated,
            &serde_json::json!({
                "data_type": format!("{:?}", record.data_type),
                "source": record.source,
                "category": format!("{:?}", record.category()),
                "encrypted_size": ciphertext.data.len(),
            }),
        )?;

        // Evaluate against manifest
        let evaluator_results = {
            let evaluators = self.evaluators.lock().unwrap();
            let evaluator = evaluators
                .get(session_id)
                .ok_or_else(|| ChambersError::SessionNotFound(session_id.clone()))?;
            evaluator.evaluate(record)?
        };

        let mut transmitted = Vec::new();
        let mut blocked = Vec::new();

        let category = DataCategory::from_data_type(&record.data_type);

        if evaluator_results.is_empty() {
            // No stakeholder declared this data type — block
            blocked.push(BlockRecord {
                category: category.to_string(),
                reason: "No stakeholder declared this data type".to_string(),
            });

            self.audit_log.log_event(
                session_id,
                EventType::DataBlocked,
                &serde_json::json!({
                    "data_type": format!("{:?}", record.data_type),
                    "reason": "undeclared_data_type",
                }),
            )?;
        }

        for (stakeholder_id, filtered_record) in &evaluator_results {
            // Check if stakeholder has a known endpoint
            if !self
                .stakeholder_endpoints
                .contains_key(&stakeholder_id.0)
            {
                blocked.push(BlockRecord {
                    category: category.to_string(),
                    reason: format!(
                        "Unknown stakeholder endpoint: {}",
                        stakeholder_id
                    ),
                });

                self.audit_log.log_event(
                    session_id,
                    EventType::DataBlocked,
                    &serde_json::json!({
                        "stakeholder": stakeholder_id.0,
                        "reason": "unknown_endpoint",
                    }),
                )?;

                continue;
            }

            // "Transmit" to stakeholder (in simulation, just log it)
            transmitted.push(TransmissionRecord {
                stakeholder_id: stakeholder_id.0.clone(),
                category: category.to_string(),
                granularity: format!("{:?}", filtered_record.granularity),
            });

            self.audit_log.log_event(
                session_id,
                EventType::DataTransmitted,
                &serde_json::json!({
                    "stakeholder": stakeholder_id.0,
                    "data_type": format!("{:?}", record.data_type),
                    "granularity": format!("{:?}", filtered_record.granularity),
                    "fields_sent": filtered_record.fields.keys().collect::<Vec<_>>(),
                }),
            )?;
        }

        // Update metrics
        {
            let mut m = self.metrics.lock().unwrap();
            m.total_processed += 1;
            m.total_transmitted += transmitted.len();
            m.total_blocked += blocked.len();
        }

        Ok(ProcessingResult {
            session_id: session_id.clone(),
            records_transmitted: transmitted,
            records_blocked: blocked,
        })
    }

    /// End a session: trigger 6-layer burn, log SessionEnd + BurnCompleted.
    pub fn end_session(&self, session_id: &SessionId) -> Result<BurnReceipt> {
        let key_handle = {
            let sessions = self.active_sessions.lock().unwrap();
            let state = sessions
                .get(session_id)
                .ok_or_else(|| ChambersError::SessionNotFound(session_id.clone()))?;
            state.key_handle
        };

        // Log BurnStarted
        self.audit_log.log_event(
            session_id,
            EventType::BurnStarted,
            &serde_json::json!({
                "key_handle": key_handle,
            }),
        )?;

        // Execute 6-layer burn
        let receipt = self.burn_engine.burn_session(session_id, key_handle);

        // Log BurnCompleted
        self.audit_log.log_event(
            session_id,
            EventType::BurnCompleted,
            &serde_json::json!({
                "overall_success": receipt.overall_success,
                "layers": receipt.layers_completed.iter().map(|l| {
                    serde_json::json!({
                        "layer": l.layer_name,
                        "success": l.success,
                        "duration_ms": l.duration_ms,
                    })
                }).collect::<Vec<_>>(),
            }),
        )?;

        // Log SessionEnd
        self.audit_log.log_event(
            session_id,
            EventType::SessionEnd,
            &serde_json::json!({
                "burn_success": receipt.overall_success,
            }),
        )?;

        // Remove session
        {
            let mut sessions = self.active_sessions.lock().unwrap();
            sessions.remove(session_id);
        }

        // Remove evaluator
        {
            let mut evaluators = self.evaluators.lock().unwrap();
            evaluators.remove(session_id);
        }

        // Update metrics
        {
            let mut m = self.metrics.lock().unwrap();
            m.active_sessions = m.active_sessions.saturating_sub(1);
            m.total_burned += 1;
        }

        Ok(receipt)
    }

    /// Revoke consent for a stakeholder within a session.
    pub fn revoke_consent(
        &self,
        session_id: &SessionId,
        stakeholder_id: &StakeholderId,
    ) -> Result<()> {
        // Verify session exists
        {
            let sessions = self.active_sessions.lock().unwrap();
            if !sessions.contains_key(session_id) {
                return Err(ChambersError::SessionNotFound(session_id.clone()));
            }
        }

        // Revoke in manifest evaluator
        let revoked_at = {
            let evaluators = self.evaluators.lock().unwrap();
            let evaluator = evaluators
                .get(session_id)
                .ok_or_else(|| ChambersError::SessionNotFound(session_id.clone()))?;
            evaluator.revoke_consent(stakeholder_id)?
        };

        // Audit log
        self.audit_log.log_event(
            session_id,
            EventType::ConsentRevoked,
            &serde_json::json!({
                "stakeholder": stakeholder_id.0,
                "revoked_at": revoked_at.to_rfc3339(),
            }),
        )?;

        Ok(())
    }

    /// Enter fallback mode: HSM is unavailable, block ALL telemetry.
    pub fn enter_fallback_mode(&self, session_id: &SessionId) -> Result<()> {
        let mut sessions = self.active_sessions.lock().unwrap();
        let state = sessions
            .get_mut(session_id)
            .ok_or_else(|| ChambersError::SessionNotFound(session_id.clone()))?;

        state.fallback_mode = true;

        drop(sessions);

        self.audit_log.log_event(
            session_id,
            EventType::HsmFallback,
            &serde_json::json!({
                "message": "HSM unavailable, all telemetry blocked",
            }),
        )?;

        Ok(())
    }

    /// Get current gateway metrics.
    pub fn metrics(&self) -> GatewayMetrics {
        self.metrics.lock().unwrap().clone()
    }

    /// Get a reference to the audit log.
    pub fn audit_log(&self) -> &AuditLog {
        &self.audit_log
    }

    /// Get a reference to the HSM.
    pub fn hsm(&self) -> &SoftwareHsm {
        &self.hsm
    }

    /// Check if a session is active.
    pub fn is_session_active(&self, session_id: &SessionId) -> bool {
        let sessions = self.active_sessions.lock().unwrap();
        sessions.contains_key(session_id)
    }

    /// Get session state (clone).
    pub fn get_session_state(&self, session_id: &SessionId) -> Option<SessionState> {
        let sessions = self.active_sessions.lock().unwrap();
        sessions.get(session_id).cloned()
    }

    /// Attempt to decrypt session data (for testing that burn makes it impossible).
    pub fn try_decrypt_session_data(
        &self,
        key_handle: KeyHandle,
        ciphertext: &crate::hsm::CipherText,
    ) -> Result<Vec<u8>> {
        self.hsm.decrypt(key_handle, ciphertext)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::DataType;
    use crate::manifest::demo_manifest;
    use std::collections::HashMap;

    #[test]
    fn session_lifecycle() {
        let gw = Gateway::new(":memory:").unwrap();
        let manifest = demo_manifest("VIN-TEST-001");
        let sid = gw.start_session("VIN-TEST-001", manifest).unwrap();

        assert!(gw.is_session_active(&sid));

        // Process a speed record
        let mut fields = HashMap::new();
        fields.insert(
            "avg_speed".into(),
            serde_json::json!(72.5),
        );
        let record = DataRecord::new(sid.clone(), "speed-sensor", DataType::Speed, fields);
        let result = gw.process_record(&sid, &record).unwrap();
        assert!(!result.records_transmitted.is_empty());

        // End session (burn)
        let receipt = gw.end_session(&sid).unwrap();
        assert!(receipt.overall_success);
        assert!(!gw.is_session_active(&sid));
    }
}
