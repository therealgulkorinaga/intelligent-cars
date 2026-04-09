use crate::core::SessionId;
use crate::hsm::{DestructionReceipt, KeyHandle, SoftwareHsm};
use chrono::{DateTime, Utc};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Instant;

/// Result of executing a single burn layer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LayerResult {
    pub layer_name: String,
    pub layer_number: u8,
    pub success: bool,
    pub duration_ms: u64,
    pub details: String,
}

/// Overall receipt produced after a 6-layer burn.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BurnReceipt {
    pub session_id: SessionId,
    pub timestamp: DateTime<Utc>,
    pub layers_completed: Vec<LayerResult>,
    pub key_handle: KeyHandle,
    pub destruction_receipt: Option<DestructionReceipt>,
    pub overall_success: bool,
}

/// In-memory representation of encrypted data blobs stored per session.
#[derive(Debug)]
pub struct SessionDataStore {
    /// Mapping from session to list of encrypted data blobs and a deletion flag.
    entries: HashMap<SessionId, Vec<DataEntry>>,
}

#[derive(Debug)]
struct DataEntry {
    encrypted_data: Vec<u8>,
    deleted: bool,
}

impl SessionDataStore {
    pub fn new() -> Self {
        Self {
            entries: HashMap::new(),
        }
    }

    pub fn store(&mut self, session_id: &SessionId, encrypted_data: Vec<u8>) {
        self.entries
            .entry(session_id.clone())
            .or_default()
            .push(DataEntry {
                encrypted_data,
                deleted: false,
            });
    }

    pub fn mark_deleted(&mut self, session_id: &SessionId) -> usize {
        let mut count = 0;
        if let Some(entries) = self.entries.get_mut(session_id) {
            for entry in entries.iter_mut() {
                if !entry.deleted {
                    entry.deleted = true;
                    count += 1;
                }
            }
        }
        count
    }

    pub fn overwrite_with_random(&mut self, session_id: &SessionId) -> usize {
        let mut rng = rand::thread_rng();
        let mut count = 0;
        if let Some(entries) = self.entries.get_mut(session_id) {
            for entry in entries.iter_mut() {
                rng.fill_bytes(&mut entry.encrypted_data);
                count += 1;
            }
        }
        count
    }

    pub fn zero_memory(&mut self, session_id: &SessionId) -> usize {
        let mut count = 0;
        if let Some(entries) = self.entries.get_mut(session_id) {
            for entry in entries.iter_mut() {
                for byte in entry.encrypted_data.iter_mut() {
                    *byte = 0;
                }
                count += 1;
            }
        }
        count
    }

    pub fn remove_session(&mut self, session_id: &SessionId) -> bool {
        self.entries.remove(session_id).is_some()
    }

    pub fn entry_count(&self, session_id: &SessionId) -> usize {
        self.entries.get(session_id).map_or(0, |e| e.len())
    }

    pub fn has_session(&self, session_id: &SessionId) -> bool {
        self.entries.contains_key(session_id)
    }
}

impl Default for SessionDataStore {
    fn default() -> Self {
        Self::new()
    }
}

/// The 6-layer burn engine. Each burn executes all layers in sequence to
/// achieve irrecoverable data destruction.
pub struct BurnEngine {
    hsm: SoftwareHsm,
    data_store: Arc<Mutex<SessionDataStore>>,
    /// Session-to-key mapping (destroyed in layer 5 - Semantic)
    session_keys: Arc<Mutex<HashMap<SessionId, KeyHandle>>>,
}

impl BurnEngine {
    pub fn new(hsm: SoftwareHsm, data_store: Arc<Mutex<SessionDataStore>>) -> Self {
        Self {
            hsm,
            data_store,
            session_keys: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Register a session-to-key mapping so the burn engine knows which key
    /// belongs to which session.
    pub fn register_session(&self, session_id: &SessionId, key_handle: KeyHandle) {
        let mut keys = self.session_keys.lock().unwrap();
        keys.insert(session_id.clone(), key_handle);
    }

    /// Execute the full 6-layer burn protocol.
    pub fn burn_session(&self, session_id: &SessionId, key_handle: KeyHandle) -> BurnReceipt {
        let mut layers = Vec::with_capacity(6);

        // Layer 1: Logical deletion
        let l1 = self.layer_1_logical(session_id);
        layers.push(l1);

        // Layer 2: Cryptographic destruction
        let (l2, destruction_receipt) = self.layer_2_cryptographic(key_handle);
        layers.push(l2);

        // Layer 3: Storage overwrite
        let l3 = self.layer_3_storage(session_id);
        layers.push(l3);

        // Layer 4: Memory zeroing
        let l4 = self.layer_4_memory(session_id);
        layers.push(l4);

        // Layer 5: Semantic destruction
        let l5 = self.layer_5_semantic(session_id);
        layers.push(l5);

        // Layer 6: Verification
        let l6 = self.layer_6_verification(session_id, key_handle, &layers);
        layers.push(l6);

        let overall_success = layers.iter().all(|l| l.success);

        BurnReceipt {
            session_id: session_id.clone(),
            timestamp: Utc::now(),
            layers_completed: layers,
            key_handle,
            destruction_receipt,
            overall_success,
        }
    }

    /// Layer 1: Mark all session data as logically deleted.
    fn layer_1_logical(&self, session_id: &SessionId) -> LayerResult {
        let start = Instant::now();
        let mut store = self.data_store.lock().unwrap();
        let count = store.mark_deleted(session_id);
        let duration = start.elapsed().as_millis() as u64;

        LayerResult {
            layer_name: "Logical Deletion".to_string(),
            layer_number: 1,
            success: true,
            duration_ms: duration,
            details: format!("Marked {} data entries as deleted", count),
        }
    }

    /// Layer 2: Destroy the encryption key via HSM.
    fn layer_2_cryptographic(
        &self,
        key_handle: KeyHandle,
    ) -> (LayerResult, Option<DestructionReceipt>) {
        let start = Instant::now();
        match self.hsm.destroy_key(key_handle) {
            Ok(receipt) => {
                let duration = start.elapsed().as_millis() as u64;
                (
                    LayerResult {
                        layer_name: "Cryptographic Destruction".to_string(),
                        layer_number: 2,
                        success: true,
                        duration_ms: duration,
                        details: format!(
                            "Key handle {} destroyed at {}",
                            key_handle, receipt.destroyed_at
                        ),
                    },
                    Some(receipt),
                )
            }
            Err(e) => {
                let duration = start.elapsed().as_millis() as u64;
                (
                    LayerResult {
                        layer_name: "Cryptographic Destruction".to_string(),
                        layer_number: 2,
                        success: false,
                        duration_ms: duration,
                        details: format!("Failed to destroy key {}: {}", key_handle, e),
                    },
                    None,
                )
            }
        }
    }

    /// Layer 3: Overwrite encrypted data regions with random bytes.
    fn layer_3_storage(&self, session_id: &SessionId) -> LayerResult {
        let start = Instant::now();
        let mut store = self.data_store.lock().unwrap();
        let count = store.overwrite_with_random(session_id);
        let duration = start.elapsed().as_millis() as u64;

        LayerResult {
            layer_name: "Storage Overwrite".to_string(),
            layer_number: 3,
            success: true,
            duration_ms: duration,
            details: format!("Overwrote {} data entries with random bytes", count),
        }
    }

    /// Layer 4: Zero any in-memory buffers associated with the session.
    fn layer_4_memory(&self, session_id: &SessionId) -> LayerResult {
        let start = Instant::now();
        let mut store = self.data_store.lock().unwrap();
        let count = store.zero_memory(session_id);
        let duration = start.elapsed().as_millis() as u64;

        LayerResult {
            layer_name: "Memory Zeroing".to_string(),
            layer_number: 4,
            success: true,
            duration_ms: duration,
            details: format!("Zeroed {} in-memory buffers", count),
        }
    }

    /// Layer 5: Destroy session-to-key and session-to-data mappings.
    fn layer_5_semantic(&self, session_id: &SessionId) -> LayerResult {
        let start = Instant::now();

        // Remove session-key mapping
        let key_removed = {
            let mut keys = self.session_keys.lock().unwrap();
            keys.remove(session_id).is_some()
        };

        // Remove session data entries entirely
        let data_removed = {
            let mut store = self.data_store.lock().unwrap();
            store.remove_session(session_id)
        };

        let duration = start.elapsed().as_millis() as u64;

        LayerResult {
            layer_name: "Semantic Destruction".to_string(),
            layer_number: 5,
            success: true,
            duration_ms: duration,
            details: format!(
                "Key mapping removed: {}, data mapping removed: {}",
                key_removed, data_removed
            ),
        }
    }

    /// Layer 6: Verify each previous layer completed successfully.
    fn layer_6_verification(
        &self,
        session_id: &SessionId,
        key_handle: KeyHandle,
        previous_layers: &[LayerResult],
    ) -> LayerResult {
        let start = Instant::now();
        let mut checks = Vec::new();
        let mut all_passed = true;

        // Check layers 1-5 reported success
        for layer in previous_layers {
            if !layer.success {
                checks.push(format!(
                    "FAIL: Layer {} did not succeed",
                    layer.layer_number
                ));
                all_passed = false;
            } else {
                checks.push(format!("OK: Layer {} succeeded", layer.layer_number));
            }
        }

        // Verify key is actually destroyed
        match self.hsm.is_key_active(key_handle) {
            Ok(false) => checks.push("OK: Key confirmed destroyed in HSM".to_string()),
            Ok(true) => {
                checks.push("FAIL: Key still active in HSM!".to_string());
                all_passed = false;
            }
            Err(e) => {
                checks.push(format!("WARN: Could not verify key status: {}", e));
            }
        }

        // Verify data store no longer has session entries
        {
            let store = self.data_store.lock().unwrap();
            if store.has_session(session_id) {
                checks.push("FAIL: Session data still present in store".to_string());
                all_passed = false;
            } else {
                checks.push("OK: Session data removed from store".to_string());
            }
        }

        // Verify session-key mapping removed
        {
            let keys = self.session_keys.lock().unwrap();
            if keys.contains_key(session_id) {
                checks.push("FAIL: Session-key mapping still exists".to_string());
                all_passed = false;
            } else {
                checks.push("OK: Session-key mapping removed".to_string());
            }
        }

        let duration = start.elapsed().as_millis() as u64;

        LayerResult {
            layer_name: "Verification".to_string(),
            layer_number: 6,
            success: all_passed,
            duration_ms: duration,
            details: checks.join("; "),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn full_burn_succeeds() {
        let hsm = SoftwareHsm::new();
        let (handle, _) = hsm.generate_key().unwrap();
        let store = Arc::new(Mutex::new(SessionDataStore::new()));
        let engine = BurnEngine::new(hsm.clone(), store.clone());

        let sid = SessionId::new();
        engine.register_session(&sid, handle);

        // Store some data
        {
            let ct = hsm.encrypt(handle, b"secret telemetry").unwrap();
            let mut s = store.lock().unwrap();
            s.store(&sid, ct.data);
        }

        let receipt = engine.burn_session(&sid, handle);
        assert!(receipt.overall_success);
        assert_eq!(receipt.layers_completed.len(), 6);
        assert!(receipt.destruction_receipt.is_some());

        // Verify key is destroyed
        assert!(hsm.encrypt(handle, b"test").is_err());
    }
}
