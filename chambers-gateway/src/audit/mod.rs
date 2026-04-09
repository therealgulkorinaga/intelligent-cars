use crate::core::{ChambersError, Result, SessionId};
use chrono::Utc;
use ring::hmac;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;

// ── Event types ────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EventType {
    SessionStart,
    SessionEnd,
    DataGenerated,
    DataTransmitted,
    DataBlocked,
    BurnStarted,
    BurnCompleted,
    ConsentRevoked,
    ManifestLoaded,
    JurisdictionBlocked,
    HsmFallback,
    PolicyViolation,
}

impl std::fmt::Display for EventType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            EventType::SessionStart => "SessionStart",
            EventType::SessionEnd => "SessionEnd",
            EventType::DataGenerated => "DataGenerated",
            EventType::DataTransmitted => "DataTransmitted",
            EventType::DataBlocked => "DataBlocked",
            EventType::BurnStarted => "BurnStarted",
            EventType::BurnCompleted => "BurnCompleted",
            EventType::ConsentRevoked => "ConsentRevoked",
            EventType::ManifestLoaded => "ManifestLoaded",
            EventType::JurisdictionBlocked => "JurisdictionBlocked",
            EventType::HsmFallback => "HsmFallback",
            EventType::PolicyViolation => "PolicyViolation",
        };
        write!(f, "{}", s)
    }
}

// ── Audit entry ────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub id: i64,
    pub session_id: String,
    pub timestamp: String,
    pub event_type: String,
    pub payload: String,
    pub hmac: String,
    pub previous_hmac: String,
}

// ── Session summary ────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionSummary {
    pub session_id: String,
    pub total_events: usize,
    pub data_generated: usize,
    pub data_transmitted: usize,
    pub data_blocked: usize,
    pub burns_completed: usize,
    pub consent_revocations: usize,
    pub jurisdiction_blocks: usize,
    pub policy_violations: usize,
    pub chain_intact: bool,
}

// ── AuditLog ───────────────────────────────────────────────────────────

/// Immutable HMAC-chained audit log backed by SQLite.
pub struct AuditLog {
    conn: Mutex<Connection>,
    hmac_key: hmac::Key,
}

impl AuditLog {
    /// Create a new audit log. If `db_path` is ":memory:", uses an in-memory
    /// database; otherwise creates/opens a file-backed SQLite DB.
    pub fn new(db_path: &str) -> Result<Self> {
        let conn = if db_path == ":memory:" {
            Connection::open_in_memory()?
        } else {
            Connection::open(Path::new(db_path))?
        };

        // Create table if not exists
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS audit_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     TEXT NOT NULL,
                timestamp      TEXT NOT NULL,
                event_type     TEXT NOT NULL,
                payload        TEXT NOT NULL,
                hmac           TEXT NOT NULL,
                previous_hmac  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
            CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_type);",
        )?;

        // Derive HMAC key from a fixed seed (in production this would come from
        // the HSM or a secure key store). For the simulation we use a
        // deterministic key so chain verification works across restarts.
        let key_material = b"chambers-audit-hmac-key-v1-2026";
        let hmac_key = hmac::Key::new(hmac::HMAC_SHA256, key_material);

        Ok(Self {
            conn: Mutex::new(conn),
            hmac_key,
        })
    }

    /// Log an event. The HMAC chain links each entry to its predecessor
    /// within the same session.
    pub fn log_event(
        &self,
        session_id: &SessionId,
        event_type: EventType,
        payload: &serde_json::Value,
    ) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

        let session_str = session_id.to_string();
        let timestamp = Utc::now().to_rfc3339();
        let event_str = event_type.to_string();
        let payload_str = serde_json::to_string(payload)?;

        // Get previous HMAC for this session (chain linking)
        let previous_hmac: String = conn
            .query_row(
                "SELECT hmac FROM audit_log WHERE session_id = ?1 ORDER BY id DESC LIMIT 1",
                params![session_str],
                |row| row.get(0),
            )
            .unwrap_or_else(|_| "genesis".to_string());

        // Compute HMAC = HMAC-SHA256(key, previous_hmac + payload)
        let hmac_input = format!("{}{}", previous_hmac, payload_str);
        let tag = hmac::sign(&self.hmac_key, hmac_input.as_bytes());
        let hmac_hex = hex::encode(tag.as_ref());

        conn.execute(
            "INSERT INTO audit_log (session_id, timestamp, event_type, payload, hmac, previous_hmac)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                session_str,
                timestamp,
                event_str,
                payload_str,
                hmac_hex,
                previous_hmac
            ],
        )?;

        Ok(())
    }

    /// Verify the HMAC chain integrity for a session.
    /// Returns true if every entry's HMAC matches its recomputed value.
    pub fn verify_chain(&self, session_id: &SessionId) -> Result<bool> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

        let session_str = session_id.to_string();

        let mut stmt = conn.prepare(
            "SELECT id, payload, hmac, previous_hmac FROM audit_log
             WHERE session_id = ?1 ORDER BY id ASC",
        )?;

        let entries: Vec<(i64, String, String, String)> = stmt
            .query_map(params![session_str], |row| {
                Ok((
                    row.get::<_, i64>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })?
            .filter_map(|r| r.ok())
            .collect();

        if entries.is_empty() {
            return Ok(true); // no entries = trivially valid
        }

        let mut expected_previous = "genesis".to_string();

        for (_id, payload, stored_hmac, previous_hmac) in &entries {
            // Verify previous_hmac chain link
            if *previous_hmac != expected_previous {
                return Ok(false);
            }

            // Recompute HMAC
            let hmac_input = format!("{}{}", previous_hmac, payload);
            let tag = hmac::sign(&self.hmac_key, hmac_input.as_bytes());
            let computed = hex::encode(tag.as_ref());

            if computed != *stored_hmac {
                return Ok(false);
            }

            expected_previous = stored_hmac.clone();
        }

        Ok(true)
    }

    /// Get a summary of all events for a session.
    pub fn get_session_summary(&self, session_id: &SessionId) -> Result<SessionSummary> {
        let counts = {
            let conn = self
                .conn
                .lock()
                .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

            let session_str = session_id.to_string();

            let mut stmt = conn.prepare(
                "SELECT event_type, COUNT(*) FROM audit_log
                 WHERE session_id = ?1 GROUP BY event_type",
            )?;

            let counts: Vec<(String, usize)> = stmt
                .query_map(params![session_str], |row| {
                    Ok((row.get::<_, String>(0)?, row.get::<_, usize>(1)?))
                })?
                .filter_map(|r| r.ok())
                .collect();

            counts
        }; // conn and stmt dropped here

        let mut summary = SessionSummary {
            session_id: session_id.to_string(),
            total_events: 0,
            data_generated: 0,
            data_transmitted: 0,
            data_blocked: 0,
            burns_completed: 0,
            consent_revocations: 0,
            jurisdiction_blocks: 0,
            policy_violations: 0,
            chain_intact: false,
        };

        for (event_type, count) in &counts {
            summary.total_events += count;
            match event_type.as_str() {
                "DataGenerated" => summary.data_generated = *count,
                "DataTransmitted" => summary.data_transmitted = *count,
                "DataBlocked" => summary.data_blocked = *count,
                "BurnCompleted" => summary.burns_completed = *count,
                "ConsentRevoked" => summary.consent_revocations = *count,
                "JurisdictionBlocked" => summary.jurisdiction_blocks = *count,
                "PolicyViolation" => summary.policy_violations = *count,
                _ => {}
            }
        }

        // Also verify chain integrity
        summary.chain_intact = self.verify_chain(session_id)?;

        Ok(summary)
    }

    /// Export all audit entries for a session as JSON (for regulators).
    pub fn export_session(&self, session_id: &SessionId) -> Result<String> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

        let session_str = session_id.to_string();

        let mut stmt = conn.prepare(
            "SELECT id, session_id, timestamp, event_type, payload, hmac, previous_hmac
             FROM audit_log WHERE session_id = ?1 ORDER BY id ASC",
        )?;

        let entries: Vec<AuditEntry> = stmt
            .query_map(params![session_str], |row| {
                Ok(AuditEntry {
                    id: row.get(0)?,
                    session_id: row.get(1)?,
                    timestamp: row.get(2)?,
                    event_type: row.get(3)?,
                    payload: row.get(4)?,
                    hmac: row.get(5)?,
                    previous_hmac: row.get(6)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        let export = serde_json::json!({
            "session_id": session_str,
            "export_timestamp": Utc::now().to_rfc3339(),
            "entry_count": entries.len(),
            "chain_verified": self.verify_chain_internal(&entries),
            "entries": entries,
        });

        Ok(serde_json::to_string_pretty(&export)?)
    }

    /// Human-readable driver summary with no jargon.
    pub fn get_driver_summary(&self, session_id: &SessionId) -> Result<String> {
        let summary = self.get_session_summary(session_id)?;

        let mut lines = Vec::new();
        lines.push("Your Driving Session Summary".to_string());
        lines.push("============================".to_string());
        lines.push(format!("Session: {}", summary.session_id));
        lines.push(String::new());

        if summary.total_events == 0 {
            lines.push("No activity recorded for this session.".to_string());
            return Ok(lines.join("\n"));
        }

        lines.push(format!(
            "During this trip, your vehicle collected {} data points.",
            summary.data_generated
        ));

        if summary.data_transmitted > 0 {
            lines.push(format!(
                "{} data points were shared with authorised partners (as you agreed).",
                summary.data_transmitted
            ));
        }

        if summary.data_blocked > 0 {
            lines.push(format!(
                "{} data requests were blocked because they were not covered by your agreements.",
                summary.data_blocked
            ));
        }

        if summary.jurisdiction_blocks > 0 {
            lines.push(format!(
                "{} data requests were blocked because the destination was in a region not covered by your data sharing agreement.",
                summary.jurisdiction_blocks
            ));
        }

        if summary.consent_revocations > 0 {
            lines.push(format!(
                "You withdrew permission from {} partner(s) during this session. Their data access was stopped immediately.",
                summary.consent_revocations
            ));
        }

        if summary.burns_completed > 0 {
            lines.push(String::new());
            lines.push(
                "When your trip ended, all raw data was permanently and irrecoverably destroyed."
                    .to_string(),
            );
            lines.push(
                "This means nobody — not even the vehicle manufacturer — can access the original data."
                    .to_string(),
            );
        }

        if summary.chain_intact {
            lines.push(String::new());
            lines.push(
                "The integrity of your data log has been verified. No records have been altered."
                    .to_string(),
            );
        }

        lines.push(String::new());
        lines.push(format!("Total events logged: {}", summary.total_events));

        Ok(lines.join("\n"))
    }

    /// Get all entries for a session (used internally and in tests).
    pub fn get_entries(&self, session_id: &SessionId) -> Result<Vec<AuditEntry>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

        let session_str = session_id.to_string();

        let mut stmt = conn.prepare(
            "SELECT id, session_id, timestamp, event_type, payload, hmac, previous_hmac
             FROM audit_log WHERE session_id = ?1 ORDER BY id ASC",
        )?;

        let entries: Vec<AuditEntry> = stmt
            .query_map(params![session_str], |row| {
                Ok(AuditEntry {
                    id: row.get(0)?,
                    session_id: row.get(1)?,
                    timestamp: row.get(2)?,
                    event_type: row.get(3)?,
                    payload: row.get(4)?,
                    hmac: row.get(5)?,
                    previous_hmac: row.get(6)?,
                })
            })?
            .filter_map(|r| r.ok())
            .collect();

        Ok(entries)
    }

    /// Tamper with an entry (for testing only!). Modifies the payload of an
    /// entry without updating the HMAC, breaking the chain.
    /// This method exists solely for testing chain integrity verification.
    pub fn tamper_entry(&self, entry_id: i64, new_payload: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| ChambersError::Audit(format!("Lock error: {}", e)))?;

        conn.execute(
            "UPDATE audit_log SET payload = ?1 WHERE id = ?2",
            params![new_payload, entry_id],
        )?;

        Ok(())
    }

    // ── Internal helpers ───────────────────────────────────────────────

    fn verify_chain_internal(&self, entries: &[AuditEntry]) -> bool {
        if entries.is_empty() {
            return true;
        }

        let mut expected_previous = "genesis".to_string();

        for entry in entries {
            if entry.previous_hmac != expected_previous {
                return false;
            }

            let hmac_input = format!("{}{}", entry.previous_hmac, entry.payload);
            let tag = hmac::sign(&self.hmac_key, hmac_input.as_bytes());
            let computed = hex::encode(tag.as_ref());

            if computed != entry.hmac {
                return false;
            }

            expected_previous = entry.hmac.clone();
        }

        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn log_and_verify_chain() {
        let log = AuditLog::new(":memory:").unwrap();
        let sid = SessionId::new();

        log.log_event(&sid, EventType::SessionStart, &json!({"vehicle": "V1"}))
            .unwrap();
        log.log_event(&sid, EventType::DataGenerated, &json!({"type": "speed"}))
            .unwrap();
        log.log_event(&sid, EventType::SessionEnd, &json!({}))
            .unwrap();

        assert!(log.verify_chain(&sid).unwrap());
    }

    #[test]
    fn detect_tampering() {
        let log = AuditLog::new(":memory:").unwrap();
        let sid = SessionId::new();

        log.log_event(&sid, EventType::SessionStart, &json!({"vehicle": "V1"}))
            .unwrap();
        log.log_event(&sid, EventType::DataGenerated, &json!({"type": "speed"}))
            .unwrap();

        // Tamper with the first entry
        let entries = log.get_entries(&sid).unwrap();
        log.tamper_entry(entries[0].id, r#"{"vehicle":"HACKED"}"#)
            .unwrap();

        assert!(!log.verify_chain(&sid).unwrap());
    }

    #[test]
    fn session_summary_counts() {
        let log = AuditLog::new(":memory:").unwrap();
        let sid = SessionId::new();

        log.log_event(&sid, EventType::SessionStart, &json!({}))
            .unwrap();
        log.log_event(&sid, EventType::DataGenerated, &json!({}))
            .unwrap();
        log.log_event(&sid, EventType::DataGenerated, &json!({}))
            .unwrap();
        log.log_event(&sid, EventType::DataTransmitted, &json!({}))
            .unwrap();
        log.log_event(&sid, EventType::DataBlocked, &json!({}))
            .unwrap();

        let summary = log.get_session_summary(&sid).unwrap();
        assert_eq!(summary.data_generated, 2);
        assert_eq!(summary.data_transmitted, 1);
        assert_eq!(summary.data_blocked, 1);
        assert_eq!(summary.total_events, 5);
    }
}
